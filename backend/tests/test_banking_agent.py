"""Banking workflow: planning, compiling, SQL policy, verification, forecasting, follow-ups.

The warehouse is faked here; real-data correctness is scored by evals/run_banking_eval.py.
"""

import json
import unittest
import uuid
from datetime import date
from unittest.mock import patch

import numpy as np
from fastapi import HTTPException

from test_ask_ai_workflow import AskAiTestCase, FakeWarehouse

from app.auth import AuthUser
from app.config import settings
from app.models import ConversationQueryState, HitlReview
from app.sensitive_data import require_compiled_sql_safe
from app.services.banking_agent import BankingAgent, _coverage_cache
from app.services.banking_query import (
    Period,
    PlanError,
    QueryPlan,
    compile_plan,
    extract_periods,
    parse_question,
    PROMPT_VERSION,
    plan_with_llm,
    validate_plan,
)
from app.services.banking_registry import APPROVED_TABLES, METRICS, metric_by_id
from app.services.forecasting import (
    MIN_PERIODS,
    InsufficientHistoryError,
    assess_history,
    backtest,
    forecast_series,
)
from app.services.guardrail import GuardrailError
from app.services.sql_policy import classify_sql_error, enforce_select_policy

TODAY = date(2026, 11, 2)
ONE_DAY = {"first_date": "2026-03-15", "last_date": "2026-03-15", "distinct_dates": 1}


def plan_of(question: str, previous=None) -> QueryPlan:
    parsed = parse_question(question, previous, TODAY)
    assert parsed.plan is not None, (question, parsed)
    return parsed.plan


class PlanningTests(unittest.TestCase):
    def test_filters_dimensions_and_periods_are_all_read(self) -> None:
        plan = plan_of("Total transaction amount in Mumbai for electronics during Q1 2026")
        self.assertEqual(plan.metric_id, "transaction_amount")
        self.assertEqual(plan.filters, {"city": ["Mumbai"], "merchant_category": ["ELECTRONICS"]})
        self.assertEqual((plan.period.start, plan.period.end_exclusive), (date(2026, 1, 1), date(2026, 4, 1)))

    def test_spoken_values_map_to_exact_column_values(self) -> None:
        self.assertEqual(plan_of("amount of successful transactions").filters, {"status": ["SUCCESS"]})
        self.assertEqual(plan_of("how many net banking transactions").filters, {"channel": ["NETBANKING"]})
        self.assertEqual(plan_of("how many transactions in Bangalore").filters, {"city": ["Bengaluru"]})

    def test_a_word_that_cannot_be_placed_is_never_dropped(self) -> None:
        for question in (
            "What is the transaction amount for rural customers?",
            "Total transactions for gadgets",
            "Show transaction volume for premium-plus clients",
        ):
            parsed = parse_question(question, None, TODAY)
            self.assertIsNone(parsed.plan, question)
            self.assertTrue(parsed.unknown_terms, question)

    def test_the_premium_customer_segment_is_not_mistaken_for_insurance(self) -> None:
        self.assertEqual(plan_of("transaction amount for premium customers").filters, {"customer_segment": ["PREMIUM"]})
        self.assertIn("Insurance", parse_question("what is the written premium?", None, TODAY).refusal)

    def test_a_breakdown_the_metric_does_not_have_is_refused_with_alternatives(self) -> None:
        parsed = parse_question("Show total balance by merchant category", None, TODAY)
        self.assertIn("Available breakdowns", parsed.refusal)
        parsed = parse_question("Show transaction amount by shoe size", None, TODAY)
        self.assertIn("shoe size", parsed.refusal)

    def test_unavailable_measures_are_refused_by_name(self) -> None:
        for question in ("What is the NPA ratio?", "net interest margin for 2025", "customer churn", "credit score by city", "fraud transactions", "balance trend"):
            self.assertTrue(parse_question(question, None, TODAY).refusal, question)

    def test_current_figures_refuse_a_period_instead_of_ignoring_it(self) -> None:
        plan = plan_of("How many accounts are there in 2020?")
        with self.assertRaisesRegex(PlanError, "no dated history"):
            validate_plan(plan)

    def test_periods(self) -> None:
        cases = {
            "from 2025-01 to 2025-06": (date(2025, 1, 1), date(2025, 7, 1)),
            "between March 2025 and May 2025": (date(2025, 3, 1), date(2025, 6, 1)),
            "on 15 March 2026": (date(2026, 3, 15), date(2026, 3, 16)),
            "on 2026-03-15": (date(2026, 3, 15), date(2026, 3, 16)),
            "last month": (date(2026, 10, 1), date(2026, 11, 1)),
            "this year": (date(2026, 1, 1), date(2026, 11, 3)),
            "last 3 months": (date(2026, 8, 1), date(2026, 11, 3)),
            "in Feb 2024": (date(2024, 2, 1), date(2024, 3, 1)),
            "for 2024": (date(2024, 1, 1), date(2025, 1, 1)),
        }
        for text, expected in cases.items():
            periods, _rest = extract_periods(text, TODAY)
            self.assertEqual(len(periods), 1, text)
            self.assertEqual((periods[0].start, periods[0].end_exclusive), expected, text)

    def test_two_periods_mean_a_comparison_in_the_order_asked(self) -> None:
        plan = plan_of("Compare loan repayments in 2025 versus 2024")
        self.assertEqual(plan.intent, "compare")
        self.assertEqual((plan.period.label, plan.compare_to.label), ("2025", "2024"))

    def test_ranking_by_amount_uses_the_amount_metric(self) -> None:
        plan = plan_of("top 3 merchant categories by amount for failed transactions")
        self.assertEqual((plan.metric_id, plan.top_n, plan.dimensions), ("transaction_amount", 3, ["merchant_category"]))
        self.assertEqual(plan.filters, {"status": ["FAILED"]})

    def test_forecast_horizon_is_read_and_bounded(self) -> None:
        self.assertEqual(plan_of("forecast loan repayments for the next 3 months").horizon, 3)
        self.assertEqual(plan_of("predict new accounts opened next quarter").horizon, 3)
        self.assertIn("limited", parse_question("forecast new accounts next year", None, TODAY).refusal)

    def test_validation_rejects_anything_not_in_the_registry(self) -> None:
        for plan in (
            QueryPlan(metric_id="transaction_amount", dimensions=["card_number"]),
            QueryPlan(metric_id="transaction_amount", filters={"channel": ["' OR 1=1 --"]}),
            QueryPlan(metric_id="transaction_amount", filters={"account_id": ["A1"]}),
            QueryPlan(metric_id="transaction_amount", grain="second"),
            QueryPlan(metric_id="transaction_amount", intent="forecast"),
            QueryPlan(metric_id="made_up_metric"),
        ):
            with self.assertRaises(PlanError):
                validate_plan(plan)


class FollowUpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.first = plan_of("Show transaction amount for UPI in March 2026")

    def test_a_new_filter_keeps_the_metric_and_period(self) -> None:
        plan = plan_of("and only failed ones?", self.first)
        self.assertEqual(plan.metric_id, "transaction_amount")
        self.assertEqual(plan.filters, {"channel": ["UPI"], "status": ["FAILED"]})
        self.assertEqual(plan.period.label, "March 2026")

    def test_a_value_of_the_same_dimension_replaces_it(self) -> None:
        self.assertEqual(plan_of("what about ATM?", self.first).filters, {"channel": ["ATM"]})

    def test_a_breakdown_keeps_the_filters(self) -> None:
        plan = plan_of("now by city", self.first)
        self.assertEqual((plan.dimensions, plan.filters), (["city"], {"channel": ["UPI"]}))

    def test_a_new_period_replaces_the_old_one(self) -> None:
        self.assertEqual(plan_of("same for April 2026", self.first).period.label, "April 2026")

    def test_a_new_metric_with_a_cue_keeps_the_filters(self) -> None:
        plan = plan_of("and the average transaction amount?", self.first)
        self.assertEqual((plan.metric_id, plan.filters), ("average_transaction_amount", {"channel": ["UPI"]}))

    def test_filters_can_be_cleared(self) -> None:
        self.assertEqual(plan_of("same but remove the filters", self.first).filters, {})

    def test_an_unrelated_question_does_not_inherit(self) -> None:
        self.assertEqual(plan_of("How many loans were disbursed by loan type?", self.first).filters, {})
        self.assertIsNone(parse_question("What is this application about?", self.first, TODAY).plan)

    def test_plans_survive_being_stored(self) -> None:
        stored = json.loads(json.dumps(self.first.to_dict()))
        self.assertEqual(QueryPlan.from_dict(stored), self.first)


