"""The finance, insurance, retail and healthcare registries, with Databricks replaced.

Unit tests. Correctness against real data is measured by evals/run_registry_eval.py.
"""

import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.auth import AuthUser
from app.config import settings
from app.sensitive_data import require_compiled_sql_safe
from app.services.banking_agent import BankingAgent
from app.services.banking_query import QueryPlan, compile_plan, parse_question
from app.services.banking_registry import BANKING, active_registry, use_registry
from app.services.domain_registries import REGISTRIES
from app.services.guardrail import GuardrailError, validate_sql
from app.services.orchestrator import _registry_for
from app.services.sql_policy import enforce_select_policy
from app.warehouse import execute_healthcare_tenant_readonly

_CATALOGS = "banking:banking,finance:finance,insurance:insurance,retail:retail,healthcare:healthcare"


def parse(domain: str, question: str):
    with use_registry(REGISTRIES[domain]):
        return parse_question(question)


class CompileTests(unittest.TestCase):
    def test_every_metric_and_dimension_compiles_to_policy_compliant_sql(self) -> None:
        for domain, registry in REGISTRIES.items():
            catalog, schema = registry.catalog_schema.split(".")
            with use_registry(registry):
                for metric in registry.metrics:
                    plans = [QueryPlan(metric.id)]
                    plans += [QueryPlan(metric.id, intent="breakdown", dimensions=[d.id]) for d in metric.source.dimensions]
                    plans += [QueryPlan(metric.id, filters={d.id: [d.values[0]]}) for d in metric.source.dimensions if d.values]
                    if metric.supports_time:
                        plans.append(QueryPlan(metric.id, intent="trend", grain="month"))
                    for plan in plans:
                        with self.subTest(domain=domain, metric=metric.id, plan=plan.describe()):
                            compiled = compile_plan(plan)
                            enforce_select_policy(compiled.sql, registry.approved_tables, catalog, schema)
                            validate_sql(compiled.sql, compiled.limit)
                            require_compiled_sql_safe(compiled.sql)

    def test_filter_values_are_parameters_never_text(self) -> None:
        with use_registry(REGISTRIES["retail"]):
            compiled = compile_plan(QueryPlan("net_sales", filters={"city": ["Pune"]}))
        self.assertNotIn("Pune", compiled.sql)
        self.assertIn("Pune", compiled.parameters.values())

    def test_registries_never_name_another_domains_tables(self) -> None:
        banking_only = {"gold_transactions", "gold_loans", "gold_loan_repayments", "gold_cards", "gold_branches"}
        for domain, registry in REGISTRIES.items():
            with self.subTest(domain=domain):
                self.assertFalse(registry.approved_tables & banking_only)
                for metric in registry.metrics:
                    self.assertTrue(set(metric.source.tables) <= registry.approved_tables, metric.id)

    def test_ids_are_unique_within_each_registry(self) -> None:
        for registry in REGISTRIES.values():
            ids = [metric.id for metric in registry.metrics]
            self.assertEqual(len(ids), len(set(ids)))


class ParsingTests(unittest.TestCase):
    def test_typical_questions_map_to_the_right_metric_filters_and_breakdowns(self) -> None:
        cases = (
            ("finance", "What is the active-account cash balance?", "total_cash_balance", {"account_status": ["ACTIVE"]}, []),
            ("finance", "How many trades were executed?", "trade_count", {"trade_status": ["EXECUTED"]}, []),
            ("finance", "What is the gross trade value by asset class?", "gross_trade_value", {}, ["asset_class"]),
            ("insurance", "How many claims are there by status?", "claim_count", {}, ["claim_status"]),
            ("insurance", "What is the written premium for health policies?", "written_premium", {"policy_type": ["HEALTH"]}, []),
            ("retail", "What are net sales by product category?", "net_sales", {}, ["category"]),
            ("retail", "How many online orders were there?", "order_count", {"channel": ["ONLINE"]}, []),
            ("healthcare", "How many encounters are there by type?", "encounter_count", {}, ["encounter_type"]),
            ("healthcare", "What is the billed claim amount for rejected claims?", "billed_amount", {"claim_status": ["REJECTED"]}, []),
        )
        for domain, question, metric, filters, dimensions in cases:
            with self.subTest(question=question):
                plan = parse(domain, question).plan
                self.assertIsNotNone(plan)
                self.assertEqual((plan.metric_id, plan.filters, plan.dimensions), (metric, filters, dimensions))

    def test_an_amount_question_is_never_read_as_a_count(self) -> None:
        cases = (
            ("finance", "total value of cancelled trades", "gross_trade_value"),
            ("insurance", "total premium for health policies", "written_premium"),
            ("retail", "total net sales for online orders", "net_sales"),
        )
        for domain, question, metric in cases:
            with self.subTest(question=question):
                plan = parse(domain, question).plan
                self.assertIsNotNone(plan)
                self.assertEqual(plan.metric_id, metric)

    def test_a_shop_is_not_the_store_channel(self) -> None:
        plan = parse("retail", "What were net sales in Pune stores?").plan
        self.assertEqual(plan.filters, {"city": ["Pune"]})

    def test_monthly_asks_for_a_trend_not_a_payment_frequency(self) -> None:
        plan = parse("insurance", "Show monthly written premium for 2025").plan
        self.assertEqual((plan.intent, plan.grain, plan.filters), ("trend", "month", {}))

    def test_one_letter_codes_are_never_read_as_filters(self) -> None:
        for domain, question in (("healthcare", "How many patients are there in Delhi?"), ("retail", "How many customers are in Pune?")):
            plan = parse(domain, question).plan
            self.assertNotIn("gender", plan.filters)

    def test_a_shared_value_cannot_be_read_from_two_dimensions(self) -> None:
        # MF is both an account type and an asset class; trades only carry asset class.
        plan = parse("finance", "How many MF trades were there?").plan
        self.assertEqual(plan.filters, {"asset_class": ["MF"]})

    def test_ranking_securities_needs_the_security_breakdown(self) -> None:
        plan = parse("finance", "Show the top 5 securities by number of trades").plan
        self.assertEqual(plan.dimensions, ["security"])
        self.assertEqual(plan.top_n, 5)

    def test_named_unsupported_topics_are_refused_with_a_reason(self) -> None:
        for domain, question in (("finance", "What is our AUM?"), ("insurance", "What is the loss ratio?"), ("retail", "What is our gross margin?"), ("healthcare", "What is the readmission rate?")):
            with self.subTest(question=question):
                self.assertIsNotNone(parse(domain, question).refusal)


