"""Model governance and lineage report what a request actually ran, read from its recorded trace."""

import unittest
import uuid
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import AuthUser
from app.config import settings
from app.db import Base
from app.governance import build_lineage, execution_summary, model_gov
from app.services.orchestrator import Orchestrator

REGISTRY_SQL = "SELECT COUNT(*) AS account_count FROM gold_accounts a WHERE a.status IN (:f_account_status_0) LIMIT 1"
GENERATED_SQL = "SELECT COUNT(*) AS trades FROM gold_trades LIMIT 1"


def step(stage: str, engine: str, ok: bool = True, detail: str | None = None) -> dict:
    return {"stage": stage, "engine": engine, "ok": ok, "detail": detail, "latency_ms": None}


# The trace the banking registry recorded for "How many active accounts do we have?".
REGISTRY_TRACE = [
    step("context", "banking business context 1.0.0"),
    step("router", "banking value (rules)"),
    step("sql_validation", "parsed SQL policy: read-only, approved tables, bounded, no identifiers returned"),
    step("sql_execution", "banking.gold"),
    step("result_validation", "shape, range and reconciliation checks"),
    step("business_rules", "banking business rules"),
]
# The trace the general path recorded for "How many trades were made?" in Finance.
GENERATED_TRACE = [
    step("planner", "deterministic classifier"),
    step("router", "metric"),
    step("tool", "metrics.calculate@1"),
    step("sql_generation", "google:gemini-3.5-flash"),
    step("sql_validation", "read-only, authorized sources and columns, sensitive fields"),
    step("sql_execution", "Databricks SQL Warehouse finance.gold"),
    step("synthesis", "google:gemini-3.5-flash"),
    step("grounding_check", "numeric claims verified against query result"),
]
PROVIDER_WORDS = ("google", "gemini", "ollama", "openai", "anthropic", "grok", "heuristic fallback")


class ExecutionSummaryTests(unittest.TestCase):
    def test_registry_answer_reports_no_ai_model(self) -> None:
        summary = execution_summary(REGISTRY_TRACE, REGISTRY_SQL, {"kind": "metric"})
        self.assertEqual(summary["answer_generated_by"], "registry")
        self.assertEqual(summary["sql_origin"], "registry")
        self.assertEqual(summary["ai_model_calls"], {})
        self.assertFalse(summary["fallback_used"])
        self.assertFalse(summary["external_ai_service_called"])
        gov = model_gov(None, summary)
        self.assertEqual(gov["answer_generated_by"], "approved metric registry (template)")
        self.assertEqual(gov["sql_origin"], "approved registry template")
        self.assertEqual(gov["ai_model_calls"], "none")
        self.assertEqual(gov["prediction_runtime"], "not invoked")
        self.assertFalse(gov["external_forecasting_model"])
        self.assertNotIn("llm", gov)
        self.assertNotIn("external_ml_api", gov)

    def test_ai_generated_answer_reports_each_real_call(self) -> None:
        summary = execution_summary(GENERATED_TRACE, GENERATED_SQL, None)
        self.assertEqual(summary["answer_generated_by"], "ai")
        self.assertEqual(summary["sql_origin"], "ai")
        self.assertEqual(summary["ai_model_calls"], {
            "sql_generation": {"succeeded": 1, "failed": 0, "skipped": 0},
            "synthesis": {"succeeded": 1, "failed": 0, "skipped": 0},
        })
        self.assertFalse(summary["fallback_used"])
        self.assertTrue(summary["external_ai_service_called"])
        gov = model_gov(None, summary)
        self.assertEqual(gov["answer_generated_by"], "AI model")
        self.assertEqual(gov["sql_origin"], "AI-generated, then validated")
        self.assertEqual(gov["ai_model_calls"], "sql generation: 1 succeeded; synthesis: 1 succeeded")
        # Governance names stages and counts, never providers or models.
        for word in PROVIDER_WORDS:
            self.assertNotIn(word, str(gov).lower())

    def test_fallback_after_a_failed_provider_is_reported(self) -> None:
        trace = [
            step("sql_generation", "google:gemini-3.5-flash", ok=False, detail="quota exhausted"),
            step("sql_generation", "ollama:qwen3"),
            step("sql_execution", "Databricks SQL Warehouse finance.gold"),
            step("synthesis", "deterministic summary of the query result"),
        ]
        with patch.object(settings, "ollama_base_url", "http://127.0.0.1:11434/v1"):
            summary = execution_summary(trace, GENERATED_SQL, None)
        self.assertTrue(summary["fallback_used"])
        self.assertEqual(summary["ai_model_calls"], {"sql_generation": {"succeeded": 1, "failed": 1, "skipped": 0}})
        self.assertEqual(summary["sql_origin"], "ai")
        self.assertEqual(summary["answer_generated_by"], "rule_based")
        # The failed attempt still reached the external provider.
        self.assertTrue(summary["external_ai_service_called"])
        self.assertEqual(model_gov(None, summary)["ai_model_calls"], "sql generation: 1 succeeded, 1 failed")

    def test_a_skipped_provider_is_not_a_call_but_still_a_fallback(self) -> None:
        trace = [
            step("synthesis", "google:gemini-3.5-flash", ok=False, detail="Skipped: google recently reported a quota failure; using fallback ollama."),
            step("synthesis", "ollama:qwen3"),
        ]
        with patch.object(settings, "ollama_base_url", "http://localhost:11434/v1"):
            summary = execution_summary(trace, None, {"kind": "none"})
        self.assertTrue(summary["fallback_used"])
        self.assertEqual(summary["ai_model_calls"], {"synthesis": {"succeeded": 1, "failed": 0, "skipped": 1}})
        self.assertFalse(summary["external_ai_service_called"])
        with patch.object(settings, "ollama_base_url", "https://ollama.example.com/v1"):
            self.assertTrue(execution_summary(trace, None, {"kind": "none"})["external_ai_service_called"])

    def test_a_retry_on_the_same_provider_is_not_a_fallback(self) -> None:
        trace = [step("planner", "google:gemini-3.5-flash", ok=False), step("planner", "google:gemini-3.5-flash")]
        self.assertFalse(execution_summary(trace, None, None)["fallback_used"])

    def test_a_discarded_ai_answer_is_not_credited_to_the_model(self) -> None:
        trace = [
            *GENERATED_TRACE[:6],
            step("synthesis", "google:gemini-3.5-flash"),
            step("grounding_check", "numeric claims verified against query result", ok=False),
            step("synthesis", "deterministic summary of the query result"),
        ]
        summary = execution_summary(trace, GENERATED_SQL, None)
        self.assertEqual(summary["answer_generated_by"], "rule_based")
        self.assertEqual(summary["ai_model_calls"]["synthesis"]["succeeded"], 1)

    def test_ai_planning_with_a_registry_answer_keeps_the_template_origin(self) -> None:
        trace = [step("planner", "google:gemini-3.5-flash"), *REGISTRY_TRACE]
        summary = execution_summary(trace, REGISTRY_SQL, {"kind": "metric"})
        self.assertEqual(summary["answer_generated_by"], "registry")
        self.assertEqual(summary["sql_origin"], "registry")
        self.assertEqual(summary["ai_model_calls"], {"planner": {"succeeded": 1, "failed": 0, "skipped": 0}})

    def test_rule_based_sql_and_answers_without_sql(self) -> None:
        trace = [step("sql_generation", "deterministic fallback template"), step("synthesis", "deterministic summary of the query result")]
        summary = execution_summary(trace, GENERATED_SQL, None)
        self.assertEqual((summary["sql_origin"], summary["answer_generated_by"]), ("rule_based", "rule_based"))
        out_of_scope = execution_summary([step("input_policy", "business-scope check")], None, {"kind": "none"})
        self.assertEqual((out_of_scope["sql_origin"], out_of_scope["answer_generated_by"]), ("none", "built_in"))

    def test_prediction_runtime_names_the_engine_that_ran(self) -> None:
        trace = [step("prediction", "seasonal naive baseline")]
        self.assertEqual(model_gov(object(), execution_summary(trace, GENERATED_SQL, None))["prediction_runtime"], "seasonal naive baseline")


