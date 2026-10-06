import sys
import types
import unittest
from unittest.mock import patch

from app.warehouse import test_warehouse_connection, warehouse_schema_columns


class FakeCursor:
    def __init__(self, executed: list[str]) -> None:
        self.executed = executed
        self.description = None

    def execute(self, query: str) -> None:
        self.executed.append(query)
        self.description = [("result",)] if query == "SELECT 1" else None

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


class WarehouseTests(unittest.TestCase):
    def test_warehouse_schema_columns_reads_databricks_information_schema(self) -> None:
        settings_values = {
            "warehouse": "databricks",
            "databricks_server_hostname": "workspace.example.com",
            "databricks_http_path": "/sql/warehouse",
            "databricks_warehouse_id": "",
            "databricks_token": "secret-token",
            "databricks_catalog": "main",
            "databricks_schema": "ryze_bi",
            "query_row_limit": 200,
        }
        returned = [
            {"table_name": "customers", "column_name": "domain", "data_type": "STRING"},
            {"table_name": "customers", "column_name": "account_number", "data_type": "STRING"},
        ]
        with patch.multiple("app.warehouse.settings", **settings_values), patch(
            "app.warehouse.execute_readonly", return_value=returned
        ) as execute:
            columns = warehouse_schema_columns()

        self.assertEqual(columns["customers"], [
            {"name": "domain", "type": "STRING"},
            {"name": "account_number", "type": "STRING"},
        ])
        self.assertIn("`main`.information_schema.columns", execute.call_args.args[0])
        self.assertIn("table_schema = 'ryze_bi'", execute.call_args.args[0])

    def test_connection_test_uses_warehouse_id_as_http_path(self) -> None:
        executed: list[str] = []
        connect_calls: list[dict[str, str]] = []

        def connect(**kwargs: str) -> FakeConnection:
            connect_calls.append(kwargs)
            return FakeConnection(executed)

        connector = types.ModuleType("databricks.sql")
        connector.connect = connect
        databricks = types.ModuleType("databricks")
        databricks.sql = connector
        settings_values = {
            "warehouse": "databricks",
            "databricks_server_hostname": "workspace.example.com",
            "databricks_http_path": "",
            "databricks_warehouse_id": "warehouse-123",
            "databricks_token": "secret-token",
            "databricks_catalog": "main",
            "databricks_schema": "ryze_bi",
            "query_row_limit": 200,
        }
        with patch.multiple("app.warehouse.settings", **settings_values), patch.dict(
            sys.modules, {"databricks": databricks, "databricks.sql": connector}
        ):
            status = test_warehouse_connection()

        self.assertTrue(status["ready"])
        self.assertTrue(status["connected"])
        self.assertEqual(connect_calls[0]["http_path"], "/sql/1.0/warehouses/warehouse-123")
        self.assertEqual(connect_calls[0]["access_token"], "secret-token")
        self.assertIn("SELECT 1 AS connection_test", executed)
        self.assertTrue(any("`customers`" in query for query in executed))


if __name__ == "__main__":
    unittest.main()
