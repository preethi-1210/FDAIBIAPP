import sys
import types
import unittest
from unittest.mock import patch

from app.warehouse import (
    close_warehouse_sessions,
    test_warehouse_connection as _test_warehouse_connection,
    verify_warehouse_domain,
    warehouse_schema_columns,
)


class FakeCursor:
    def __init__(self, executed: list[str], catalog: str = "") -> None:
        self.executed = executed
        self.catalog = catalog
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
                self.catalog,
            )
            tables = {
                "banking": (
                    "agg_accounts_daily",
                    "agg_accounts_monthly",
                    "agg_transactions_daily",
                ),
                "insurance": (
                    "agg_claims_daily",
                    "agg_policies_daily",
                ),
                "retail": (
                    "gold_orders", "gold_customers", "gold_products", "gold_order_items",
                    "gold_payments", "gold_inventory", "gold_returns", "gold_stores",
                ),
            }
            return [(table,) for table in tables.get(catalog, ())]
        return [(1,)]

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None


class FakeConnection:
    def __init__(self, executed: list[str], catalog: str = "") -> None:
        self.executed = executed
        self.catalog = catalog

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.executed, self.catalog)

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None


class WarehouseTests(unittest.TestCase):
    def setUp(self) -> None:
        # Pooled sessions from another test (or a real workspace) must not be reused.
        close_warehouse_sessions()
        self.addCleanup(close_warehouse_sessions)

    def test_databricks_named_parameters_use_qmark_binding(self) -> None:
        from app.warehouse import _databricks_execute_many

        executed: list[tuple[str, tuple[object, ...] | None]] = []

        class ParameterCursor(FakeCursor):
            def execute(self, query: str, parameters: tuple[object, ...] | None = None) -> None:
                executed.append((query, parameters))
                self.description = [("result",)] if query.startswith("SELECT") else None

        class ParameterConnection(FakeConnection):
            def cursor(self) -> ParameterCursor:
                return ParameterCursor([])

        connector = types.ModuleType("databricks.sql")
        connector.connect = lambda **_kwargs: ParameterConnection([])
        databricks = types.ModuleType("databricks")
        databricks.sql = connector
        with patch.multiple(
            "app.warehouse.settings",
            warehouse="databricks",
            databricks_server_hostname="workspace.example.com",
            databricks_http_path="/sql/warehouse",
            databricks_warehouse_id="",
            databricks_token="secret-token",
            databricks_domain_catalogs="banking:banking",
            databricks_domain_schema="gold",
            query_row_limit=200,
        ), patch.dict(
            sys.modules, {"databricks": databricks, "databricks.sql": connector}
        ):
            _databricks_execute_many(
                [
                    "SELECT * FROM transactions WHERE transaction_date >= :start "
                    "AND transaction_date < :end AND category = :category LIMIT 10"
                ],
                domain="banking",
                parameter_sets=[{"start": "2026-09-01", "end": "2026-10-01", "category": "retail"}],
            )

        self.assertEqual(
            executed[-1],
            (
                "SELECT * FROM transactions WHERE transaction_date >= ? "
                "AND transaction_date < ? AND category = ? LIMIT 10",
                ("2026-09-01", "2026-10-01", "retail"),
            ),
        )

    def test_warehouse_schema_columns_reads_databricks_information_schema(self) -> None:
        settings_values = {
            "warehouse": "databricks",
            "databricks_server_hostname": "workspace.example.com",
            "databricks_http_path": "/sql/warehouse",
            "databricks_warehouse_id": "",
            "databricks_token": "secret-token",
            "databricks_catalog": "main",
            "databricks_schema": "ryze_bi",
            "databricks_domain_catalogs": "banking:banking,finance:banking,insurance:insurance,retail:retail",
            "databricks_domain_schema": "gold",
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
            return FakeConnection(executed, kwargs.get("catalog", ""))

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
            "databricks_domain_catalogs": "banking:banking,finance:banking,insurance:insurance,retail:retail",
            "databricks_domain_schema": "gold",
            "query_row_limit": 200,
        }
        with patch.multiple("app.warehouse.settings", **settings_values), patch.dict(
            sys.modules, {"databricks": databricks, "databricks.sql": connector}
        ):
            status = _test_warehouse_connection("banking")

        self.assertTrue(status["ready"])
        self.assertTrue(status["connected"])
        self.assertEqual(connect_calls[0]["http_path"], "/sql/1.0/warehouses/warehouse-123")
        self.assertEqual(connect_calls[0]["access_token"], "secret-token")
        self.assertTrue(any(query.startswith("SELECT 1 AS connection_test") for query in executed))
        self.assertEqual(connect_calls[0]["catalog"], "banking")
        self.assertTrue(any("information_schema.tables" in query for query in executed))
        self.assertFalse(any("SELECT *" in query for query in executed))

    def test_live_domain_verification_checks_real_metadata_and_returns_query_results(self) -> None:
        metadata_results = [
            [{"catalog": "banking", "schema": "gold"}],
            [{"table_name": table} for table in (
                "agg_accounts_daily", "agg_transactions_daily"
            )],
        ]
        sample_results = [
            [{"_gold_date": "2026-10-14", "total_balance": 500, "account_count": 94}],
        ]
        with patch.multiple(
            "app.warehouse.settings",
            warehouse="databricks",
            databricks_server_hostname="workspace.example.com",
            databricks_http_path="/sql/warehouse",
            databricks_warehouse_id="",
            databricks_token="secret-token",
            databricks_domain_catalogs="banking:banking,insurance:insurance,retail:retail",
            databricks_domain_schema="gold",
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
        self.assertEqual(result["schema"], "gold")
        self.assertEqual(result["source_table"], "banking.gold.agg_accounts_daily")
        self.assertEqual(result["results"], sample_results)
        self.assertEqual(execute.call_args_list[0].args[0], "banking")
        self.assertTrue(any("agg_accounts_daily" in query for query in result["sql"]))
        self.assertFalse(any("fact_customers" in query for query in result["sql"]))

    def test_domain_scoped_query_selects_the_mapped_databricks_catalog(self) -> None:
        from app.warehouse import _databricks_execute_many

        executed: list[str] = []
        connect_calls: list[dict[str, str]] = []
        connector = types.ModuleType("databricks.sql")
        connector.connect = lambda **kwargs: connect_calls.append(kwargs) or FakeConnection(executed)
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

        self.assertEqual(connect_calls[0]["catalog"], "insurance")
        self.assertEqual(connect_calls[0]["schema"], "silver")


class DatabricksSessionPoolTests(unittest.TestCase):
    settings_values = {
        "warehouse": "databricks",
        "databricks_server_hostname": "workspace.example.com",
        "databricks_http_path": "/sql/warehouse",
        "databricks_warehouse_id": "",
        "databricks_token": "secret-token",
        "databricks_domain_catalogs": "banking:banking",
        "databricks_domain_schema": "gold",
        "query_row_limit": 200,
    }

    def setUp(self) -> None:
        close_warehouse_sessions()
        self.addCleanup(close_warehouse_sessions)

    def _connector(self, connect):
        connector = types.ModuleType("databricks.sql")
        connector.connect = connect
        databricks = types.ModuleType("databricks")
        databricks.sql = connector
        return patch.dict(sys.modules, {"databricks": databricks, "databricks.sql": connector})

    def test_sessions_are_reused_and_batches_keep_query_order(self) -> None:
        from app.warehouse import _databricks_execute_many

        opened: list[dict[str, str]] = []

        class EchoCursor(FakeCursor):
            def execute(self, query: str, parameters=None) -> None:
                self.description = [("query",)]
                self.query = query

            def fetchmany(self, size: int):
                return [(self.query,)]

        class EchoConnection(FakeConnection):
            def cursor(self) -> EchoCursor:
                return EchoCursor([])

        def connect(**kwargs):
            opened.append(kwargs)
            return EchoConnection([])

        queries = [f"SELECT {index} AS value LIMIT 1" for index in range(6)]
        with patch.multiple("app.warehouse.settings", **self.settings_values), self._connector(connect):
            first = _databricks_execute_many(queries, domain="banking")
            sessions_after_first_batch = len(opened)
            _databricks_execute_many(["SELECT 1 LIMIT 1"], domain="banking")
            _databricks_execute_many(["SELECT 2 LIMIT 1"], domain="banking")

        self.assertEqual([rows[0]["query"] for rows in first], queries)
        self.assertEqual(len(opened), sessions_after_first_batch)
        self.assertTrue(all(kwargs["catalog"] == "banking" and kwargs["schema"] == "gold" for kwargs in opened))

    def test_an_expired_pooled_session_is_replaced_once(self) -> None:
        from app.warehouse import _databricks_execute_many

        connections = []

        class ExpiringCursor(FakeCursor):
            def __init__(self, connection) -> None:
                super().__init__([])
                self.connection = connection

            def execute(self, query: str, parameters=None) -> None:
                if self.connection.expired:
                    raise RuntimeError("Invalid SessionHandle")
                self.description = [("result",)]

        class ExpiringConnection(FakeConnection):
            def __init__(self) -> None:
                super().__init__([])
                self.expired = False
                self.closed = False

            def cursor(self) -> ExpiringCursor:
                return ExpiringCursor(self)

            def close(self) -> None:
                self.closed = True

        def connect(**_kwargs):
            connections.append(ExpiringConnection())
            return connections[-1]

        with patch.multiple("app.warehouse.settings", **self.settings_values), self._connector(connect):
            _databricks_execute_many(["SELECT 1 LIMIT 1"], domain="banking")
            connections[0].expired = True
            rows = _databricks_execute_many(["SELECT 1 LIMIT 1"], domain="banking")

        self.assertEqual(rows, [[{"result": 1}]])
        self.assertEqual(len(connections), 2)
        self.assertTrue(connections[0].closed)

    def test_a_rejected_statement_is_not_retried_and_keeps_the_session(self) -> None:
        from app.services.guardrail import GuardrailError
        from app.warehouse import _databricks_execute_many

        opened = []

        class ServerOperationError(Exception):
            pass

        exc_module = types.ModuleType("databricks.sql.exc")
        exc_module.ServerOperationError = ServerOperationError

        class RejectingCursor(FakeCursor):
            def execute(self, query: str, parameters=None) -> None:
                raise ServerOperationError("[TABLE_OR_VIEW_NOT_FOUND] missing_table")

        class RejectingConnection(FakeConnection):
            def cursor(self) -> RejectingCursor:
                return RejectingCursor([])

        def connect(**_kwargs):
            opened.append(1)
            return RejectingConnection([])

        with (
            patch.multiple("app.warehouse.settings", **self.settings_values),
            self._connector(connect),
            patch.dict(sys.modules, {"databricks.sql.exc": exc_module}),
        ):
            for _attempt in range(2):
                with self.assertRaisesRegex(GuardrailError, "TABLE_OR_VIEW_NOT_FOUND"):
                    _databricks_execute_many(["SELECT * FROM missing_table LIMIT 1"], domain="banking")

        self.assertEqual(len(opened), 1)

    def test_sessions_are_not_shared_across_workspaces(self) -> None:
        from app.warehouse import _databricks_execute_many

        opened: list[str] = []

        def connect(**kwargs):
            opened.append(kwargs["server_hostname"])
            return FakeConnection([])

        with self._connector(connect):
            for hostname in ("one.example.com", "two.example.com"):
                with patch.multiple(
                    "app.warehouse.settings",
                    **{**self.settings_values, "databricks_server_hostname": hostname},
                ):
                    _databricks_execute_many(["SELECT 1 LIMIT 1"], domain="banking")

        self.assertEqual(opened, ["one.example.com", "two.example.com"])


if __name__ == "__main__":
    unittest.main()
