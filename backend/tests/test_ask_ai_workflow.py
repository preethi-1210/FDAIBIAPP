"""End-to-end tests of the Ask AI workflow (planner -> metadata -> SQL -> guardrails ->
warehouse -> TabPFN -> synthesis -> HITL -> history).

The orchestrator, planner, SQL guardrails, source/relevance validation, grounding check and
feature builder run for real. Only the external services are replaced: the LLM transport,
the warehouse driver (which records exactly what SQL ran against which domain), and the
TabPFN estimator. Set RYZE_LIVE_TESTS=1 to also run the live Databricks/LLM checks.
"""

import os
import types
import unittest
import uuid
from unittest.mock import patch

import numpy as np
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import AuthUser, create_access_token, hash_password
from app.config import settings
from app.db import Base, get_db
from app.main import fastapi_app
from app.models import AppUser, ConversationMessage, HitlReview, QueryHistory
from app.schemas import QueryResponse
from app.services.guardrail import GuardrailError
from app.services.orchestrator import Orchestrator


def _columns(*pairs: str) -> list[dict[str, str]]:
    return [{"name": name, "type": kind} for name, kind in (pair.split(":") for pair in pairs)]


SCHEMAS = {
    "banking": {
        "agg_transactions_daily": _columns("_gold_date:date", "record_count:bigint", "total_amount:double"),
        "gold_transactions": _columns("txn_id:string", "txn_timestamp:timestamp", "amount:double", "txn_type:string", "account_id:string"),
        "gold_branches": _columns("branch_id:string", "state:string", "region:string"),
    },
    "insurance": {"gold_claims": _columns("claim_id:string", "claim_date:date", "claim_status:string", "claimed_amount:double", "approved_amount:double")},
    "retail": {"gold_orders": _columns("order_id:string", "order_date:timestamp", "order_status:string", "store_id:string")},
    "finance": {"agg_accounts_daily": _columns("_gold_date:date", "record_count:bigint", "total_balance:double")},
}


class FakeWarehouse:
    """Stands in for the Databricks driver; records every statement and its domain."""

    def __init__(self, responses: list | None = None, availability: dict | None = None) -> None:
        self.responses = list(responses or [])
        self.availability = availability or {"first_value": "2026-03-15", "last_value": "2026-03-15"}
        self.executed: list[tuple[str, str | None]] = []

    def __call__(self, sql: str, domain: str | None = None, parameters=None, row_limit=None):
        self.executed.append((sql, domain))
        if sql.startswith("SELECT MIN("):
            return [self.availability]
        response = self.responses.pop(0) if self.responses else []
        if isinstance(response, Exception):
            raise response
        return response


class FakeRegressor:
    quantiles = (90.0, 100.0, 110.0)
    error: Exception | None = None
    fitted_rows = 0

    def fit(self, X, y):
        if FakeRegressor.error:
            raise FakeRegressor.error
        FakeRegressor.fitted_rows = len(X)
        return self

    def predict(self, X, output_type="mean", quantiles=None):
        return [np.array([value]) for value in FakeRegressor.quantiles]


class AskAiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.orchestrator = Orchestrator()
        self.orchestrator._ready = True
        self.retrieved: list[str] = []

        def resolve(question, domain=None, include_all_tables=False, retrieval_text=None):
            self.retrieved.append(question)
            return {"tables": SCHEMAS.get(domain, {}), "join_paths": [], "glossary": [], "rag_context": []}

        self.orchestrator.graph.resolve = resolve
        self.llm_sql: list = []
        self.llm_text: list = []
        self.planner: list = []
        self.sql_prompts: list[str] = []
        self.patches = [
            patch.object(settings, "warehouse", "databricks"),
            patch.object(settings, "databricks_domain_schema", "gold"),
            patch.object(settings, "databricks_domain_catalogs", "banking:banking,finance:banking,insurance:insurance,retail:retail,healthcare:healthcare"),
            patch.object(settings, "databricks_server_hostname", "workspace.example.com"),
            patch.object(settings, "databricks_http_path", "/sql/1.0/warehouses/test"),
            patch.object(settings, "databricks_token", "token"),
            patch.object(settings, "hitl_prediction_review", True),
            # Fallback is exercised explicitly in ProviderFallbackTests only.
            patch.object(settings, "llm_fallback_provider", ""),
            # These tests exercise SQL generation; routing has its own tests in test_agentic_platform.py.
            patch.object(settings, "ask_ai_agentic_routing", False),
            patch.object(settings, "rate_limit_query_per_minute", 0),
            patch.dict("app.llm._breakers", clear=True),
            patch.dict("app.llm._unavailable_until", clear=True),
            patch("app.llm.resolve_provider", side_effect=lambda override=None: override or "google"),
            patch("app.llm._google_message", side_effect=self._fake_llm),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        FakeRegressor.error = None
        FakeRegressor.quantiles = (90.0, 100.0, 110.0)
        # Cleanups run last-in-first-out: close the session before disposing the engine.
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)

    def _fake_llm(self, system: str, user: str, json_mode: bool = False) -> str:
        """Fake Gemini transport: answers each pipeline stage from its queue."""
        if "Planner" in system:
            return self.planner.pop(0) if self.planner else "{}"
        if "SQL Generator" in system:
            self.sql_prompts.append(user)
            item = self.llm_sql.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        if self.llm_text:
            item = self.llm_text.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        return '{"task": "regression", "target": "missing", "features": []}' if json_mode else ""

    def ask_ai(self, question, user=None, warehouse=None, **kwargs) -> tuple[QueryResponse, FakeWarehouse]:
        warehouse = warehouse or FakeWarehouse()
        with patch("app.services.orchestrator.execute_readonly", warehouse):
            response = self.orchestrator.run(self.db, question, user or self.analyst("banking"), **kwargs)
        return response, warehouse

    @staticmethod
    def analyst(domain: str) -> AuthUser:
        return AuthUser(username=f"{domain}.user", domain=domain, role="analyst")

    @staticmethod
    def admin(domain: str = "banking") -> AuthUser:
        return AuthUser(username="admin", domain=domain, role="admin")

    @staticmethod
    def engines(response: QueryResponse, stage: str) -> list[str]:
        return [step["engine"] for step in response.trace if step["stage"] == stage]


