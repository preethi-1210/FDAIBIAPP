"""Phase 3: the banking business context inside the Ask AI banking workflow."""

import json
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from app.auth import AuthUser
from app.config import settings
from app.llm import trace_steps
from app.services import banking_context as ctx
from app.services.banking_agent import BankingAgent
from app.services.banking_query import PROMPT_VERSION, _PLAN_SYSTEM, parse_question, plan_with_llm, ParseResult
from app.services.banking_registry import BANKING, METRICS, use_registry

DATASET = json.loads((Path(__file__).resolve().parents[1] / "evals" / "banking_context_dataset.json").read_text(encoding="utf-8"))
TODAY = date(2026, 11, 11)
ANALYST = AuthUser(username="bank.user", domain="banking", role="analyst")


class FakeWarehouse:
    """Returns rows shaped like the compiled query asked for; records every statement."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def __call__(self, sql, domain, parameters, limit):
        self.statements.append(sql)
        if "MIN(" in sql and "first_date" in sql:
            return [{"first_date": "2010-01-02", "last_date": "2026-06-06", "distinct_dates": 4453}]
        if "SUM(a.balance)" in sql:
            return [{"total_balance": 1988344989.56, "record_count": 8000}]
        if "AVG(a.balance)" in sql:
            return [{"average_balance": 248543.12, "record_count": 8000}]
        if "SUM(t.amount)" in sql:
            return [{"total_amount": 251829230.04, "transaction_count": 100011, "record_count": 100011}]
        if "COUNT(*)" in sql and "gold_accounts" in sql:
            return [{"account_count": 6825, "record_count": 6825}]
        raise AssertionError(f"unexpected SQL: {sql[:80]}")


def run(question: str, warehouse: FakeWarehouse | None = None, llm_reply: dict | None = None):
    warehouse = warehouse or FakeWarehouse()
    calls = []

    def fake_complete_json(system, user, provider=None, stage="llm"):
        calls.append(user)
        return llm_reply

    with trace_steps() as steps, patch("app.services.banking_query.complete_json", side_effect=fake_complete_json), \
            patch.object(settings, "banking_llm_planning", True):
        result = BankingAgent(warehouse).run(question, ANALYST)
    return result, steps, warehouse, calls


class DatasetTests(unittest.TestCase):
    """The 46 capability questions, through the business context and rule planner (no model)."""

    def test_every_question_reaches_its_expected_outcome(self) -> None:
        self.assertEqual(len(DATASET), 46)
        for case in DATASET:
            with self.subTest(case=case["id"]):
                resolution = ctx.resolve_terminology(case["question"])
                if resolution.refusal:
                    kind = "refuse"
                elif resolution.clarification:
                    kind = "clarify"
                else:
                    parsed = parse_question(resolution.question, None, TODAY)
                    kind = "refuse" if parsed.refusal else "answer" if parsed.plan else "clarify"
                    if parsed.plan is not None:
                        self.assertEqual(parsed.plan.metric_id, case["metric"])
                        self.assertEqual(dict(parsed.plan.filters), case.get("filters", {}))
                        self.assertEqual(list(parsed.plan.dimensions), case.get("dimensions", []))
                self.assertEqual(kind, case["expect"])
                for term in case.get("terms", []):
                    self.assertIn(term, resolution.terms)


class MerchantTests(unittest.TestCase):
    def test_merchant_identity_is_refused_and_never_planned(self) -> None:
        for question in (
            "Which merchants generate the highest transaction value?",
            "Show transaction amount by merchant",
            "Top 5 merchants by amount",
            "What is the merchant-level failure rate?",
        ):
            with self.subTest(question=question):
                result, _steps, warehouse, calls = run(question, llm_reply={"metric_id": "transaction_amount", "dimensions": ["merchant_category"]})
                self.assertEqual(result.status, "insufficient_data")
                self.assertIn("merchant_category", result.answer)
                self.assertEqual(warehouse.statements, [])
                self.assertEqual(calls, [], "the model must not be asked")

    def test_merchant_category_still_works(self) -> None:
        for question in ("Show transaction amount by merchant category", "Top 5 merchant categories by transaction amount", "transaction volume by spend category"):
            with self.subTest(question=question):
                resolution = ctx.resolve_terminology(question)
                self.assertIsNone(resolution.refusal)
                self.assertEqual(parse_question(resolution.question, None, TODAY).plan.dimensions, ["merchant_category"])

    def test_the_registry_no_longer_reads_merchants_as_categories(self) -> None:
        aliases = [alias for metric in METRICS for dim in metric.source.dimensions if dim.id == "merchant_category" for alias in dim.aliases]
        self.assertTrue(aliases)
        self.assertNotIn(r"merchants?", aliases)


class SynonymTests(unittest.TestCase):
    def test_unambiguous_phrases_are_read_with_the_registry_words(self) -> None:
        cases = {
            "How many new customers joined in 2024?": ("new_customers", "2024"),
            "How many accounts were opened in 2025?": ("new_accounts_opened", "2025"),
            "How many clients do we have?": ("customer_count", None),
            "Show loan payments for 2025": ("repayment_collected", "2025"),
        }
        for question, (metric_id, period) in cases.items():
            with self.subTest(question=question):
                resolution = ctx.resolve_terminology(question)
                plan = parse_question(resolution.question, None, TODAY).plan
                self.assertEqual(plan.metric_id, metric_id)
                self.assertEqual(plan.period.label if plan.period else None, period)
                self.assertTrue(resolution.rewrites)

    def test_ambiguous_words_ask_and_never_reach_the_model_or_warehouse(self) -> None:
        for question in (
            "What is the total deposit balance?", "How many inactive accounts are there?", "Which customers are inactive?",
            "What is the payment success rate?", "How much money is held by customers?", "Which KYC records are incomplete?",
            "What is the customer growth rate?",
        ):
            with self.subTest(question=question):
                # Even a model that would happily map it is never asked.
                result, _steps, warehouse, calls = run(question, llm_reply={"metric_id": "total_account_balance"})
                self.assertEqual(result.status, "needs_clarification")
                self.assertEqual(warehouse.statements, [])
                self.assertEqual(calls, [])
                self.assertEqual(result.evidence["business_context"]["context_version"], ctx.CONTEXT_VERSION)

    def test_unavailable_data_is_refused_with_its_reason(self) -> None:
        for question, fragment in (
            ("What is the total outstanding loan amount?", "outstanding"),
            ("How many loans are approved?", "disbursed loans"),
            ("Are there settlement mismatches?", "settlement"),
            ("How many refunds were processed?", "refund"),
            ("Show RTGS transactions", "RTGS"),
            ("What are the operating expenses?", "never estimated from transaction amounts"),
            ("Which loan categories have higher risk?", "Credit risk"),
            ("Which transactions look suspicious?", "never confirmed fraud"),
        ):
            with self.subTest(question=question):
                result, _steps, warehouse, calls = run(question)
                self.assertEqual(result.status, "insufficient_data")
                self.assertIn(fragment, result.answer)
                self.assertEqual(warehouse.statements, [])
                self.assertEqual(calls, [])

    def test_the_nine_pending_rules_stay_pending(self) -> None:
        self.assertEqual(sum(1 for rule in ctx.RULES if rule.status == ctx.PENDING), 9)


class DisclosureTests(unittest.TestCase):
    def test_total_balance_discloses_closed_accounts_without_changing_the_formula(self) -> None:
        result, steps, warehouse, _calls = run("What is the total account balance?")
        self.assertEqual(result.status, "complete")
        self.assertIn("SUM(a.balance)", warehouse.statements[-1])
        self.assertNotIn("CLOSED", warehouse.statements[-1])
        self.assertIn("CLOSED accounts are included in this current total balance", result.answer)
        self.assertIn("existing registry definition", result.answer)
        self.assertIn("official business approval", result.answer)
        self.assertIn("not an owner-approved business KPI", result.answer)
        rules = {rule["id"]: rule["status"] for rule in result.evidence["business_context"]["rules"]}
        self.assertEqual(rules["closed_accounts_in_total_balance"], ctx.PENDING)
        self.assertTrue(any(step["stage"] == "business_rules" for step in steps))

    def test_balance_of_active_accounts_needs_no_closed_disclosure(self) -> None:
        result, *_ = run("What is the total account balance of active accounts?")
        self.assertEqual(result.status, "complete")
        self.assertNotIn("CLOSED accounts are included", result.answer)

    def test_known_limitations_are_stated_in_the_answer(self) -> None:
        result, *_ = run("What is the average account balance?")
        self.assertIn("Limitations: 15 accounts have no balance.", result.answer)
        self.assertIn("CLOSED accounts are included in this current average balance", result.answer)

    def test_evidence_keeps_its_provenance_and_adds_the_context(self) -> None:
        result, *_ = run("How many active accounts do we have?")
        evidence = result.evidence
        for key in ("metric", "registry_version", "definition", "sources", "coverage", "data_quality"):
            self.assertIn(key, evidence)
        self.assertTrue(all(source.startswith("banking.gold.") for source in evidence["sources"]))
        context = evidence["business_context"]
        self.assertIn("Active account", context["terms"])
        self.assertEqual(context["source"], "banking.gold")


class GroundingTests(unittest.TestCase):
    """A model plan may only contain what the question asks for."""

    question = "How much money moved?"

    def test_rules_leave_this_question_to_the_model(self) -> None:
        parsed = parse_question(self.question, None, TODAY)
        self.assertIsNone(parsed.plan)
        self.assertFalse(parsed.unknown_terms)

    def test_unrequested_parts_of_a_plan_are_detected(self) -> None:
        from app.services.banking_agent import _ungrounded
        from app.services.banking_query import QueryPlan

        plain = "total transaction amount"
        self.assertEqual(_ungrounded(QueryPlan("transaction_amount", filters={"status": ["SUCCESS"]}), plain, ParseResult()), ["a filter on SUCCESS"])
        self.assertEqual(_ungrounded(QueryPlan("transaction_amount", intent="trend", grain="day"), plain, ParseResult()), ["a daily grain"])
        self.assertEqual(
            _ungrounded(QueryPlan("transaction_amount", intent="breakdown", dimensions=["channel"], top_n=5), plain, ParseResult()),
            ["a breakdown by channel", "a top 5 ranking"],
        )
        self.assertEqual(_ungrounded(QueryPlan("transaction_amount", intent="breakdown", dimensions=["channel"], top_n=5), "top 5 channels by transaction amount", ParseResult()), [])
        # A paraphrase leaves words for the model to map, so its filter stands (validation still applies).
        self.assertEqual(_ungrounded(QueryPlan("transaction_amount", filters={"merchant_category": ["FUEL"]}), "What was paid for automotive energy?", ParseResult()), [])

    def test_an_invented_breakdown_is_refused_before_any_query(self) -> None:
        for reply, fragment in (
            ({"metric_id": "transaction_amount", "dimensions": ["channel"]}, "a breakdown by channel"),
            ({"metric_id": "transaction_amount", "grain": "day", "intent": "trend"}, "a daily grain"),
        ):
            with self.subTest(reply=reply):
                result, steps, warehouse, calls = run(self.question, llm_reply=reply)
                self.assertEqual(len(calls), 1)
                self.assertEqual(result.status, "needs_clarification")
                self.assertIn(fragment, result.answer)
                self.assertEqual([sql for sql in warehouse.statements if "first_date" not in sql], [])
                self.assertTrue(any(step["stage"] == "planner" and not step["ok"] for step in steps))

    def test_a_plan_that_adds_nothing_is_answered(self) -> None:
        result, _steps, _warehouse, calls = run(self.question, llm_reply={"metric_id": "transaction_amount"})
        self.assertEqual(len(calls), 1)
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.plan.interpreted_by, "llm")
        self.assertIn("Read your question as", result.answer)


class PlannerContextTests(unittest.TestCase):
    def test_the_model_prompt_carries_the_business_context(self) -> None:
        captured = []
        with use_registry(BANKING), patch("app.services.banking_query.complete_json", side_effect=lambda s, u, p=None, stage="": captured.append((s, u)) or {}):
            plan_with_llm("anything", ParseResult(), None, None)
        system, user = captured[0]
        self.assertIn("Approved business terms", user)
        self.assertIn("Never map them to a metric", user)
        for word in ("deposits", "merchants", "payments", "outstanding loans", "fraud"):
            self.assertIn(word, user)
        self.assertNotIn("failed payments", system)
        self.assertEqual(PROMPT_VERSION, "banking-plan-3")

    def test_other_domains_get_no_banking_context(self) -> None:
        from app.services.domain_registries import REGISTRIES

        captured = []
        with use_registry(REGISTRIES["retail"]), patch("app.services.banking_query.complete_json", side_effect=lambda s, u, p=None, stage="": captured.append(u) or {}):
            plan_with_llm("anything", ParseResult(), None, None)
        self.assertNotIn("Approved business terms", captured[0])


if __name__ == "__main__":
    unittest.main()