class AgentTests(unittest.TestCase):
    def agent(self, domain, rows=None):
        self.sent = []

        def execute(sql, domain_name, parameters, limit):
            self.sent.append((sql, domain_name))
            if "AS first_date" in sql:
                return [{"first_date": "2025-01-01", "last_date": "2026-03-01", "distinct_dates": 50}]
            return rows if rows is not None else []

        return BankingAgent(execute, REGISTRIES[domain], scope="t")

    def test_what_the_registry_cannot_place_is_left_to_the_existing_path(self) -> None:
        for domain, question in (("retail", "Which customers are likely to churn next month?"), ("finance", "How do account openings vary by month?"), ("insurance", "Tell me about our agents' performance")):
            with self.subTest(question=question):
                self.assertIsNone(self.agent(domain).run(question, AuthUser("u", domain, "analyst")))
                self.assertEqual(self.sent, [])

    def test_runs_in_its_own_domain_and_restores_banking_afterwards(self) -> None:
        agent = self.agent("insurance", [{"claim_count": 3, "record_count": 3}])
        result = agent.run("How many claims are there?", AuthUser("u", "insurance", "analyst"))
        self.assertEqual(result.status, "complete")
        self.assertTrue(all(domain == "insurance" for _sql, domain in self.sent))
        self.assertIs(active_registry(), BANKING)
        self.assertIn("insurance.gold", result.answer)

    def test_a_snapshot_has_no_anomaly_series(self) -> None:
        result = self.agent("finance").run("Are there unusual cash balances?", AuthUser("u", "finance", "analyst"))
        self.assertEqual(result.status, "insufficient_data")


class RoutingTests(unittest.TestCase):
    def test_enabled_domains_use_their_registry(self) -> None:
        with patch.multiple(settings, warehouse="databricks", ask_ai_agentic_routing=True, metric_registry_domains="finance,insurance,retail,healthcare"):
            for domain in REGISTRIES:
                self.assertIs(_registry_for(domain, False), REGISTRIES[domain])
            self.assertIsNone(_registry_for("banking", False))

    def test_switches_and_sensitive_requests_turn_it_off(self) -> None:
        with patch.multiple(settings, warehouse="databricks", ask_ai_agentic_routing=True, metric_registry_domains="retail"):
            self.assertIsNone(_registry_for("finance", False))
            self.assertIsNone(_registry_for("retail", True))
        with patch.multiple(settings, ask_ai_agentic_routing=False, metric_registry_domains="retail"):
            self.assertIsNone(_registry_for("retail", False))
        with patch.multiple(settings, warehouse="local", ask_ai_agentic_routing=True, metric_registry_domains="healthcare"):
            self.assertIsNone(_registry_for("healthcare", False))


class HealthcareTenantTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch.multiple(
            settings, warehouse="databricks", databricks_server_hostname="h", databricks_http_path="/p", databricks_token="t",
            databricks_domain_catalogs=_CATALOGS, databricks_domain_schema="gold", databricks_healthcare_tenant_catalogs="tenant-a:hc_tenant_a",
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_queries_run_only_in_the_tenants_mapped_catalog(self) -> None:
        with patch("app.warehouse._databricks_query", return_value=[{"n": 1}]) as sent:
            execute_healthcare_tenant_readonly("SELECT COUNT(*) AS n FROM gold_encounters", "tenant-a")
        namespace = sent.call_args.args[0]
        self.assertEqual((namespace[2], namespace[3]), ("hc_tenant_a", "gold"))

    def test_no_tenant_or_an_unmapped_tenant_runs_nothing(self) -> None:
        with patch("app.warehouse._databricks_query") as sent:
            for tenant in (None, "", "tenant-b"):
                with self.subTest(tenant=tenant), self.assertRaises(GuardrailError):
                    execute_healthcare_tenant_readonly("SELECT COUNT(*) AS n FROM gold_encounters", tenant)
            sent.assert_not_called()

    def test_the_sql_policy_still_applies(self) -> None:
        with patch("app.warehouse._databricks_query") as sent, self.assertRaises(GuardrailError):
            execute_healthcare_tenant_readonly("SELECT secret('a', 'b') AS v FROM gold_encounters", "tenant-a")
        sent.assert_not_called()

    def test_identifiers_are_never_returned(self) -> None:
        with self.assertRaises(HTTPException):
            require_compiled_sql_safe("SELECT patient_id FROM gold_patients LIMIT 5")


if __name__ == "__main__":
    unittest.main()