class CompilerTests(unittest.TestCase):
    def test_user_text_never_enters_the_statement(self) -> None:
        plan = QueryPlan(metric_id="transaction_amount", filters={"channel": ["UPI"]}, period=Period(date(2026, 3, 1), date(2026, 4, 1), "March"))
        compiled = compile_plan(plan)
        self.assertNotIn("UPI", compiled.sql)
        self.assertEqual(compiled.parameters["f_channel_0"], "UPI")
        self.assertEqual(compiled.parameters["period_start"], date(2026, 3, 1))

    def test_every_metric_and_dimension_compiles_inside_the_policy(self) -> None:
        for metric in METRICS:
            shapes = [QueryPlan(metric_id=metric.id)] + [QueryPlan(metric_id=metric.id, dimensions=[d.id], intent="breakdown") for d in metric.source.dimensions]
            if metric.supports_time:
                shapes.append(QueryPlan(metric_id=metric.id, grain="month", intent="trend"))
            for plan in shapes:
                validate_plan(plan)
                sql = compile_plan(plan).sql
                enforce_select_policy(sql, APPROVED_TABLES, "banking", "gold")
                require_compiled_sql_safe(sql)

    def test_identifiers_only_link_tables(self) -> None:
        sql = compile_plan(QueryPlan(metric_id="transaction_amount", dimensions=["region"], intent="breakdown")).sql
        self.assertIn("a.account_id = t.account_id", sql)
        select_list = sql.split(" FROM ")[0]
        self.assertNotIn("account_id", select_list)

    def test_only_forecastable_metrics_have_a_date(self) -> None:
        for metric_id in ("new_accounts_opened", "repayment_collected"):
            self.assertTrue(metric_by_id(metric_id).forecastable)
        self.assertFalse(metric_by_id("total_account_balance").forecastable)


class SqlPolicyTests(unittest.TestCase):
    def check(self, sql: str) -> str:
        return enforce_select_policy(sql, APPROVED_TABLES, "banking", "gold")

    def test_rejected_statements(self) -> None:
        rejected = {
            "DROP TABLE gold_accounts": "SELECT",
            "SELECT 1 FROM gold_accounts LIMIT 1; DROP TABLE gold_accounts": "one SQL",
            "DELETE FROM gold_accounts": "SELECT",
            "INSERT INTO gold_accounts VALUES (1)": "SELECT",
            "SELECT * FROM gold_accounts LIMIT 5": "Wildcard",
            "SELECT status FROM gold_accounts": "row limit",
            "SELECT status FROM gold_accounts LIMIT 100000": "row limit",
            "SELECT status FROM healthcare.gold.gold_patients LIMIT 5": "banking.gold",
            "SELECT status FROM banking.silver.accounts LIMIT 5": "banking.gold",
            "SELECT table_name FROM system.information_schema.tables LIMIT 5": "System metadata",
            "SELECT status FROM summary_transactions LIMIT 5": "not approved",
            "SELECT status FROM snapshot_customers LIMIT 5": "not approved",
            "SELECT x FROM read_files('s3://bucket/x') LIMIT 5": "Table functions",
            "SELECT ai_query('m', 'p') AS x FROM gold_accounts LIMIT 5": "not allowed",
            "SELECT current_user() AS u FROM gold_accounts LIMIT 5": "not allowed",
            "SELECT a.status FROM gold_accounts a CROSS JOIN gold_branches b LIMIT 5": "join condition",
            "this is not sql": "SELECT|parsed",
        }
        for sql, reason in rejected.items():
            with self.assertRaisesRegex(GuardrailError, reason, msg=sql):
                self.check(sql)

    def test_allowed_statements(self) -> None:
        self.check("SELECT status, COUNT(*) AS n FROM gold_accounts GROUP BY status LIMIT 10")
        self.check("SELECT status FROM banking.gold.gold_accounts LIMIT 10")
        self.check("WITH x AS (SELECT status FROM gold_accounts) SELECT status FROM x LIMIT 5")

    def test_cte_cannot_hide_an_unapproved_table(self) -> None:
        with self.assertRaises(GuardrailError):
            self.check("WITH x AS (SELECT status FROM banking.silver.accounts) SELECT status FROM x LIMIT 5")

    def test_failures_are_told_apart(self) -> None:
        cases = {
            "[UNRESOLVED_COLUMN.WITH_SUGGESTION] A column cannot be resolved": "invalid_query",
            "[TABLE_OR_VIEW_NOT_FOUND] The table cannot be found": "missing_object",
            "[INSUFFICIENT_PERMISSIONS] User does not have USE SCHEMA": "permission_denied",
            "Query cancelled because of STATEMENT_TIMEOUT": "timeout",
            "Databricks SQL connection or query failed: connection reset": "infrastructure",
            "The Databricks warehouse is failing repeatedly": "infrastructure",
        }
        for message, kind in cases.items():
            self.assertEqual(classify_sql_error(message), kind, message)


