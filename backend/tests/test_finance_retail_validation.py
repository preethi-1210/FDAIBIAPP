"""Finance prompts, metrics and glossary, and the Retail health check, with Databricks replaced.

Unit tests only. Live checks against the real warehouse are run separately.
"""

import re
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from test_ask_ai_workflow import SCHEMAS, AskAiTestCase

from app.auth import AuthUser
from app.config import settings
from app.domains import PROMPTS, finance_gold_glossary
from app.services.guardrail import validate_sql
from app.services.metrics import calculate_metric, format_metric_answer, match_metric
from app.services.orchestrator import _FINANCE_DASHBOARD_TEXT, _databricks_dashboard_queries
from app.services.sql_generator import generate_sql
from app.warehouse import test_warehouse_connection as check_connection
from app.warehouse import verify_warehouse_domain

_CATALOGS = "banking:banking,finance:finance,insurance:insurance,retail:retail,healthcare:healthcare"
_SCHEMA = {
    "gold_accounts": ("account_id", "client_id", "account_type", "open_date", "status", "cash_balance"),
    "gold_trades": ("account_id", "price", "quantity", "security_id", "settlement_date", "side", "status", "trade_id", "trade_timestamp"),
    "gold_securities": ("security_id", "symbol", "security_name", "asset_class", "sector", "exchange", "currency", "listing_date"),
    "gold_dividends": ("dividend_id", "security_id", "ex_date", "amount_per_share"),
    "gold_clients": ("client_id", "first_name", "last_name", "risk_profile", "kyc_status", "city", "onboarding_date"),
    "agg_accounts_monthly": ("_gold_month", "record_count", "total_cash_balance", "avg_cash_balance"),
}
_RETAIL_TABLES = {"gold_orders", "gold_customers", "gold_products", "gold_order_items", "gold_payments", "gold_inventory", "gold_returns", "gold_stores"}
_RETAIL_ONLY_REAL = {
    "gold_orders": {"order_id", "customer_id", "store_id", "order_date", "channel", "order_status"},
    "gold_customers": {"customer_id", "loyalty_tier", "signup_date"},
}


def schema_context() -> dict:
    return {
        "tables": {table: [{"name": name, "type": "string"} for name in columns] for table, columns in _SCHEMA.items()},
        "join_paths": [],
        "glossary": [],
        "rag_context": [],
    }


