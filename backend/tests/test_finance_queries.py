"""Finance queries against the columns verified in finance.gold, with Databricks replaced.

These are unit tests. They guard the SQL text and how results are used; the live check against the
real warehouse is separate.
"""

import unittest
from datetime import date
from unittest.mock import patch

from sqlglot import exp, parse_one

from app.config import settings
from app.domains import DOMAINS
from app.services import orchestrator as orchestrator_module
from app.services.guardrail import validate_sql
from app.services.metrics import calculate_metric, match_metric
from app.services.orchestrator import Orchestrator, _databricks_dashboard_queries
from app.services.reports import get_report
from app.services.sql_generator import generate_sql
from app.warehouse import verify_warehouse_domain

VERIFIED = {
    "gold_accounts": {"account_id", "client_id", "account_type", "open_date", "status", "cash_balance"},
    "gold_trades": {"account_id", "price", "quantity", "security_id", "settlement_date", "side", "status", "trade_id", "trade_timestamp"},
    "agg_accounts_daily": {"_gold_date", "record_count", "total_cash_balance", "avg_cash_balance"},
    "agg_accounts_monthly": {"_gold_month", "record_count", "total_cash_balance", "avg_cash_balance"},
}
_CATALOGS = "banking:banking,finance:finance,insurance:insurance,retail:retail,healthcare:healthcare"
_SETTINGS = {"warehouse": "databricks", "databricks_domain_catalogs": _CATALOGS, "databricks_domain_schema": "gold"}


def columns_by_table(sql: str) -> list[tuple[str, str]]:
    """(table, column) for real column references; a query with one source needs no qualifier."""
    tree = parse_one(sql, read="databricks")
    aliases = {table.alias_or_name: table.name for table in tree.find_all(exp.Table)}
    found = []
    for column in tree.find_all(exp.Column):
        # An unqualified column belongs to the source of its own SELECT, which may be a subquery's.
        own = column.find_ancestor(exp.Select).find(exp.Table)
        table = aliases.get(column.table) if column.table else (own.name if own is not None else None)
        found.append((table, column.name))
    return found


