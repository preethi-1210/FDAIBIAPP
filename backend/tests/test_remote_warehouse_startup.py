import unittest
from unittest.mock import Mock, patch

from app.main import startup
from app.services.guardrail import GuardrailError


class RemoteWarehouseStartupTests(unittest.TestCase):
    def test_remote_schema_failure_does_not_block_authentication_api_startup(self) -> None:
        database = Mock()
        with (
            patch("app.main.settings.demo_mode", True),
            patch("app.main.settings.warehouse", "databricks"),
            patch("app.main.ensure_schema"),
            patch("app.main.SessionLocal", return_value=database),
            patch("app.main.seed_if_empty"),
            patch("app.main.apply_postgres_routines"),
            patch(
                "app.main.orchestrator.warmup",
                side_effect=GuardrailError(
                    "Catalog 'main' was not found in the configured Databricks workspace"
                ),
            ),
            patch("app.main.logger.warning") as warning,
        ):
            startup()

        database.close.assert_called_once()
        warning.assert_called_once()
        self.assertIn("authentication remains available", warning.call_args.args[0])
        self.assertIn("Catalog 'main' was not found", str(warning.call_args.args[-1]))

    def test_local_database_warmup_failure_still_fails_startup(self) -> None:
        database = Mock()
        with (
            patch("app.main.settings.demo_mode", True),
            patch("app.main.settings.warehouse", "local"),
            patch("app.main.ensure_schema"),
            patch("app.main.SessionLocal", return_value=database),
            patch("app.main.seed_if_empty"),
            patch("app.main.apply_postgres_routines"),
            patch("app.main.orchestrator.warmup", side_effect=GuardrailError("local schema failure")),
            self.assertRaises(GuardrailError),
        ):
            startup()

        database.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