class _Databricks(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch.multiple(settings, warehouse="databricks", databricks_domain_catalogs=_CATALOGS, databricks_domain_schema="gold")
        patcher.start()
        self.addCleanup(patcher.stop)


class ActiveCashBalanceTests(_Databricks):
    def test_the_dashboard_filters_active_accounts_explicitly_in_the_finance_catalog(self) -> None:
        headline = _databricks_dashboard_queries("finance")[0]
        self.assertIn("`finance`.gold.gold_accounts", headline)
        self.assertIn("WHERE status = 'ACTIVE'", headline)
        validate_sql(headline)

    def test_the_all_status_total_has_no_status_filter(self) -> None:
        headline = _databricks_dashboard_queries("finance")[0]
        outer = headline[headline.rindex("FROM"):]
        self.assertIn("agg_accounts_monthly", outer)
        self.assertNotIn("status", outer)
        self.assertIn("SUM(total_cash_balance)", headline)


class TerminologyTests(unittest.TestCase):
    def test_finance_text_never_calls_cash_aum_or_notional(self) -> None:
        text = " ".join(_FINANCE_DASHBOARD_TEXT.values()).lower()
        for word in ("aum", "assets under management", "notional"):
            self.assertNotIn(word, text)

    def test_the_tile_is_accounts(self) -> None:
        self.assertEqual(_FINANCE_DASHBOARD_TEXT["event_label"], "Accounts")

    def test_trend_text_does_not_present_openings_as_balance_history(self) -> None:
        self.assertIn("not a history", _FINANCE_DASHBOARD_TEXT["trend_description"])
        self.assertIn("opening", _FINANCE_DASHBOARD_TEXT["trend_value_label"])
        self.assertNotIn("balance trend", " ".join(_FINANCE_DASHBOARD_TEXT.values()).lower())


class PromptTests(_Databricks):
    def test_no_prompt_assumes_data_the_catalog_lacks(self) -> None:
        for prompt in PROMPTS["finance"]:
            for word in ("aum", "notional", "region", "segment", "churn", "market value", "portfolio"):
                self.assertNotIn(word, prompt.lower(), prompt)

    def test_every_prompt_resolves_without_a_model(self) -> None:
        context = schema_context()
        with patch("app.services.sql_generator.complete_json", side_effect=AssertionError("needs a model")):
            for prompt in PROMPTS["finance"]:
                with self.subTest(prompt=prompt):
                    matched = match_metric(prompt, "finance")
                    if matched is None:
                        validate_sql(generate_sql(prompt, context, False, "finance"))
                    else:
                        self.assertNotIsInstance(matched, str)

    def test_generated_templates_use_real_columns(self) -> None:
        for prompt, expected in (
            ("How many accounts are there by status?", "FROM gold_accounts"),
            ("Which securities have the highest trading activity?", "LEFT JOIN gold_securities"),
            ("What dividend records are available?", "FROM gold_dividends"),
            ("How do account openings vary by month?", "agg_accounts_monthly"),
        ):
            with self.subTest(prompt=prompt):
                self.assertIn(expected, generate_sql(prompt, schema_context(), False, "finance"))


class MetricTests(_Databricks):
    def measured(self, question: str, value: float, records: int) -> tuple[dict, str]:
        captured = []

        def execute(sql, domain, parameters, limit):
            captured.append((sql, domain))
            return [{"metric_value": value, "record_count": records}]

        result = calculate_metric(match_metric(question, "finance"), question, execute=execute)
        self.assertEqual(captured[-1][1], "finance")
        return result, captured[-1][0]

    def test_total_cash_balance_includes_every_status(self) -> None:
        result, sql = self.measured("What is the total cash balance across accounts?", 3016849156.56, 3003)
        self.assertEqual(result["metric"], "total_cash_balance")
        self.assertNotIn("WHERE", sql)

    def test_active_balance_filters_on_active(self) -> None:
        result, sql = self.measured("What is the active-account cash balance?", 2625316070.84, 2611)
        self.assertEqual(result["metric"], "active_account_cash_balance")
        self.assertIn("status = 'ACTIVE'", sql)

    def test_executed_trades_filters_on_executed_and_reads_as_a_count(self) -> None:
        result, sql = self.measured("How many trades were executed?", 36193, 36193)
        self.assertIn("status = 'EXECUTED'", sql)
        self.assertIn("36,193 across", format_metric_answer(result))

    def test_gross_trade_value_says_it_includes_every_status(self) -> None:
        result, sql = self.measured("What is the gross trade value?", 14411204517.99, 37758)
        self.assertIn("SUM(price * quantity)", sql)
        self.assertNotIn("WHERE", sql)
        self.assertIn("CANCELLED", result["filters"])

    def test_a_cash_balance_is_not_answered_for_a_past_period(self) -> None:
        # open_date says when an account opened, so filtering on it would misreport a past balance.
        self.assertIsNone(match_metric("What was the total cash balance in 2024?", "finance"))
        self.assertIsNone(match_metric("What is the active-account cash balance for 2024-03?", "finance"))

    def test_new_metrics_belong_to_finance_only(self) -> None:
        for domain in ("banking", "insurance", "retail", "healthcare"):
            for question in ("What is the total cash balance?", "What is the gross trade value?"):
                with self.subTest(domain=domain, question=question):
                    matched = match_metric(question, domain)
                    self.assertFalse(getattr(matched, "domain", None) == "finance")


class GlossaryTests(unittest.TestCase):
    def test_every_entry_names_a_verified_table_and_column(self) -> None:
        for row in finance_gold_glossary():
            with self.subTest(term=row["business_term"]):
                self.assertIn(row["table_name"], _SCHEMA)
                self.assertIn(row["column_name"], _SCHEMA[row["table_name"]])
                self.assertEqual(row["domain"], "finance")

    def test_no_entry_refers_to_demo_tables_or_unsupported_terms(self) -> None:
        for row in finance_gold_glossary():
            self.assertNotIn(row["table_name"], {"orders", "products", "customers"})
            self.assertNotIn(row["business_term"], {"aum", "notional", "churn", "instrument"})

    def test_only_finance_is_replaced_when_on_databricks(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from app.services.context_graph import ContextGraphService

        db = MagicMock()
        db.execute.return_value.mappings.return_value = [
            {"domain": "finance", "business_term": "aum", "table_name": "orders", "column_name": "total_amount", "description": "demo"},
            {"domain": "retail", "business_term": "revenue", "table_name": "orders", "column_name": "total_amount", "description": "keep"},
        ]
        service = ContextGraphService.__new__(ContextGraphService)
        import networkx

        service.graph, service.tables, service.domain_tables, service.glossary = networkx.Graph(), {}, {}, []
        service.rag = SimpleNamespace(sync=lambda *_args, **_kwargs: None)
        with patch("app.services.context_graph.inspect") as inspector, patch.object(settings, "warehouse", "databricks"), patch("app.services.context_graph.warehouse_status", return_value={"ready": False}):
            inspector.return_value.get_table_names.return_value = []
            service.rebuild(db)
        terms = {(row["domain"], row["business_term"]) for row in service.glossary}
        self.assertNotIn(("finance", "aum"), terms)
        self.assertIn(("retail", "revenue"), terms)
        self.assertIn(("finance", "cash balance"), terms)


class RetailHealthTests(_Databricks):
    def batch(self, domain, queries, tenant_id=None):
        self.sent.append((domain, queries))
        if "information_schema" in queries[-1]:
            return [[{"catalog": "retail", "schema": "gold"}], [{"table_name": name} for name in self.available]]
        return [[{"order_count": 20007}] for _query in queries]

    def verify(self, available):
        self.sent, self.available = [], available
        with patch("app.warehouse.warehouse_status", return_value={"ready": True}), patch("app.warehouse.execute_databricks_domain_batch", side_effect=self.batch):
            return verify_warehouse_domain("retail")

    def test_it_passes_on_the_real_table_names(self) -> None:
        result = self.verify(_RETAIL_TABLES)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["source_table"], "retail.gold.gold_orders")
        self.assertEqual(set(result["tables_checked"]), _RETAIL_TABLES)

    def test_a_missing_core_table_still_fails(self) -> None:
        for missing in sorted(_RETAIL_TABLES):
            with self.subTest(missing=missing), self.assertRaises(Exception) as raised:
                self.verify(_RETAIL_TABLES - {missing})
            self.assertIn(missing, str(raised.exception))

    def test_the_samples_name_only_verified_columns_and_pass_the_sql_policy(self) -> None:
        result = self.verify(_RETAIL_TABLES)
        self.assertEqual(len(result["sql"]), 2)
        for sql in result["sql"]:
            validate_sql(sql)
            self.assertNotIn("agg_retail", sql)
            self.assertNotIn("total_order_amount", sql)
        for table, columns in _RETAIL_ONLY_REAL.items():
            sample = next(sql for sql in result["sql"] if f"FROM {table}" in sql)
            for column in columns:
                self.assertIn(column, sample)

    def test_the_connection_check_uses_the_same_tables(self) -> None:
        self.sent, self.available = [], _RETAIL_TABLES
        with patch("app.warehouse.warehouse_status", return_value={"mode": "databricks", "ready": True}), patch("app.warehouse.execute_databricks_domain_batch", side_effect=self.batch):
            self.assertTrue(check_connection("retail")["connected"])

    def test_retail_reads_only_retail(self) -> None:
        self.verify(_RETAIL_TABLES)
        self.assertTrue(all(domain == "retail" for domain, _queries in self.sent))

    def test_healthcare_stays_unconfigured_rather_than_faked(self) -> None:
        with patch("app.warehouse.warehouse_status", return_value={"ready": True}), self.assertRaises(Exception) as raised:
            verify_warehouse_domain("healthcare")
        self.assertIn("No live Databricks verification query", str(raised.exception))
        self.assertNotIsInstance(raised.exception, HTTPException)


class PromptPipelineTests(AskAiTestCase):
    """Each suggested prompt through the real orchestrator and validators; only the connection is replaced."""

    def setUp(self) -> None:
        super().setUp()
        real = {table: [{"name": name, "type": "string"} for name in columns] for table, columns in _SCHEMA.items()}
        for patcher in (
            patch.dict(SCHEMAS, {"finance": real}),
            patch.object(settings, "ask_ai_agentic_routing", True),
            patch.object(settings, "databricks_domain_catalogs", _CATALOGS),
            patch("app.warehouse._databricks_query", side_effect=self._record),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.sent: list[str] = []

    def _record(self, _namespace, sql, _values, _fetch_limit):
        self.sent.append(sql)
        if "AS first_date" in sql:
            return [{"first_date": "2015-01-03", "last_date": "2026-03-13", "distinct_dates": 100}]
        # Exactly the columns the query selects, as the warehouse would return them.
        row = {alias: 5 for alias in re.findall(r" AS (\w+)", sql)}
        if "period" in row:
            row["period"] = "2026-01"
        return [row]

    def test_every_prompt_is_answered_from_the_warehouse_without_generated_sql(self) -> None:
        user = AuthUser(username="finance.user", domain="finance", role="analyst")
        for prompt in PROMPTS["finance"]:
            with self.subTest(prompt=prompt):
                self.sent.clear()
                response = self.orchestrator.run(self.db, prompt, user)
                self.assertEqual(response.status, "complete", response.answer)
                self.assertGreaterEqual(len(self.sent), 1)
        self.assertEqual(self.sql_prompts, [], "a prompt needed the model to write SQL")


if __name__ == "__main__":
    unittest.main()