class FunctionalTests(AskAiTestCase):
    def test_retrieval_runs_validated_sql_on_the_domain_catalog_and_answers_from_its_result(self) -> None:
        sql = (
            "SELECT SUM(total_amount) AS transaction_volume, SUM(record_count) AS transaction_count "
            "FROM agg_transactions_daily WHERE _gold_date BETWEEN '2026-03-01' AND '2026-03-31'"
        )
        self.llm_sql.append(f'{{"sql": "{sql}"}}')
        self.llm_text.append("Transaction volume for March 2026 was 251,739,333.53 across 99,981 transactions.")
        response, warehouse = self.ask_ai(
            "Show the total transaction volume for March 2026",
            warehouse=FakeWarehouse([[{"transaction_volume": 251739333.53, "transaction_count": 99981}]]),
        )
        self.assertEqual(response.status, "complete")
        self.assertEqual(warehouse.executed[0][1], "banking")
        self.assertTrue(warehouse.executed[0][0].startswith(sql))
        self.assertEqual(response.sql, sql)
        self.assertIn("251,739,333.53", response.answer)
        self.assertTrue(self.engines(response, "sql_generation")[-1].startswith("google:"))
        self.assertEqual(response.llm_provider, "google")
        self.assertTrue(self.db.query(QueryHistory).one().guardrail_ok)

    def test_period_comparison_reports_difference_and_percentage_from_the_rows(self) -> None:
        self.llm_sql.append('{"sql": "SELECT date_format(_gold_date, \'yyyy-MM\') AS month, SUM(total_amount) AS volume FROM agg_transactions_daily GROUP BY 1 ORDER BY 1"}')
        self.llm_text.append("Volume fell from 200.00 in 2026-02 to 150.00 in 2026-03, a decrease of 50.00 (-25.0%).")
        response, _ = self.ask_ai(
            "Compare transaction volume for March 2026 with February 2026",
            warehouse=FakeWarehouse([[{"month": "2026-02", "volume": 200.0}, {"month": "2026-03", "volume": 150.0}]]),
        )
        self.assertEqual(response.status, "complete")
        self.assertIn("-25.0%", response.answer)

    def test_top_transaction_types_question_returns_ranked_rows(self) -> None:
        self.llm_sql.append('{"sql": "SELECT t.txn_type, SUM(t.amount) AS volume FROM gold_transactions t GROUP BY t.txn_type ORDER BY volume DESC LIMIT 5"}')
        self.llm_text.append("The top transaction type is UPI with 900.00, followed by NEFT with 500.00.")
        rows = [{"txn_type": "UPI", "volume": 900.0}, {"txn_type": "NEFT", "volume": 500.0}]
        response, _ = self.ask_ai("Show the top transaction types", warehouse=FakeWarehouse([rows]))
        self.assertEqual(response.table, rows)
        self.assertIn("UPI", response.answer)

    def test_joins_on_restricted_identifiers_are_blocked_for_business_users(self) -> None:
        self.llm_sql.append('{"sql": "SELECT b.region, SUM(t.amount) AS volume FROM gold_transactions t JOIN gold_branches b ON b.branch_id = t.account_id GROUP BY b.region"}')
        with self.assertRaises(HTTPException) as raised:
            self.ask_ai("Show transaction volume by region")
        self.assertEqual(raised.exception.status_code, 403)

    def test_each_domain_queries_only_its_own_catalog(self) -> None:
        cases = {
            "insurance": ("Summarize claims by status", "SELECT claim_status, SUM(claimed_amount) AS claimed FROM gold_claims GROUP BY claim_status"),
            "retail": ("How many orders by status", "SELECT order_status, COUNT(order_id) AS orders FROM gold_orders GROUP BY order_status"),
        }
        for domain, (question, sql) in cases.items():
            with self.subTest(domain=domain):
                self.llm_sql.append(f'{{"sql": "{sql}"}}')
                self.llm_text.append("Results are shown in the table.")
                response, warehouse = self.ask_ai(question, user=self.analyst(domain), warehouse=FakeWarehouse([[{"label": "A", "value": 1}]]))
                self.assertEqual([domain for _, domain in warehouse.executed], [domain])
                self.assertIn(f"{domain}.gold", self.engines(response, "sql_execution")[0])

    def test_a_missing_table_is_refused_before_any_query_runs(self) -> None:
        self.llm_sql.append('{"sql": "SELECT SUM(amount) AS total FROM loan_writeoffs"}')
        with self.assertRaises(HTTPException) as raised:
            self.ask_ai("Show total loan write-offs")
        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn("No query was run", raised.exception.detail)

    def test_an_unknown_column_is_refused_against_metadata_before_execution(self) -> None:
        self.llm_sql.append('{"sql": "SELECT SUM(t.fee_amount) AS fees FROM gold_transactions t"}')
        warehouse = FakeWarehouse()
        with self.assertRaises(HTTPException) as raised:
            self.ask_ai("Show total transaction fees", warehouse=warehouse)
        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn("t.fee_amount", raised.exception.detail)
        self.assertEqual(warehouse.executed, [])

    def test_a_warehouse_execution_error_is_reported_without_a_fabricated_answer(self) -> None:
        self.llm_sql.append('{"sql": "SELECT SUM(total_amount) AS volume FROM agg_transactions_daily"}')
        warehouse = FakeWarehouse([GuardrailError("[PERMISSION_DENIED] User does not have SELECT on agg_transactions_daily")])
        response, _ = self.ask_ai("Show total transaction volume", warehouse=warehouse)
        self.assertEqual(response.status, "error")
        self.assertIn("PERMISSION_DENIED", response.answer)
        self.assertEqual(self.engines(response, "synthesis"), [])
        self.assertFalse(self.db.query(QueryHistory).one().guardrail_ok)

    def test_a_follow_up_is_rewritten_with_conversation_context(self) -> None:
        context = [
            {"role": "user", "content": "Show the total transaction volume for March 2026"},
            {"role": "assistant", "content": "Transaction volume for March 2026 was 150.00."},
        ]
        self.planner.append('{"intent": "comparison", "is_prediction": false, "standalone_question": "Compare total transaction volume for March 2026 with February 2026", "needs_clarification": false}')
        self.llm_sql.append('{"sql": "SELECT date_format(_gold_date, \'yyyy-MM\') AS month, SUM(total_amount) AS volume FROM agg_transactions_daily GROUP BY 1"}')
        self.llm_text.append("March 2026 volume was 150.00 versus 200.00 in February 2026.")
        response, _ = self.ask_ai(
            "Compare it with the month before that",
            conversation_context=context,
            warehouse=FakeWarehouse([[{"month": "2026-02", "volume": 200.0}, {"month": "2026-03", "volume": 150.0}]]),
        )
        self.assertEqual(response.plan["planner"], "llm")
        self.assertIn("February 2026", self.retrieved[-1])
        self.assertIn("Current question: Compare total transaction volume for March 2026 with February 2026", self.sql_prompts[-1])
        self.assertIn("Show the total transaction volume for March 2026", self.sql_prompts[-1])
        self.assertEqual(response.status, "complete")

    def test_an_unresolvable_follow_up_asks_for_clarification_without_querying(self) -> None:
        self.planner.append('{"intent": "clarification", "is_prediction": false, "standalone_question": "that", "needs_clarification": true, "clarification_question": "Which metric do you mean?"}')
        response, warehouse = self.ask_ai("and that?", conversation_context=[{"role": "user", "content": "hello"}])
        self.assertEqual(response.status, "needs_clarification")
        self.assertEqual(response.answer, "Which metric do you mean?")
        self.assertEqual(warehouse.executed, [])


