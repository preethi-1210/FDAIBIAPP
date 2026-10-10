import unittest
from unittest.mock import Mock
from unittest.mock import patch

from app.auth import AuthUser
from app.main import _dashboard_cache, dashboard, list_domains, me


class DemoAccountVisibilityTests(unittest.TestCase):
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
        ):
            response = me(user)

        self.assertEqual(response["warehouse"]["mode"], "databricks")
        self.assertEqual(response["data_mode"], "live")

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


if __name__ == "__main__":
    unittest.main()
