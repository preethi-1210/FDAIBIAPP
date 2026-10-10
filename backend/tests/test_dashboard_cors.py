import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.auth import AuthUser, get_current_user
from app.db import get_db
from app.main import app, fastapi_app
from app.services.guardrail import GuardrailError


class DashboardCorsTests(unittest.TestCase):
    def setUp(self) -> None:
        fastapi_app.dependency_overrides[get_current_user] = lambda: AuthUser(
            username="analyst", domain="banking", role="analyst"
        )
        fastapi_app.dependency_overrides[get_db] = lambda: object()
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self) -> None:
        fastapi_app.dependency_overrides.clear()

    def test_unconfigured_warehouse_returns_clear_503_with_cors(self) -> None:
        with patch("app.main.settings.warehouse", "databricks"), patch(
            "app.main.orchestrator.dashboard_metrics",
            side_effect=GuardrailError("Databricks warehouse is not configured yet"),
        ), patch(
            "app.main.warehouse_status",
            return_value={
                "mode": "databricks",
                "ready": False,
                "connected": False,
                "detail": (
                    "Set DATABRICKS_SERVER_HOSTNAME, DATABRICKS_TOKEN, and "
                    "DATABRICKS_HTTP_PATH or DATABRICKS_WAREHOUSE_ID"
                ),
            },
        ):
            response = self.client.get(
                "/dashboard", headers={"Origin": "http://localhost:3001"}
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.headers["access-control-allow-origin"],
            "http://localhost:3001",
        )
        self.assertIn("WAREHOUSE=local", response.json()["detail"])
        self.assertIn("DATABRICKS_SERVER_HOSTNAME", response.json()["detail"])
        self.assertIn("backend/.env", response.json()["detail"])
        self.assertIn("will not substitute sample data", response.json()["detail"])

    def test_configured_but_unavailable_warehouse_does_not_suggest_sample_fallback(self) -> None:
        with patch("app.main.settings.warehouse", "databricks"), patch(
            "app.main.orchestrator.dashboard_metrics",
            side_effect=GuardrailError("Databricks query timed out"),
        ), patch(
            "app.main.warehouse_status",
            return_value={"mode": "databricks", "ready": True, "connected": False},
        ):
            response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 503)
        self.assertIn("SELECT permissions", response.json()["detail"])
        self.assertIn("WAREHOUSE=local only if you intentionally", response.json()["detail"])

    def test_unexpected_server_error_also_includes_cors(self) -> None:
        with patch(
            "app.main.orchestrator.dashboard_metrics",
            side_effect=RuntimeError("unexpected test failure"),
        ):
            response = self.client.get(
                "/dashboard", headers={"Origin": "http://localhost:3001"}
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.headers["access-control-allow-origin"],
            "http://localhost:3001",
        )

    def test_preflight_allows_dashboard_authorization_headers(self) -> None:
        response = self.client.options(
            "/dashboard",
            headers={
                "Origin": "http://localhost:3001",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["access-control-allow-origin"],
            "http://localhost:3001",
        )
        self.assertIn("authorization", response.headers["access-control-allow-headers"])


if __name__ == "__main__":
    unittest.main()