class PredictionTests(AskAiTestCase):
    months = [f"2025-{m:02d}" for m in range(1, 13)]

    def history(self, periods: int) -> list[dict]:
        return [{"month": month, "total_balance": 1000.0 + 10 * i} for i, month in enumerate(self.months[:periods])]

    def forecast_run(self, user, periods=12):
        self.llm_sql.append('{"sql": "SELECT date_format(_gold_date, \'yyyy-MM\') AS month, SUM(total_balance) AS total_balance FROM agg_accounts_daily GROUP BY 1 ORDER BY 1"}')
        with patch("tabpfn.TabPFNRegressor", FakeRegressor):
            return self.ask_ai(
                "Predict the total balance for next month",
                user=user,
                warehouse=FakeWarehouse([self.history(periods)]),
            )

    def test_an_administrator_forecast_runs_tabpfn_on_history_and_labels_it_as_a_prediction(self) -> None:
        response, _ = self.forecast_run(self.admin("finance"))
        self.assertEqual(response.status, "complete")
        self.assertEqual(response.prediction.type, "forecast")
        self.assertEqual(response.prediction.labels, ["2026-01"])
        self.assertEqual(response.prediction.values, [100.0])
        self.assertEqual(response.uncertainty.confidence_range, [90.0, 110.0])
        self.assertEqual(FakeRegressor.fitted_rows, 10)  # 12 periods minus the two lag rows
        self.assertIn("not actual data", response.answer)
        self.assertIn("latest actual value was 1,110.00 (2025-12)", response.answer)
        self.assertIn("self-hosted TabPFNRegressor", self.engines(response, "prediction")[0])

    def test_insufficient_history_is_reported_and_the_model_is_not_run(self) -> None:
        FakeRegressor.fitted_rows = 0
        response, _ = self.forecast_run(self.admin("finance"), periods=4)
        self.assertEqual(response.status, "insufficient_data")
        self.assertIsNone(response.prediction)
        self.assertIn("Only 4 period(s)", response.answer)
        self.assertEqual(FakeRegressor.fitted_rows, 0)

    def test_unavailable_model_weights_produce_no_prediction(self) -> None:
        FakeRegressor.error = OSError("LocalEntryNotFoundError: tabpfn-v2.5-regressor.ckpt not found on huggingface")
        with patch.object(settings, "prediction_engine", "tabpfn"):
            response, _ = self.forecast_run(self.admin("finance"))
        self.assertEqual(response.status, "error")
        self.assertIsNone(response.prediction)
        self.assertIn("No prediction was produced", response.answer)

    def test_historical_churn_rate_is_not_routed_to_the_model(self) -> None:
        self.llm_sql.append('{"sql": "SELECT SUM(record_count) AS closed_accounts FROM agg_transactions_daily"}')
        self.llm_text.append("Results are shown in the table.")
        response, _ = self.ask_ai("Show the churn rate by month", warehouse=FakeWarehouse([[{"closed_accounts": 5}]]))
        self.assertFalse(response.plan["is_prediction"])