class LlmPlanTests(unittest.TestCase):
    def agent(self, reply, rows=None):
        patcher = patch("app.services.banking_query.complete_json", side_effect=reply if isinstance(reply, Exception) else None, return_value=None if isinstance(reply, Exception) else reply)
        patcher.start()
        self.addCleanup(patcher.stop)
        _coverage_cache._entries.clear()
        self.executed = []

        def execute(sql, domain, parameters, limit):
            self.executed.append(sql)
            if sql.startswith("SELECT MIN"):
                return [ONE_DAY]
            return rows or [{"total_amount": 5.0, "transaction_count": 2, "record_count": 2}]

        return BankingAgent(execute)

    def run_agent(self, agent, question):
        return agent.run(question, AuthUser("u", "banking", "analyst"), None, "ollama")

    def test_a_model_plan_is_used_and_disclosed(self) -> None:
        agent = self.agent({"metric_id": "transaction_amount", "filters": {"merchant_category": ["FUEL"]}, "unsupported_terms": []})
        result = self.run_agent(agent, "What was paid for automotive energy?")
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.plan.interpreted_by, "llm")
        self.assertIn("Read your question as", result.answer)
        self.assertEqual(result.evidence["prompt_version"], PROMPT_VERSION)

    def test_a_model_cannot_add_columns_or_values(self) -> None:
        for reply in (
            {"metric_id": "transaction_amount", "dimensions": ["counterparty_account"]},
            {"metric_id": "transaction_amount", "filters": {"merchant_category": ["ALL'; DROP TABLE x"]}},
            {"metric_id": "invented_metric"},
        ):
            agent = self.agent(reply)
            result = self.run_agent(agent, "Show spending by gadgets")
            if result is not None:
                self.assertNotEqual(result.status, "complete")
            self.assertEqual(self.executed, [])

    def test_a_model_that_drops_a_filter_gets_a_clarification(self) -> None:
        agent = self.agent({"metric_id": "transaction_amount", "filters": {}, "dimensions": [], "unsupported_terms": []})
        result = self.run_agent(agent, "What is the transaction amount for rural customers?")
        self.assertEqual(result.status, "needs_clarification")
        self.assertIn("rural", result.answer)
        self.assertEqual(self.executed, [])

    def test_a_breakdown_does_not_account_for_a_word_rules_could_not_place(self) -> None:
        agent = self.agent({"metric_id": "transaction_amount", "dimensions": ["customer_segment"], "unsupported_terms": []})
        result = self.run_agent(agent, "What is the transaction amount for rural customers?")
        self.assertEqual(result.status, "needs_clarification")
        self.assertIn("rural", result.answer)
        self.assertEqual(self.executed, [])

    def test_a_malformed_model_plan_never_reaches_the_user_as_an_explanation(self) -> None:
        # Observed from llama3.2 on the real question.
        agent = self.agent({"metric_id": "transaction_amount", "dimensions": ["customer_segment="]})
        result = self.run_agent(agent, "What is the transaction amount for rural customers?")
        self.assertEqual(result.status, "needs_clarification")
        self.assertIn("rural", result.answer)
        self.assertNotIn("customer_segment=", result.answer)
        self.assertEqual(self.executed, [])
        agent = self.agent({"metric_id": "collection_rate", "dimensions": ["loan_type", "AUTO"], "grain": "day"})
        result = self.run_agent(agent, "What was the total paid for automotive energy purchases?")
        self.assertEqual(result.status, "needs_clarification")
        self.assertNotIn("AUTO", result.answer)
        self.assertEqual(self.executed, [])

    def test_a_filter_rules_found_stays_whatever_the_model_replies(self) -> None:
        agent = self.agent({"metric_id": "transaction_amount", "filters": {"merchant_category": ["FUEL"]}})
        result = self.run_agent(agent, "What is the total transaction amount for automotive energy through UPI?")
        self.assertEqual(result.plan.filters, {"merchant_category": ["FUEL"], "channel": ["UPI"]})

    def test_a_filter_is_kept_even_when_rules_found_no_metric(self) -> None:
        agent = self.agent({"metric_id": "transaction_amount", "filters": {}, "unsupported_terms": []})
        result = self.run_agent(agent, "How much money moved through UPI in March 2026?")
        self.assertEqual(result.plan.filters, {"channel": ["UPI"]})
        self.assertEqual(result.plan.period.label, "March 2026")

    def test_every_provider_failing_is_a_clarification_not_an_answer(self) -> None:
        agent = self.agent(RuntimeError("all providers down"))
        result = self.run_agent(agent, "What is the transaction amount for rural customers?")
        self.assertEqual(result.status, "needs_clarification")

    def test_no_provider_reply_is_handled(self) -> None:
        parsed = parse_question("How much money moved?", None, TODAY)
        with patch("app.services.banking_query.complete_json", return_value=None):
            self.assertEqual(plan_with_llm("How much money moved?", parsed, None, None), (None, []))