class _Finance(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch.multiple(settings, **_SETTINGS)
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_uses_verified_columns(self, sql: str) -> None:
        pairs = columns_by_table(sql)
        self.assertTrue(pairs or "NULL" in sql.upper(), sql)
        for table, column in pairs:
            self.assertIn(table, VERIFIED, sql)
            self.assertIn(column, VERIFIED[table], f"{table}.{column} is not a verified column")


class DashboardQueryTests(_Finance):
    def test_every_query_uses_only_verified_columns_and_passes_the_sql_policy(self) -> None:
        queries = _databricks_dashboard_queries("finance")
        self.assertEqual(len(queries), 10)
        for sql in queries:
            with self.subTest(sql=sql[:60]):
                self.assert_uses_verified_columns(sql)
                validate_sql(sql)

    def test_finance_reads_only_its_own_catalog(self) -> None:
        text = " ".join(_databricks_dashboard_queries("finance"))
        self.assertIn("`finance`.gold.", text)
        for other in ("`banking`", "`insurance`", "`retail`", "`healthcare`"):
            self.assertNotIn(other, text)

    def test_the_headline_totals_every_month_rather_than_the_latest_one(self) -> None:
        headline = _databricks_dashboard_queries("finance")[0]
        self.assertIn("SUM(total_cash_balance)", headline)
        self.assertIn("SUM(record_count)", headline)
        self.assertNotIn("MAX(_gold_month)", headline)

    def test_active_appears_only_as_the_explicit_status_filter(self) -> None:
        text = " ".join(_databricks_dashboard_queries("finance"))
        self.assertEqual(text.count("status = 'ACTIVE'"), 1)
        self.assertEqual(text.lower().count("active"), 2)  # The filter and the column alias.


class DashboardResultTests(_Finance):
    def batch(self, *_args, **_kwargs):
        return [
            [{"volume": 3016849156.56, "events": 3003, "active_cash_balance": 2625316070.84}], [{"customers": None, "churn_rate": None}], [], [],
            [{"label": "2026-01", "value": 12022937.18, "events": 10}], [], [], [{"claims": None}], [], [],
        ]

    def dashboard(self, domain: str, db) -> dict:
        with patch.object(orchestrator_module, "execute_databricks_domain_batch", side_effect=self.batch) as batch:
            result = Orchestrator.__new__(Orchestrator)._dashboard_metrics_remote_warehouse(db, domain)
        self.assertEqual(batch.call_args.args[0], domain)
        return result

    def test_the_headline_is_labelled_as_what_it_is(self) -> None:
        from unittest.mock import MagicMock

        db = MagicMock()
        db.query.return_value.filter.return_value.count.return_value = 0
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
        result = self.dashboard("finance", db)
        self.assertEqual(result["metric_name"], "Total cash balance")
        self.assertEqual((result["volume"], result["events"]), (3016849156.56, 3003))
        self.assertEqual(result["active_cash_balance"], 2625316070.84)
        self.assertEqual(result["event_label"], "Accounts")

    def test_other_domains_keep_their_labels(self) -> None:
        from unittest.mock import MagicMock

        db = MagicMock()
        db.query.return_value.filter.return_value.count.return_value = 0
        db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = []
        expected = {"banking": "Deposits / flow", "insurance": "Gross written premium", "retail": "Revenue"}
        for domain, label in expected.items():
            with self.subTest(domain=domain):
                result = self.dashboard(domain, db)
                self.assertEqual(result["metric_name"], label)
                self.assertNotIn("active_cash_balance", result)


class OtherFinancePathTests(_Finance):
    def test_the_metric_uses_the_verified_column(self) -> None:
        captured = []

        def execute(sql, domain, parameters, limit):
            captured.append((sql, domain))
            return [{"value": 12022937.18, "row_count": 10}]

        question = "What is the opened account balance for 2026-01?"
        calculate_metric(match_metric(question, "finance"), question, execute=execute)
        sql, domain = captured[-1]
        self.assertEqual(domain, "finance")
        self.assertIn("SUM(total_cash_balance)", sql)
        self.assert_uses_verified_columns(sql.replace(":start", "'2026-01-01'").replace(":end", "'2026-02-01'"))

    def test_the_report_uses_the_verified_column(self) -> None:
        calls = []

        def many(statements, domain=None, row_limit=None):
            calls.extend(sql for sql, _parameters in statements)
            return [
                [{"first_date": date(2015, 1, 3), "last_date": date(2026, 1, 15)}] if " AS first_date" in sql
                else [{"event_date": date(2025, 6, 2), "amount_value": 100.0, "count_value": 1}]
                for sql, _parameters in statements
            ]

        with patch("app.services.reports.execute_readonly_many", side_effect=many), patch("app.services.reports.databricks_domain_catalog", return_value="finance"):
            get_report("finance", None, date(2025, 6, 1), date(2025, 6, 30), "monthly", "previous_period")
        text = " ".join(calls)
        self.assertIn("a.total_cash_balance", text)
        self.assertNotIn("total_balance", text)
        self.assertIn("`finance`.`gold`.`agg_accounts_daily`", text)

    def test_the_month_template_matches_the_real_columns_without_a_model(self) -> None:
        columns = [{"name": name, "type": "string"} for name in sorted(VERIFIED["agg_accounts_monthly"])]
        schema = {"tables": {"agg_accounts_monthly": columns}, "join_paths": [], "glossary": [], "rag_context": []}
        with patch("app.services.sql_generator.complete_json", side_effect=AssertionError("template should match")):
            sql = generate_sql("Show account balances by month", schema, False, "finance")
        self.assert_uses_verified_columns(sql)
        self.assertIn("total_cash_balance", sql)

    def test_the_health_check_uses_the_verified_column(self) -> None:
        sent = []

        def batch(domain, queries, tenant_id=None):
            sent.append((domain, queries))
            if "table_name" in queries[-1]:
                return [[{"catalog": "finance", "schema": "gold"}], [{"table_name": "agg_accounts_monthly"}]]
            return [[{"_gold_month": "2026-01", "total_cash_balance": 12022937.18, "account_count": 10}]]

        with patch("app.warehouse.warehouse_status", return_value={"ready": True}), patch("app.warehouse.execute_databricks_domain_batch", side_effect=batch):
            result = verify_warehouse_domain("finance")
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["source_table"], "finance.gold.agg_accounts_monthly")
        sample = sent[-1][1][0]
        self.assert_uses_verified_columns(sample)
        self.assertNotIn("total_balance", sample)
        self.assertTrue(all(domain == "finance" for domain, _queries in sent))


class DomainIsolationTests(_Finance):
    def test_finance_columns_are_not_used_by_other_domains(self) -> None:
        for domain in DOMAINS:
            if domain in {"finance", "healthcare"}:
                continue
            with self.subTest(domain=domain):
                self.assertNotIn("total_cash_balance", " ".join(_databricks_dashboard_queries(domain)))

    def test_other_domains_never_read_the_finance_catalog(self) -> None:
        for domain in ("banking", "insurance", "retail"):
            with self.subTest(domain=domain):
                self.assertNotIn("`finance`", " ".join(_databricks_dashboard_queries(domain)))

    def test_the_finance_metric_is_offered_only_to_finance(self) -> None:
        question = "What is the opened account balance?"
        self.assertIsNotNone(match_metric(question, "finance"))
        for domain in ("banking", "insurance", "retail", "healthcare"):
            with self.subTest(domain=domain):
                matched = match_metric(question, domain)
                self.assertFalse(getattr(matched, "key", None) == "opened_account_balance")


if __name__ == "__main__":
    unittest.main()