class LlmIntegrationTests(AskAiTestCase):
    def test_ollama_handles_the_request_when_selected(self) -> None:
        calls = []

        class FakeCompletions:
            def create(self, **kwargs):
                calls.append(kwargs["model"])
                content = (
                    '{"sql": "SELECT SUM(total_amount) AS volume FROM agg_transactions_daily"}'
                    if "SQL Generator" in kwargs["messages"][0]["content"]
                    else "Total volume was 42.00."
                )
                return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content))])

        client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=FakeCompletions()))
        with patch("app.llm._client", return_value=(client, "llama3.2:latest")):
            response, _ = self.ask_ai(
                "Show total transaction volume",
                llm_override="ollama",
                warehouse=FakeWarehouse([[{"volume": 42.0}]]),
            )
        self.assertEqual(response.llm_provider, "ollama")
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(engine.startswith("ollama") for engine in self.engines(response, "synthesis")))

    def test_multi_line_sql_in_an_ollama_json_reply_is_accepted(self) -> None:
        # Ollama often returns SQL with raw line breaks inside the JSON string, which strict
        # JSON parsing rejected as "Invalid control character".
        class FakeCompletions:
            def create(self, **kwargs):
                content = (
                    # A real line break inside the JSON string value, as Ollama returns it.
                    '{"sql": "SELECT SUM(total_amount) AS volume\n         FROM agg_transactions_daily"}'
                    if "SQL Generator" in kwargs["messages"][0]["content"]
                    else "Total volume was 42.00."
                )
                return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content))])

        client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=FakeCompletions()))
        with patch("app.llm._client", return_value=(client, "llama3.2:latest")):
            response, warehouse = self.ask_ai(
                "Show total transaction volume", llm_override="ollama", warehouse=FakeWarehouse([[{"volume": 42.0}]])
            )
        self.assertEqual(response.status, "complete")
        self.assertIn("\n", response.sql)
        self.assertEqual(warehouse.executed[0][1], "banking")

    def test_gemini_failure_never_yields_a_fabricated_answer(self) -> None:
        self.llm_sql.append(TimeoutError("deadline exceeded"))
        warehouse = FakeWarehouse()
        with self.assertRaises(HTTPException) as raised:
            with patch("app.services.orchestrator.execute_readonly", warehouse):
                self.orchestrator.run(self.db, "Show average transaction amount by channel", self.analyst("banking"))
        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(warehouse.executed, [])

    def test_a_failed_narrative_falls_back_to_the_actual_rows_and_is_traced(self) -> None:
        self.llm_sql.append('{"sql": "SELECT SUM(total_amount) AS volume FROM agg_transactions_daily"}')
        self.llm_text.append(TimeoutError("deadline exceeded"))
        response, _ = self.ask_ai("Show total transaction volume", warehouse=FakeWarehouse([[{"volume": 42.0}]]))
        self.assertEqual(response.status, "complete")
        self.assertIn("Returned 1 rows", response.answer)
        self.assertFalse([s for s in response.trace if s["stage"] == "synthesis" and s["engine"].startswith("google")][0]["ok"])

    def test_a_template_query_is_not_credited_to_an_llm(self) -> None:
        response, _ = self.ask_ai(
            "Show transaction volume for last week",
            warehouse=FakeWarehouse([[{"transaction_count": 3, "transaction_volume": 30.0}]]),
        )
        self.assertEqual(self.engines(response, "sql_generation"), ["verified query template"])


class ProviderFallbackTests(AskAiTestCase):
    def ollama_client(self, calls: list):
        class FakeCompletions:
            def create(self, **kwargs):
                calls.append(kwargs["model"])
                content = (
                    '{"sql": "SELECT SUM(total_amount) AS volume FROM agg_transactions_daily"}'
                    if "SQL Generator" in kwargs["messages"][0]["content"]
                    else "Total volume was 42.00."
                )
                return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content))])

        return types.SimpleNamespace(chat=types.SimpleNamespace(completions=FakeCompletions()))

    def test_an_exhausted_gemini_quota_falls_back_to_ollama_and_is_traced(self) -> None:
        quota = RuntimeError("ClientError: 429 RESOURCE_EXHAUSTED. You exceeded your current quota")
        self.llm_sql.append(quota)
        self.llm_text.append(quota)
        calls: list = []
        with (
            patch.object(settings, "llm_fallback_provider", "ollama"),
            patch("app.llm._client", return_value=(self.ollama_client(calls), "llama3.2:latest")),
        ):
            response, warehouse = self.ask_ai("Show total transaction volume", warehouse=FakeWarehouse([[{"volume": 42.0}]]))
            google_calls_before = len(self.sql_prompts)
            # Within the cooldown the failed provider is skipped instead of being called again.
            second, _ = self.ask_ai("Show total transaction volume", warehouse=FakeWarehouse([[{"volume": 42.0}]]))
        self.assertEqual(response.status, "complete")
        self.assertEqual(response.llm_provider, "ollama")
        self.assertEqual(warehouse.executed[0][1], "banking")
        sql_steps = [step for step in response.trace if step["stage"] == "sql_generation"]
        self.assertEqual([(step["engine"].split(":")[0], step["ok"]) for step in sql_steps], [("google", False), ("ollama", True)])
        self.assertIn("RESOURCE_EXHAUSTED", sql_steps[0]["detail"])
        self.assertEqual(len(self.sql_prompts), google_calls_before)
        self.assertIn("Skipped", [s for s in second.trace if s["stage"] == "sql_generation"][0]["detail"])
        self.assertEqual(second.llm_provider, "ollama")

    def test_when_both_providers_fail_nothing_is_answered_or_queried(self) -> None:
        self.llm_sql.append(RuntimeError("429 RESOURCE_EXHAUSTED"))

        class DownCompletions:
            def create(self, **kwargs):
                raise ConnectionError("Ollama is not running")

        client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=DownCompletions()))
        warehouse = FakeWarehouse()
        with (
            patch.object(settings, "llm_fallback_provider", "ollama"),
            patch("app.llm._client", return_value=(client, "llama3.2:latest")),
            patch("app.llm.time.sleep"),
        ):
            with self.assertRaises(HTTPException) as raised:
                self.ask_ai("Show average transaction amount by channel", warehouse=warehouse)
        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn("google and ollama", raised.exception.detail)
        self.assertEqual(warehouse.executed, [])

    def test_questions_about_the_assistant_are_answered_without_data_or_an_llm(self) -> None:
        response, warehouse = self.ask_ai("What is this application about?")
        self.assertEqual(response.status, "complete")
        self.assertIsNone(response.sql)
        self.assertEqual(warehouse.executed, [])
        self.assertIn("governed business assistant", response.answer)
        self.assertIsNone(response.llm_provider)