class VerificationTests(unittest.TestCase):
    def agent(self, rows_by_query):
        _coverage_cache._entries.clear()
        calls = list(rows_by_query)

        def execute(sql, domain, parameters, limit):
            if sql.startswith("SELECT MIN"):
                return [ONE_DAY]
            response = calls.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        return BankingAgent(execute)

    def ask(self, agent, question, role="analyst"):
        return agent.run(question, AuthUser("u", "banking", role))

    def test_a_breakdown_that_does_not_add_up_reports_no_figure(self) -> None:
        agent = self.agent([
            [{"txn_type": "DEBIT", "transaction_count": 2, "total_amount": 5.0, "record_count": 2}],
            [{"transaction_count": 9, "total_amount": 9.0, "record_count": 9}],
        ])
        result = self.ask(agent, "Show transaction volume by transaction type")
        self.assertEqual(result.status, "error")
        self.assertIn("does not add up", result.answer)
        self.assertNotIn("5", result.answer)

    def test_an_unexpected_column_reports_no_figure(self) -> None:
        agent = self.agent([[{"transaction_count": 2, "total_amount": 5.0, "record_count": 2, "account_id": "A1"}]])
        result = self.ask(agent, "Show total transaction volume")
        self.assertEqual(result.status, "error")
        self.assertNotIn("A1", result.answer)

    def test_an_impossible_percentage_reports_no_figure(self) -> None:
        agent = self.agent([[{"failure_rate_pct": 140.0, "record_count": 2}]])
        self.assertEqual(self.ask(agent, "What is the transaction failure rate?").status, "error")

    def test_no_records_is_not_reported_as_zero(self) -> None:
        agent = self.agent([[{"transaction_count": 0, "total_amount": None, "record_count": 0}]])
        result = self.ask(agent, "How many UPI transactions on 2026-03-15?")
        self.assertEqual(result.status, "no_data")
        self.assertIn("2026-03-15 to 2026-03-15", result.answer)

    def test_a_period_outside_the_data_runs_no_query(self) -> None:
        agent = self.agent([])
        self.assertEqual(self.ask(agent, "Show transaction volume for 2020").status, "no_data")

    def test_one_period_is_not_called_a_trend(self) -> None:
        agent = self.agent([
            [{"period": "2026-03", "transaction_count": 2, "total_amount": 5.0, "record_count": 2}],
            [{"transaction_count": 2, "total_amount": 5.0, "record_count": 2}],
        ])
        result = self.ask(agent, "Show the monthly trend of transaction volume")
        self.assertEqual(result.status, "insufficient_history")
        self.assertIn("only one month", result.answer)

    def test_warehouse_failures_are_named_and_report_no_figure(self) -> None:
        for message, words in (
            ("Query cancelled because of STATEMENT_TIMEOUT", "timed out"),
            ("[INSUFFICIENT_PERMISSIONS] denied", "denied permission"),
            ("Databricks SQL connection or query failed: reset", "could not be reached"),
            ("[TABLE_OR_VIEW_NOT_FOUND] gone", "does not exist"),
        ):
            agent = self.agent([GuardrailError(message)])
            result = self.ask(agent, "Show total transaction volume")
            self.assertEqual(result.status, "error", message)
            self.assertIn(words, result.answer)
            self.assertEqual(result.table, [])

    def test_answers_name_their_source_period_and_definition(self) -> None:
        agent = self.agent([[{"transaction_count": 2, "total_amount": 5.0, "record_count": 2}]])
        result = self.ask(agent, "Show total transaction volume")
        for words in ("banking.gold.gold_transactions", "data available 2026-03-15", "Definition:", "INR 5.00", "including FAILED"):
            self.assertIn(words, result.answer)
        self.assertEqual(result.evidence["registry_version"], "1.0.0")


