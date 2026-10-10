import sys
import types
import unittest
from unittest.mock import patch

from app.warehouse import (
    _snowflake_execute_many,
    test_warehouse_connection,
    warehouse_schema_columns,
)
from app.services.guardrail import GuardrailError


class FakeCursor:
    def __init__(self, executed: list[str]) -> None:
        self.executed = executed
        self.description = None

    def execute(self, query: str) -> None:
        self.executed.append(query)
        self.description = (
            [("RESULT",)]
            if query in {"SELECT 1 AS connection_test", "SELECT 1 AS RESULT"}
            else None
        )

    def fetchmany(self, size: int) -> list[tuple[int]]:
        return [(1,)] if size and self.description else []

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None


class FakeConnection:
    def __init__(self, executed: list[str]) -> None:
        self.executed = executed

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.executed)

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None


class SnowflakeWarehouseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.executed: list[str] = []
        self.connect_calls: list[dict[str, object]] = []

        def connect(**kwargs: object) -> FakeConnection:
            self.connect_calls.append(kwargs)
            return FakeConnection(self.executed)

        connector = types.ModuleType("snowflake.connector")
        connector.connect = connect
        self.connector = connector
        snowflake = types.ModuleType("snowflake")
        snowflake.__path__ = []
        snowflake.connector = connector
        self.modules = patch.dict(
            sys.modules, {"snowflake": snowflake, "snowflake.connector": connector}
        )
        self.modules.start()
        self.settings = patch.multiple(
            "app.warehouse.settings",
            warehouse="snowflake",
            snowflake_account="account",
            snowflake_user="service-user",
            snowflake_password="test-password",
            snowflake_role="read-role",
            snowflake_warehouse="compute",
            snowflake_database="FD_DEMO",
            snowflake_schema="PUBLIC",
            query_row_limit=200,
            query_timeout_seconds=8,
        )
        self.settings.start()

    def tearDown(self) -> None:
        self.settings.stop()
        self.modules.stop()

    def test_connection_test_checks_required_tables_and_uses_read_identity(self) -> None:
        status = test_warehouse_connection()

        self.assertTrue(status["ready"])
        self.assertTrue(status["connected"])
        self.assertEqual(status["mode"], "snowflake")
        self.assertEqual(self.connect_calls[0]["account"], "account")
        self.assertEqual(self.connect_calls[0]["role"], "read-role")
        self.assertTrue(any('"TRANSACTIONS"' in query for query in self.executed))
        self.assertNotIn("test-password", str(status))

    def test_schema_columns_normalize_snowflake_metadata_keys(self) -> None:
        rows = [
            {"TABLE_NAME": "ACCOUNTS", "COLUMN_NAME": "BALANCE", "DATA_TYPE": "FLOAT"}
        ]
        with patch("app.warehouse.execute_readonly", return_value=rows) as execute:
            columns = warehouse_schema_columns()

        self.assertEqual(columns, {"ACCOUNTS": [{"name": "BALANCE", "type": "FLOAT"}]})
        self.assertIn('"FD_DEMO".information_schema.columns', execute.call_args.args[0])
        self.assertIn("UPPER('PUBLIC')", execute.call_args.args[0])

    def test_readonly_results_use_lowercase_column_names(self) -> None:
        results = _snowflake_execute_many(["SELECT 1 AS RESULT"])

        self.assertEqual(results, [[{"result": 1}]])

    def test_connection_errors_do_not_return_the_password(self) -> None:
        def fail_connect(**_kwargs: object) -> None:
            raise RuntimeError("Login failed for test-password")

        self.connector.connect = fail_connect
        with self.assertRaisesRegex(GuardrailError, "redacted") as raised:
            _snowflake_execute_many(["SELECT 1"])

        self.assertNotIn("test-password", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