class GuardrailTests(AskAiTestCase):
    def test_an_empty_period_is_reported_with_source_coverage_not_as_zero(self) -> None:
        response, warehouse = self.ask_ai(
            "Show transaction volume for last week",
            warehouse=FakeWarehouse([[{"transaction_count": 0, "transaction_volume": 0.0}]]),
        )
        self.assertEqual(response.status, "no_data")
        self.assertIn("matched no records", response.answer)
        self.assertIn("covers 2026-03-15 to 2026-03-15", response.answer)
        self.assertNotIn("0.0", response.answer)
        self.assertTrue(warehouse.executed[1][0].startswith("SELECT MIN(_gold_date)"))

    def test_txn_timestamp_inside_extract_is_validated_and_executed_on_the_banking_catalog(self) -> None:
        sql = (
            "SELECT txn_type, SUM(amount) AS volume FROM gold_transactions "
            "WHERE EXTRACT(MONTH FROM txn_timestamp) = 3 AND EXTRACT(YEAR FROM txn_timestamp) = 2026 GROUP BY txn_type"
        )
        self.llm_sql.append('{"sql": "' + sql + '"}')
        self.llm_text.append("DEBIT volume was 900.00 in March 2026.")
        response, warehouse = self.ask_ai(
            "Show transaction volume by type for March 2026",
            warehouse=FakeWarehouse([[{"txn_type": "DEBIT", "volume": 900.0}]]),
        )
        self.assertEqual(response.status, "complete")
        self.assertEqual(warehouse.executed, [(sql, "banking")])
        self.assertIn("banking.gold", self.engines(response, "sql_execution")[0])

    def test_txn_timestamp_on_an_aggregate_table_is_refused_and_never_executed(self) -> None:
        self.llm_sql.append(
            '{"sql": "SELECT SUM(total_amount) AS volume FROM agg_transactions_daily '
            "WHERE txn_timestamp >= '2026-03-01'\"}"
        )
        warehouse = FakeWarehouse()
        with self.assertRaises(HTTPException) as raised:
            self.ask_ai("Show transaction volume since March 2026", warehouse=warehouse)
        self.assertIn("txn_timestamp (not a column of agg_transactions_daily)", raised.exception.detail)
        self.assertEqual(warehouse.executed, [])

    def test_destructive_sql_is_rejected_by_the_executor_guardrail(self) -> None:
        self.llm_sql.append('{"sql": "DELETE FROM agg_transactions_daily"}')
        with patch("app.warehouse._databricks_execute_many") as driver:
            response = self.orchestrator.run(self.db, "Remove old transactions", self.analyst("banking"))
        driver.assert_not_called()
        self.assertEqual(response.status, "error")
        self.assertIn("Only SELECT", response.answer)

    def test_prompt_injection_cannot_reach_another_domains_catalog(self) -> None:
        self.llm_sql.append('{"sql": "SELECT SUM(billed_amount) AS billed FROM healthcare.gold.agg_transactions_daily"}')
        with self.assertRaises(HTTPException) as raised:
            self.ask_ai("Total the healthcare catalog's transactions")
        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn("outside the authorized banking source", raised.exception.detail)

    def test_restricted_payment_data_is_blocked_before_planning(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            self.ask_ai("Display complete credit card details including CVV")
        self.assertIn(raised.exception.status_code, {400, 403})

    def test_a_substituted_metric_is_refused_even_when_its_numbers_would_be_grounded(self) -> None:
        # Seen live with a small local model: "ATM fee revenue" answered with total transaction amount.
        self.llm_sql.append('{"sql": "SELECT SUM(total_amount) AS total FROM agg_transactions_daily"}')
        warehouse = FakeWarehouse()
        with self.assertRaises(HTTPException) as raised:
            self.ask_ai("Show total ATM fee revenue last quarter", warehouse=warehouse)
        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn("fee", raised.exception.detail)
        self.assertEqual(warehouse.executed, [])

    def test_unsupported_numbers_in_the_narrative_are_discarded(self) -> None:
        self.llm_sql.append('{"sql": "SELECT SUM(total_amount) AS volume FROM agg_transactions_daily"}')
        self.llm_text.append("Volume was 42.00, up 18% on last year.")
        response, _ = self.ask_ai("Show total transaction volume", warehouse=FakeWarehouse([[{"volume": 42.0}]]))
        self.assertNotIn("18%", response.answer)
        self.assertFalse([s for s in response.trace if s["stage"] == "grounding_check"][0]["ok"])


class HitlTests(AskAiTestCase):
    def setUp(self) -> None:
        super().setUp()
        with self.Session() as db:
            db.add_all([
                AppUser(username="finance.user", password_hash=hash_password("x" * 12), domain="finance", display_name="F"),
                AppUser(username="admin", password_hash=hash_password("x" * 12), domain="all", display_name="A"),
            ])
            db.commit()

        def override_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        fastapi_app.dependency_overrides[get_db] = override_db
        self.addCleanup(fastapi_app.dependency_overrides.clear)
        self.client = TestClient(fastapi_app)

    def headers(self, username, domain, role="analyst"):
        return {"Authorization": f"Bearer {create_access_token(username, domain, role)}"}

    def test_a_business_user_prediction_waits_for_another_administrator(self) -> None:
        conversation_id = uuid.uuid4()
        self.llm_sql.append('{"sql": "SELECT date_format(_gold_date, \'yyyy-MM\') AS month, SUM(total_balance) AS total_balance FROM agg_accounts_daily GROUP BY 1"}')
        with patch("tabpfn.TabPFNRegressor", FakeRegressor):
            FakeRegressor.fitted_rows = 0
            response, _ = self.ask_ai(
                "Predict the total balance for next month",
                user=self.analyst("finance"),
                warehouse=FakeWarehouse([PredictionTests.history(PredictionTests, 12)]),
                conversation_id=conversation_id,
            )
        self.assertEqual(response.status, "awaiting_review")
        self.assertIsNone(response.prediction)
        self.assertEqual(FakeRegressor.fitted_rows, 0)
        review_id = response.hitl["review_id"]

        user = self.headers("finance.user", "finance")
        admin = self.headers("admin", "finance", "admin")
        self.assertEqual(self.client.post(f"/hitl/{review_id}/resume", headers=user).status_code, 409)
        self.assertEqual(self.client.post(f"/hitl/{review_id}/approve", headers=user).status_code, 403)
        approved = self.client.post(f"/hitl/{review_id}/approve", json={"note": "ok"}, headers=admin)
        self.assertEqual(approved.json()["reviewed_by"], "admin")

        self.llm_sql.append('{"sql": "SELECT date_format(_gold_date, \'yyyy-MM\') AS month, SUM(total_balance) AS total_balance FROM agg_accounts_daily GROUP BY 1"}')
        with (
            patch("tabpfn.TabPFNRegressor", FakeRegressor),
            patch("app.main.orchestrator", self.orchestrator),
            patch("app.services.orchestrator.execute_readonly", FakeWarehouse([PredictionTests.history(PredictionTests, 12)])),
        ):
            resumed = self.client.post(f"/hitl/{review_id}/resume", headers=user)
        self.assertEqual(resumed.status_code, 200, resumed.text)
        self.assertEqual(resumed.json()["prediction"]["type"], "forecast")
        self.assertEqual(resumed.json()["hitl"]["reviewed_by"], "admin")
        self.assertEqual(self.client.post(f"/hitl/{review_id}/resume", headers=user).status_code, 409)
        with self.Session() as db:
            review = db.get(HitlReview, review_id)
            self.assertEqual((review.status, review.reviewed_by, review.decision_note), ("completed", "admin", "ok"))
            self.assertEqual(review.conversation_id, str(conversation_id))

    def test_an_administrator_cannot_approve_their_own_request(self) -> None:
        with self.Session() as db:
            db.add(HitlReview(audit_id="AU-1", username="admin", domain="finance", question="q", status="pending"))
            db.commit()
        response = self.client.post("/hitl/1/approve", headers=self.headers("admin", "finance", "admin"))
        self.assertEqual(response.status_code, 403)

    def test_business_users_only_see_their_own_reviews(self) -> None:
        with self.Session() as db:
            db.add_all([
                HitlReview(audit_id="AU-1", username="finance.user", domain="finance", question="mine"),
                HitlReview(audit_id="AU-2", username="other.user", domain="finance", question="theirs"),
            ])
            db.commit()
        rows = self.client.get("/hitl", headers=self.headers("finance.user", "finance")).json()
        self.assertEqual([row["question"] for row in rows], ["mine"])


class ConversationTests(AskAiTestCase):
    def setUp(self) -> None:
        super().setUp()
        with self.Session() as db:
            db.add_all([
                AppUser(username="banking.user", password_hash=hash_password("x" * 12), domain="banking", display_name="B"),
                AppUser(username="other.user", password_hash=hash_password("x" * 12), domain="banking", display_name="O"),
            ])
            db.commit()

        def override_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        fastapi_app.dependency_overrides[get_db] = override_db
        self.addCleanup(fastapi_app.dependency_overrides.clear)
        self.client = TestClient(fastapi_app)
        self.contexts: list = []

        def fake_run(db, question, user, llm_provider=None, conversation_context=None, conversation_id=None, **kwargs):
            self.contexts.append(conversation_context)
            if question == "fail":
                raise HTTPException(status_code=422, detail="No query was run.")
            return QueryResponse(answer=f"answer to {question}", conversation_id=conversation_id)

        patcher = patch("app.main.orchestrator.run", side_effect=fake_run)
        patcher.start()
        self.addCleanup(patcher.stop)

    def ask(self, question, conversation_id=None, username="banking.user"):
        return self.client.post(
            "/query",
            json={"question": question, "conversation_id": conversation_id},
            headers={"Authorization": f"Bearer {create_access_token(username, 'banking', 'analyst')}"},
        )

    def test_new_conversations_get_unique_ids_and_follow_ups_receive_context(self) -> None:
        first = self.ask("Show volume for March").json()["conversation_id"]
        second = self.ask("Show volume for April").json()["conversation_id"]
        self.assertNotEqual(first, second)
        self.ask("Compare it with February", first)
        self.assertEqual(self.contexts[-1][0]["content"], "Show volume for March")
        self.assertEqual(self.contexts[-1][1]["content"], "answer to Show volume for March")

    def test_another_users_conversation_cannot_be_continued(self) -> None:
        first = self.ask("Show volume for March").json()["conversation_id"]
        self.assertEqual(self.ask("follow up", first, username="other.user").status_code, 404)

    def test_failures_are_reported_and_not_stored_as_successful_turns(self) -> None:
        response = self.ask("fail")
        self.assertEqual(response.status_code, 422)
        with self.Session() as db:
            self.assertEqual(db.query(ConversationMessage).count(), 0)

    def test_an_unauthorized_domain_token_cannot_query(self) -> None:
        response = self.client.post(
            "/query",
            json={"question": "Show healthcare claims"},
            headers={"Authorization": f"Bearer {create_access_token('banking.user', 'healthcare', 'analyst')}"},
        )
        self.assertEqual(response.status_code, 403)


@unittest.skipUnless(os.getenv("RYZE_LIVE_TESTS") == "1", "set RYZE_LIVE_TESTS=1 to run against the configured Databricks/LLM")
class LiveDatabricksTests(unittest.TestCase):
    """Read-only checks against the configured SQL Warehouse and Unity Catalog."""

    def test_warehouse_and_catalog_discovery(self) -> None:
        from app.warehouse import execute_databricks_domain_batch, test_warehouse_connection, warehouse_schema_columns

        self.assertTrue(test_warehouse_connection("banking")["connected"])
        schema = warehouse_schema_columns("banking")
        self.assertIn("agg_transactions_daily", schema)
        identity = execute_databricks_domain_batch("banking", ["SELECT current_catalog() AS c, current_schema() AS s"])[0][0]
        self.assertEqual((identity["c"], identity["s"]), ("banking", settings.databricks_domain_schema))

    def test_generated_answer_matches_a_direct_warehouse_query(self) -> None:
        from app.db import SessionLocal
        from app.main import orchestrator
        from app.warehouse import execute_databricks_domain_batch

        db = SessionLocal()
        try:
            response = orchestrator.run(
                db, "Show total transaction volume for March 2026", AuthUser("live.test", "banking", "analyst"),
                persist_history=False,
            )
        finally:
            db.close()
        direct = execute_databricks_domain_batch("banking", [response.sql])[0]
        self.assertEqual(response.table, direct)
        self.assertEqual(response.status, "complete")


if __name__ == "__main__":
    unittest.main()
