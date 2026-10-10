import sys
import types
import unittest
from unittest.mock import patch

from app.warehouse import (
    test_warehouse_connection,
    verify_warehouse_domain,
    warehouse_schema_columns,
)


class FakeCursor:
    def __init__(self, executed: list[str]) -> None:
        self.executed = executed
        self.description = None

    def execute(self, query: str) -> None:
        self.executed.append(query)
        self.description = (
            [("table_name",)]
            if query.startswith("SELECT table_name FROM information_schema.tables")
            else ([("result",)] if query.startswith("SELECT 1") else None)
        )

    def fetchmany(self, size: int) -> list[tuple[int]]:
        if not size or not self.description:
            return []
        if self.description[0][0] == "table_name":
            catalog = next(
                (
                    statement.split()[-1].strip("`")
                    for statement in reversed(self.executed)
                    if statement.startswith("USE CATALOG ")
                ),
                "",
            )
            tables = {
                "banking": ("accounts", "branches", "customers", "products", "transactions"),
                "insurance": (
                    "claims",
                    "customers_with_payment_test_data",
                    "payments",
                    "policies",
                    "products",
                ),
                "retail": ("retail_customers", "retail_orders", "retail_products"),
            }
            return [(table,) for table in tables.get(catalog, ())]
        return [(1,)]

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
            "databricks_domain_catalogs": "banking:banking,insurance:insurance,retail:retail",
            "databricks_domain_schema": "silver",
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
            "databricks_domain_catalogs": "banking:banking,insurance:insurance,retail:retail",
            "databricks_domain_schema": "silver",
            "query_row_limit": 200,
        }
        with patch.multiple("app.warehouse.settings", **settings_values), patch.dict(
            sys.modules, {"databricks": databricks, "databricks.sql": connector}
        ):
            status = test_warehouse_connection("banking")

        self.assertTrue(status["ready"])
        self.assertTrue(status["connected"])
        self.assertEqual(connect_calls[0]["http_path"], "/sql/1.0/warehouses/warehouse-123")
        self.assertEqual(connect_calls[0]["access_token"], "secret-token")
        self.assertTrue(any(query.startswith("SELECT 1 AS connection_test") for query in executed))
        self.assertTrue(any("USE CATALOG `banking`" in query for query in executed))
        self.assertTrue(any("information_schema.tables" in query for query in executed))
        self.assertFalse(any("SELECT *" in query for query in executed))

    def test_live_domain_verification_checks_real_metadata_and_returns_query_results(self) -> None:
        metadata_results = [
            [{"catalog": "banking", "schema": "silver"}],
            [{"table_name": table} for table in (
                "accounts", "branches", "customers", "products", "transactions"
            )],
        ]
        sample_results = [
            [{"total_customers": 500, "min_score": 302, "max_score": 850}],
            [{"score_range": "300-399", "customers": 94}],
        ]
        with patch.multiple(
            "app.warehouse.settings",
            warehouse="databricks",
            databricks_server_hostname="workspace.example.com",
            databricks_http_path="/sql/warehouse",
            databricks_warehouse_id="",
            databricks_token="secret-token",
            databricks_domain_catalogs="banking:banking,insurance:insurance,retail:retail",
            databricks_domain_schema="silver",
        ), patch(
            "app.warehouse.warehouse_status",
            return_value={"ready": True},
        ), patch(
            "app.warehouse.execute_databricks_domain_batch",
            side_effect=[metadata_results, sample_results],
        ) as execute:
            result = verify_warehouse_domain("banking")

        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["catalog"], "banking")
        self.assertEqual(result["schema"], "silver")
        self.assertEqual(result["source_table"], "banking.silver.customers")
        self.assertEqual(result["results"], sample_results)
        self.assertEqual(execute.call_args_list[0].args[0], "banking")
        self.assertTrue(any("credit_score" in query for query in result["sql"]))
        self.assertTrue(any("percentile(credit_score, 0.75)" in query for query in result["sql"]))
        self.assertFalse(any("fact_customers" in query for query in result["sql"]))

    def test_domain_scoped_query_selects_the_mapped_databricks_catalog(self) -> None:
        from app.warehouse import _databricks_execute_many

        executed: list[str] = []
        connector = types.ModuleType("databricks.sql")
        connector.connect = lambda **_kwargs: FakeConnection(executed)
        databricks = types.ModuleType("databricks")
        databricks.sql = connector
        with patch.multiple(
            "app.warehouse.settings",
            warehouse="databricks",
            databricks_server_hostname="workspace.example.com",
            databricks_http_path="/sql/warehouse",
            databricks_warehouse_id="",
            databricks_token="secret-token",
            databricks_domain_catalogs="banking:banking,insurance:insurance,retail:retail",
            databricks_domain_schema="silver",
            query_row_limit=200,
        ), patch.dict(
            sys.modules, {"databricks": databricks, "databricks.sql": connector}
        ):
            _databricks_execute_many(["SELECT 1 AS result"], domain="insurance")

        self.assertIn("USE CATALOG `insurance`", executed)
        self.assertIn("USE SCHEMA `silver`", executed)


if __name__ == "__main__":
    unittest.main()