class LineageTests(unittest.TestCase):
    def lineage(self, origin: str) -> dict:
        return build_lineage("banking", "q", REGISTRY_SQL, [{"account_count": 1}], "bank.user", "AU-1", ["gold_accounts"], True, {}, sql_origin=origin)

    def test_registry_lineage_does_not_claim_nl_to_sql(self) -> None:
        lineage = self.lineage("registry")
        self.assertIn("Approved metric registry", lineage["engine"])
        self.assertNotIn("NL→SQL", str(lineage))
        self.assertEqual(lineage["trace"][1]["title"], "Approved metric registry · compiled SQL")

    def test_only_ai_generated_sql_is_nl_to_sql(self) -> None:
        self.assertIn("NL→SQL", self.lineage("ai")["engine"])
        self.assertIn("Rule-based SQL template", self.lineage("rule_based")["engine"])


class OrchestratorGovernanceTests(unittest.TestCase):
    """The response builder derives governance and lineage from the same trace."""

    def setUp(self) -> None:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()
        self.addCleanup(engine.dispose)
        self.addCleanup(self.db.close)
        self.user = AuthUser(username="bank.user", domain="banking", role="analyst")

    def respond(self, steps, sql, evidence=None):
        return Orchestrator()._respond(
            self.db, user=self.user, question="q", plan={}, schema_ctx={"tables": {"gold_accounts": []}}, steps=steps,
            audit_id="AU-1", persist_history=False, conversation_id=uuid.uuid4(), answer="a", status="complete",
            sql=sql, table=[{"account_count": 1}], guardrail_ok=True, error=None, evidence=evidence,
        )

    def test_registry_and_ai_responses_are_consistent_across_tabs(self) -> None:
        registry = self.respond(REGISTRY_TRACE, REGISTRY_SQL, {"kind": "metric", "sources": []})
        self.assertIsNone(registry.llm_provider)
        self.assertEqual(registry.model_gov["answer_generated_by"], "approved metric registry (template)")
        self.assertIn("Approved metric registry", registry.lineage["engine"])

        generated = self.respond(GENERATED_TRACE, GENERATED_SQL)
        self.assertEqual(generated.llm_provider, "google")
        self.assertEqual(generated.model_gov["answer_generated_by"], "AI model")
        self.assertIn("NL→SQL", generated.lineage["engine"])


if __name__ == "__main__":
    unittest.main()