def series(months: int, start: tuple[int, int] = (2020, 1), base: float = 100.0) -> list[dict]:
    rng = np.random.default_rng(7)
    rows = []
    year, month = start
    for index in range(months):
        seasonal = 30 * np.sin(2 * np.pi * index / 12)
        rows.append({"period": f"{year:04d}-{month:02d}", "v": float(base + index * 2 + seasonal + rng.normal(0, 3))})
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return rows


class ForecastingTests(unittest.TestCase):
    def test_insufficient_or_unusable_history_is_refused(self) -> None:
        with self.assertRaisesRegex(InsufficientHistoryError, "Only 1 complete"):
            assess_history([{"period": "2026-03", "v": 1.0}], "v", "2026-03-31")
        with self.assertRaisesRegex(InsufficientHistoryError, "No history"):
            assess_history([], "v", None)
        with self.assertRaisesRegex(InsufficientHistoryError, f"Only {MIN_PERIODS - 1}"):
            assess_history(series(MIN_PERIODS - 1), "v", "2030-01-31")
        rows = series(30)
        with self.assertRaisesRegex(InsufficientHistoryError, "missing month"):
            assess_history(rows[:10] + rows[12:], "v", "2030-01-31")
        with self.assertRaisesRegex(InsufficientHistoryError, "duplicate"):
            assess_history(rows + rows[-1:], "v", "2030-01-31")
        with self.assertRaisesRegex(InsufficientHistoryError, "no valid value"):
            assess_history(rows[:-1] + [{"period": rows[-1]["period"], "v": None}], "v", "2030-01-31")
        with self.assertRaisesRegex(InsufficientHistoryError, "does not vary"):
            assess_history([{**row, "v": 5.0} for row in rows], "v", "2030-01-31")

    def test_series_is_ordered_and_an_incomplete_last_month_is_left_out(self) -> None:
        rows = series(30)
        last = rows[-1]["period"]
        history = assess_history(list(reversed(rows)), "v", f"{last}-06")
        self.assertEqual(history.excluded_partial, last)
        self.assertEqual(list(history.labels), sorted(history.labels))
        self.assertEqual(len(history.labels), 29)

    def test_a_series_that_stopped_long_ago_is_not_forecast(self) -> None:
        with self.assertRaisesRegex(InsufficientHistoryError, "more than a year"):
            assess_history(series(30), "v", "2023-06-24", date(2026, 11, 2))

    def test_skill_is_measured_against_the_better_simple_baseline(self) -> None:
        rows = [{"period": row["period"], "v": 100.0 + 5 * index} for index, row in enumerate(series(36))]
        outcome = forecast_series(assess_history(rows, "v", "2030-01-31"), 1)
        self.assertEqual(outcome["baseline"], "naive")
        methods = {item["method"]: item["mae"] for item in outcome["backtest"]}
        self.assertLess(methods["naive"], methods["seasonal naive (same month last year)"])

    def test_backtest_never_trains_on_the_period_it_scores(self) -> None:
        seen = []

        def spy(train, horizon):
            seen.append(len(train))
            return np.repeat(train[-1], horizon)

        values = np.arange(40, dtype=float)
        with patch("app.services.forecasting._methods", return_value={"spy": (spy, 1)}):
            result = backtest(values, 3)
        self.assertEqual(seen, sorted(seen))
        self.assertLessEqual(seen[-1] + 3, len(values))
        self.assertEqual(result["spy"]["folds"], len(seen))
        # A naive forecast of a line errs by exactly 1, 2 and 3 steps.
        self.assertAlmostEqual(result["spy"]["mae"], 2.0)

    def test_a_seasonal_series_beats_the_naive_method_and_ets_is_compared(self) -> None:
        history = assess_history(series(72), "v", "2030-01-31")
        outcome = forecast_series(history, 3)
        methods = {item["method"]: item for item in outcome["backtest"]}
        self.assertIn("seasonal naive (same month last year)", methods)
        self.assertIn("ETS (damped trend)", methods)
        self.assertLess(methods[outcome["model"]]["mae"], methods["naive"]["mae"])
        self.assertEqual(outcome["labels"], ["2026-01", "2026-02", "2026-03"])
        self.assertEqual(len(outcome["values"]), 3)
        for value, (low, high) in zip(outcome["values"], outcome["intervals"]):
            self.assertLessEqual(low, value)
            self.assertGreaterEqual(high, value)

    def test_the_chosen_method_is_the_one_with_the_lowest_error(self) -> None:
        outcome = forecast_series(assess_history(series(40), "v", "2030-01-31"), 1)
        self.assertEqual(outcome["model"], outcome["backtest"][0]["method"])
        self.assertEqual(outcome["backtest"], sorted(outcome["backtest"], key=lambda item: item["mae"]))

    def test_wape_is_not_reported_when_actuals_sum_to_zero(self) -> None:
        values = np.array([0.0] * 30 + [1.0] * 2 + [0.0] * 14)
        results = backtest(values, 1, origins=6)
        self.assertTrue(all(item["wape_pct"] is None for item in results.values()))

    def test_an_implausible_forecast_is_withheld(self) -> None:
        history = assess_history(series(40), "v", "2030-01-31")
        with patch("app.services.forecasting._naive", side_effect=lambda train, horizon: np.repeat(train.max() * 100, horizon)):
            results = backtest(np.array(history.values), 1)
        self.assertIn("naive", results)
        with patch("app.services.forecasting.backtest", return_value={
            "seasonal naive (same month last year)": {"mae": 1.0, "wape_pct": 1.0, "folds": 2, "errors_by_step": [[1.0, 1.0]]},
        }), patch("app.services.forecasting._seasonal_naive", return_value=np.array([1e12])):
            with self.assertRaisesRegex(InsufficientHistoryError, "implausible"):
                forecast_series(history, 1)


