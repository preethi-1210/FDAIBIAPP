import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import patch

from app.auth import AuthUser
from app.main import _database_health, _dashboard_cache, dashboard, health, history, list_domains, me, startup
from app.schemas import QueryResponse


class DemoAccountVisibilityTests(unittest.TestCase):
    def test_database_health_checks_connection_and_reports_latency(self) -> None:
        connection = Mock()
        manager = Mock()
        manager.__enter__ = Mock(return_value=connection)
        manager.__exit__ = Mock(return_value=None)
        with patch("app.main.engine.connect", return_value=manager):
            result = _database_health()
        self.assertEqual(result["status"], "ok")
        connection.execute.assert_called_once()

    def test_health_reports_cache_outage_without_failing_application_health(self) -> None:
        with (
            patch("app.main._database_health", return_value={"status": "ok", "latency_ms": 1.2}),
            patch("app.main.cache_health", return_value={"status": "unavailable", "latency_ms": 2.4}),
            patch("app.main.warehouse_status", return_value={}),
            patch("app.main.provider_status", return_value={}),
            patch("app.main.orchestrator.tabpfn.package_status", return_value={}),
            patch("app.main.orchestrator.graph.rag.status", return_value={}),
        ):
            result = health()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["database"]["status"], "ok")
        self.assertEqual(result["cache"]["status"], "unavailable")

    def test_startup_does_not_require_cache_availability(self) -> None:
        database = Mock()
        with (
            patch("app.main.settings.demo_mode", True),
            patch("app.main.ensure_schema"),
            patch("app.main.SessionLocal", return_value=database),
            patch("app.main.seed_if_empty"),
            patch("app.main.apply_postgres_routines"),
            patch("app.main.orchestrator.warmup"),
        ):
            startup()
        database.close.assert_called_once_with()

    def test_demo_accounts_are_not_advertised_outside_demo_mode(self) -> None:
        with patch("app.main.settings.demo_mode", False):
            response = list_domains()
        self.assertEqual(response["users"], [])
        self.assertTrue(response["domains"])

    def test_demo_accounts_remain_available_in_demo_mode(self) -> None:
        with patch("app.main.settings.demo_mode", True):
            response = list_domains()
        self.assertTrue(any(user["username"] == "ins.user" for user in response["users"]))

    def test_me_returns_configuration_without_running_remote_connection_check(self) -> None:
        user = AuthUser(username="analyst", domain="banking", role="analyst")
        with (
            patch("app.main.warehouse_status", return_value={"mode": "databricks", "ready": True}),
            patch("app.main.test_warehouse_connection", side_effect=AssertionError("should be on-demand")),
            patch("app.main.provider_status", return_value={}),
            patch("app.main.orchestrator.tabpfn.package_status", return_value={}),
            patch("app.main.orchestrator.graph.rag.status", return_value={}),
            patch("app.main.domains_for", return_value=["banking"]),
            patch("app.main.notification_count", return_value=0),
        ):
            response = me(user)

        self.assertEqual(response["warehouse"]["mode"], "databricks")
        self.assertEqual(response["data_mode"], "live")

    def test_history_includes_audit_and_guardrail_metadata(self) -> None:
        result = QueryResponse(
            answer="Returned one row.",
            sql="SELECT COUNT(*) AS total LIMIT 10",
            table=[{"total": 1}],
            audit_id="AU-1234",
            guardrails={
                "ok": True,
                "error": None,
                "checks": [{"name": "SELECT only", "pass": True}],
            },
        )
        row = SimpleNamespace(
            id=7,
            audit_id="AU-1234",
            question="Count records",
            answer=result.answer,
            sql=result.sql,
            tables_used="accounts",
            row_count=1,
            guardrail_ok=1,
            result_json=result.model_dump_json(),
            created_at="2026-10-14T10:00:00",
        )
        query = Mock()
        query.filter.return_value = query
        query.order_by.return_value = query
        query.limit.return_value.all.return_value = [row]
        database = Mock()
        database.query.return_value = query

        entries = history(database, AuthUser("bank.user", "banking", "analyst"))

        self.assertEqual(entries[0]["audit_id"], "AU-1234")
        self.assertEqual(entries[0]["tables_used"], ["accounts"])
        self.assertEqual(entries[0]["row_count"], 1)
        self.assertTrue(entries[0]["guardrail_ok"])

    def test_remote_dashboard_metrics_are_cached_per_user_and_domain(self) -> None:
        _dashboard_cache.clear()
        first_user = AuthUser(username="analyst", domain="banking", role="analyst")
        another_user = AuthUser(username="other", domain="banking", role="analyst")
        try:
            with (
                patch("app.main.settings.warehouse", "databricks"),
                patch("app.main.settings.dashboard_cache_seconds", 30),
                patch(
                    "app.main.orchestrator.dashboard_metrics",
                    side_effect=lambda *_args, **_kwargs: {"volume": 123},
                ) as metrics,
            ):
                first = dashboard(Mock(), first_user)
                repeated = dashboard(Mock(), first_user)
                separate_user = dashboard(Mock(), another_user)
            self.assertEqual(first, repeated)
            self.assertNotEqual(first["user"], separate_user["user"])
            self.assertEqual(separate_user["user"], "other")
            self.assertEqual(metrics.call_count, 2)
        finally:
            _dashboard_cache.clear()

    def test_healthcare_dashboard_is_not_cached_and_uses_the_authorized_tenant(self) -> None:
        healthcare_user = AuthUser(
            username="health.user",
            domain="healthcare",
            role="analyst",
            tenant_id="tenant-a",
        )
        with (
            patch("app.main.settings.warehouse", "databricks"),
            patch("app.main.settings.dashboard_cache_seconds", 30),
            patch("app.main.cache_get_json") as cache_get,
            patch("app.main.cache_set_json") as cache_set,
            patch(
                "app.main.orchestrator.dashboard_metrics",
                return_value={"domain": "healthcare"},
            ) as metrics,
        ):
            dashboard(Mock(), healthcare_user)

        cache_get.assert_not_called()
        cache_set.assert_not_called()
        self.assertEqual(metrics.call_args.kwargs["tenant_id"], "tenant-a")


if __name__ == "__main__":
    unittest.main()