class WorkflowTests(AskAiTestCase):
    """Through the orchestrator, as a request would run."""

    def setUp(self) -> None:
        super().setUp()
        _coverage_cache._entries.clear()
        for patcher in (
            patch.object(settings, "ask_ai_agentic_routing", True),
            patch.object(settings, "banking_agent_enabled", True),
            patch("app.services.banking_agent._today", return_value=date(2025, 7, 15)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def history_warehouse(self, months=30):
        rows = [{"period": row["period"], "amount_collected": row["v"], "instalments": 10, "record_count": 10} for row in series(months, (2023, 1))]
        fake = FakeWarehouse([rows], availability={"first_date": "2023-01-01", "last_date": "2025-06-30", "distinct_dates": 900})
        return fake

    def test_no_model_is_used_for_a_metric(self) -> None:
        fake = FakeWarehouse([[{"transaction_count": 2, "total_amount": 5.0, "record_count": 2}]], availability=ONE_DAY)
        response, _ = self.ask_ai("Show total transaction volume", warehouse=fake)
        self.assertEqual(response.status, "complete")
        self.assertEqual((self.sql_prompts, response.llm_provider), ([], None))
        self.assertEqual(response.plan["query_plan"]["metric_id"], "transaction_volume")
        self.assertEqual(fake.executed[-1][1], "banking")

    def test_follow_ups_use_the_stored_plan(self) -> None:
        conversation = uuid.uuid4()
        fake = FakeWarehouse([
            [{"total_amount": 5.0, "transaction_count": 2, "record_count": 2}],
            [{"total_amount": 3.0, "transaction_count": 1, "record_count": 1}],
        ], availability=ONE_DAY)
        self.ask_ai("Show transaction amount for UPI", warehouse=fake, conversation_id=conversation)
        self.db.flush()
        self.assertEqual(self.db.query(ConversationQueryState).count(), 1)
        response, _ = self.ask_ai("and only failed ones?", warehouse=fake, conversation_id=conversation)
        self.assertEqual(response.plan["query_plan"]["filters"], {"channel": ["UPI"], "status": ["FAILED"]})

    def test_a_business_users_forecast_waits_for_approval_and_no_model_runs(self) -> None:
        response, _ = self.ask_ai("Forecast loan repayments for next month", warehouse=self.history_warehouse())
        self.assertEqual(response.status, "awaiting_review")
        self.assertIsNone(response.prediction)
        self.assertEqual(response.hitl["status"], "pending")
        self.assertEqual(self.db.query(HitlReview).one().reason, "prediction")

    def test_insufficient_history_needs_no_approval(self) -> None:
        response, _ = self.ask_ai("Forecast loan repayments for next month", warehouse=self.history_warehouse(5))
        self.assertEqual(response.status, "insufficient_history")
        self.assertEqual(self.db.query(HitlReview).count(), 0)
        self.assertIsNone(response.prediction)

    def test_an_administrators_forecast_is_complete_and_says_it_is_a_forecast(self) -> None:
        response, _ = self.ask_ai("Forecast loan repayments for the next 2 months", user=self.admin("banking"), warehouse=self.history_warehouse())
        self.assertEqual(response.status, "complete")
        self.assertEqual(response.prediction.labels, ["2025-07", "2025-08"])
        self.assertIn("not actual data", response.answer)
        self.assertIn("Backtest", response.answer)
        self.assertEqual(response.evidence["kind"], "forecast")

    def test_refusals_and_outages_during_a_warehouse_outage(self) -> None:
        self.orchestrator._ready = False
        outage = GuardrailError("Databricks SQL connection or query failed: BAD_REQUEST: Cannot create the resource, please try again later.")
        with patch.object(self.orchestrator, "warmup", side_effect=outage):
            refused, _ = self.ask_ai("What is the NPA ratio?", warehouse=FakeWarehouse([outage]))
            self.assertEqual(refused.status, "insufficient_data")
            with self.assertRaises(HTTPException) as raised:
                self.ask_ai("Show the card CVV values")
            self.assertEqual(raised.exception.status_code, 403)
            with self.assertRaises(HTTPException) as raised:
                self.ask_ai("Show total transaction volume", warehouse=lambda *_args: (_ for _ in ()).throw(outage))
            self.assertEqual(raised.exception.status_code, 503)
            self.assertIn("could not be reached", raised.exception.detail)

    def test_approval_does_not_bypass_a_refused_request(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            self.ask_ai("Show the card CVV values", approved_review=HitlReview(audit_id="A", username="u", domain="banking", question="q", status="approved"))
        self.assertEqual(raised.exception.status_code, 403)

    def test_outside_the_registry_nothing_is_generated_by_default(self) -> None:
        fake = FakeWarehouse(availability=ONE_DAY)
        response, _ = self.ask_ai("Which weekday looks busiest for the branch teams?", warehouse=fake)
        self.assertEqual(response.status, "insufficient_data")
        self.assertEqual(self.sql_prompts, [])
        self.assertEqual(fake.executed, [])
        self.assertIn("Approved measures", response.answer)

    def test_generated_sql_that_ignores_a_named_value_is_rejected(self) -> None:
        self.llm_sql.extend(['{"sql": "SELECT avg_emi_amount FROM agg_loans_daily LIMIT 5"}'] * 2)
        with patch.object(settings, "banking_generated_sql", True), self.assertRaises(HTTPException) as raised:
            self.ask_ai("What is the weekday average for education lending?", warehouse=FakeWarehouse(availability=ONE_DAY))
        self.assertEqual(raised.exception.status_code, 422)

    @patch.object(settings, "banking_generated_sql", True)
    def test_generated_sql_is_held_to_the_parsed_policy_and_retried_once(self) -> None:
        self.llm_sql.extend([
            '{"sql": "SELECT status FROM banking.silver.transactions LIMIT 5"}',
            '{"sql": "SELECT status FROM banking.silver.transactions LIMIT 5"}',
        ])
        with self.assertRaises(HTTPException) as raised:
            self.ask_ai("Which weekday looks busiest for the branch teams?", warehouse=FakeWarehouse(availability=ONE_DAY))
        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(len(self.sql_prompts), 2)
        self.assertIn("previous query was rejected", self.sql_prompts[-1])

    def test_disabling_the_workflow_returns_to_generated_sql(self) -> None:
        self.llm_sql.append('{"sql": "SELECT SUM(total_amount) AS volume FROM agg_transactions_daily"}')
        self.llm_text.append("Volume was 5.")
        with patch.object(settings, "banking_agent_enabled", False), patch.object(settings, "ask_ai_agentic_routing", False):
            response, _ = self.ask_ai("Show total transaction volume", warehouse=FakeWarehouse([[{"volume": 5.0}]]))
        self.assertEqual(response.status, "complete")
        self.assertEqual(len(self.sql_prompts), 1)


if __name__ == "__main__":
    unittest.main()
