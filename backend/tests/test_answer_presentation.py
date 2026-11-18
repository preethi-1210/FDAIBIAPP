"""Business-friendly answers: what business users receive, and verification that is never overstated."""

import json
import unittest
from datetime import date
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import create_access_token, hash_password
from app.db import Base, get_db
from app.main import fastapi_app
from app.models import AppUser, QueryHistory
from app.schemas import QueryResponse
from app.services.presentation import dataset_label, for_viewer, scrub_text

PHYSICAL = ("banking.gold", "gold_accounts", "active_account_status", "SELECT", "metrics.calculate")
TODAY = date(2026, 11, 16)


def account_count_response(**overrides) -> QueryResponse:
    """The shape the banking workflow returns for 'How many active accounts do we have?'."""
    values = dict(
        answer=(
            "Number of accounts (account status ACTIVE) for all available data: 6,825 accounts.\n\n"
            "Source: banking.gold.gold_accounts. Reporting period: all available data; data available 2010-01-02 to "
            "2026-06-06. Definition: Number of accounts held today, in any status unless a status is asked for."
        ),
        sql="SELECT COUNT(*) AS account_count FROM gold_accounts a WHERE a.status IN (:f_account_status_0) LIMIT 1",
        table=[{"account_count": 6825, "record_count": 6825}],
        plan={"intent": "value", "route": "metric", "is_prediction": False, "query_plan": {"metric_id": "account_count"}},
        schema_used=["gold_accounts"],
        domain="banking",
        llm_provider=None,
        audit_id="AU-123",
        lineage={"catalog": "banking", "trace": []},
        guardrails={"ok": True, "error": None, "checks": [{"name": "SELECT only", "pass": True}, {"name": "Genie disabled", "pass": True}]},
        model_gov={"llm": "google", "answer_generated_by": "approved metric registry (template)", "pii_policy": "masked at silver / gold view"},
        status="complete",
        trace=[
            {"stage": "context", "engine": "banking business context 1.0.0", "ok": True, "detail": "terms: Account"},
            {"stage": "router", "engine": "banking value (rules)", "ok": True, "detail": None},
            {"stage": "sql_validation", "engine": "parsed SQL policy", "ok": True, "detail": None},
            {"stage": "sql_execution", "engine": "banking.gold", "ok": True, "detail": "1 row(s)"},
            {"stage": "result_validation", "engine": "shape, range and reconciliation checks", "ok": True, "detail": None},
            {"stage": "business_rules", "engine": "banking business rules", "ok": True, "detail": "active_account_status (VERIFIED)"},
        ],
        evidence={
            "kind": "metric",
            "metric": "account_count",
            "metric_label": "Number of accounts",
            "filter_labels": {"account_status": "account status"},
            "aggregation": "COUNT(*)",
            "definition": "Number of accounts held today, in any status unless a status is asked for.",
            "time_semantics": "Current state at the last pipeline load.",
            "sources": ["banking.gold.gold_accounts"],
            "period": "all available data",
            "coverage": {"first_date": "2010-01-02", "last_date": "2026-06-06"},
            "filters": {"account_status": ["ACTIVE"]},
            "interpreted_as": "Number of accounts; account status = ACTIVE",
            "business_context": {"rules": [{"id": "active_account_status", "status": "VERIFIED"}], "source": "banking.gold"},
            "row_count": 1,
        },
    )
    values.update(overrides)
    return QueryResponse(**values)


class BusinessViewTests(unittest.TestCase):
    def test_business_users_receive_no_technical_record(self) -> None:
        view = for_viewer(account_count_response(), "analyst", TODAY)
        raw = view.model_dump_json()
        for name in PHYSICAL:
            self.assertNotIn(name, raw)
        self.assertIsNone(view.sql)
        self.assertIsNone(view.lineage)
        self.assertEqual(view.trace, [])
        self.assertEqual(view.schema_used, [])
        self.assertNotIn("route", view.plan)
        self.assertIn("Banking accounts data", view.answer)
        self.assertEqual(view.evidence["sources"], ["Banking accounts data"])
        self.assertEqual([check["name"] for check in view.guardrails["checks"]], ["Read-only access"])
        self.assertFalse(view.presentation["technical_details"])
        # A legacy record's free-text provider field is not passed on.
        self.assertNotIn("llm", view.model_gov)
        self.assertNotIn("google", raw)
        # The actual figure and its filter are untouched.
        self.assertEqual(view.table, [{"account_count": 6825, "record_count": 6825}])
        self.assertEqual(view.evidence["filters"], {"account_status": ["ACTIVE"]})

    def test_administrators_keep_the_full_technical_record(self) -> None:
        original = account_count_response()
        view = for_viewer(original, "admin", TODAY)
        self.assertEqual(view.sql, original.sql)
        self.assertEqual(view.trace, original.trace)
        self.assertEqual(view.answer, original.answer)
        self.assertTrue(view.presentation["technical_details"])

    def test_label_filter_and_definition_are_presented_together(self) -> None:
        presented = for_viewer(account_count_response(), "analyst", TODAY).presentation
        self.assertEqual(presented["metric_label"], "Number of accounts")
        self.assertEqual(presented["filters"], [{"label": "account status", "values": ["ACTIVE"]}])
        self.assertEqual(presented["definition"], "Number of accounts held today, in any status unless a status is asked for.")
        self.assertIn("Applied filter: account status = ACTIVE", presented["verification"]["checks"])
        self.assertEqual(presented["summary"], "Number of accounts (account status ACTIVE) for all available data: 6,825 accounts.")

    def test_freshness_reports_the_real_coverage_and_flags_stale_data(self) -> None:
        freshness = for_viewer(account_count_response(), "analyst", TODAY).presentation["freshness"]
        self.assertEqual(freshness["latest_date"], "2026-06-06")
        self.assertEqual(freshness["age_days"], 163)
        self.assertTrue(freshness["stale"])
        future = for_viewer(account_count_response(evidence={"kind": "metric", "coverage": {"last_date": "2030-01-01"}}), "analyst", TODAY)
        self.assertTrue(future.presentation["freshness"]["future"])
        self.assertFalse(future.presentation["freshness"]["stale"])
        unknown = for_viewer(account_count_response(evidence={"kind": "warehouse", "sources": []}), "analyst", TODAY)
        self.assertFalse(unknown.presentation["freshness"]["available"])


class VerificationTests(unittest.TestCase):
    def status(self, **overrides) -> str:
        return for_viewer(account_count_response(**overrides), "analyst", TODAY).presentation["verification"]["status"]

    def test_passed_only_when_every_validation_that_ran_succeeded(self) -> None:
        self.assertEqual(self.status(), "passed")

    def test_a_retried_validation_counts_by_its_final_outcome(self) -> None:
        trace = account_count_response().trace
        retried = [{"stage": "sql_validation", "engine": "SQL policy", "ok": False, "detail": "rejected"}, *trace]
        self.assertEqual(self.status(trace=retried), "passed")

    def test_a_failed_final_check_is_never_reported_as_passed(self) -> None:
        trace = [*account_count_response().trace[:-2], {"stage": "result_validation", "engine": "checks", "ok": False, "detail": "x"}]
        self.assertEqual(self.status(trace=trace), "failed")
        self.assertEqual(self.status(status="error"), "failed")

    def test_without_data_or_checks_nothing_is_claimed(self) -> None:
        no_checks = [step for step in account_count_response().trace if step["stage"] not in {"sql_validation", "result_validation"}]
        self.assertEqual(self.status(trace=no_checks), "not_verified")
        self.assertEqual(self.status(trace=[], sql=None, status="complete"), "not_verified")
        self.assertEqual(self.status(status="insufficient_data"), "not_verified")


class ScrubTests(unittest.TestCase):
    def test_physical_names_become_business_names(self) -> None:
        self.assertEqual(dataset_label("banking.gold.agg_transactions_daily", "banking"), "Banking transactions daily data")
        text = scrub_text("Read `tenant_a`.`gold`.`encounters` and banking.gold today; version 1.0.0 at 2.5%.", "healthcare")
        self.assertNotIn("tenant_a", text)
        self.assertNotIn("banking.gold", text)
        self.assertIn("1.0.0", text)
        self.assertIn("2.5%", text)


class EndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        Session = sessionmaker(bind=self.engine)
        response = account_count_response()
        with Session() as db:
            db.add_all([
                AppUser(username="bank.user", password_hash=hash_password("bank-password"), domain="banking", display_name="B"),
                AppUser(username="admin", password_hash=hash_password("admin-password"), domain="all", display_name="A"),
                QueryHistory(
                    username="bank.user", domain="banking", question="How many active accounts do we have?", sql=response.sql,
                    answer=response.answer, result_json=response.model_dump_json(), audit_id="AU-123",
                    tables_used="gold_accounts", row_count=1, guardrail_ok=1,
                ),
            ])
            db.commit()

        def override_db():
            db = Session()
            try:
                yield db
            finally:
                db.close()

        fastapi_app.dependency_overrides[get_db] = override_db
        self.addCleanup(fastapi_app.dependency_overrides.clear)
        self.addCleanup(self.engine.dispose)
        self.client = TestClient(fastapi_app)
        self.bank = {"Authorization": f"Bearer {create_access_token('bank.user', 'banking', 'analyst')}"}
        self.admin = {"Authorization": f"Bearer {create_access_token('admin', 'banking', 'admin')}"}

    def test_query_and_stream_return_the_business_view(self) -> None:
        fake = lambda db, question, user, llm_provider=None, **kwargs: account_count_response()  # noqa: E731
        with patch("app.main.orchestrator.run", side_effect=fake):
            plain = self.client.post("/query", json={"question": "How many active accounts?"}, headers=self.bank).text
            streamed = self.client.post("/query/stream", json={"question": "How many active accounts?"}, headers=self.bank).text
            admin = self.client.post("/query", json={"question": "How many active accounts?"}, headers=self.admin).json()
        for raw in (plain, streamed):
            for name in PHYSICAL:
                self.assertNotIn(name, raw)
        self.assertEqual(json.loads(streamed.splitlines()[-1])["data"]["presentation"]["verification"]["status"], "passed")
        self.assertIn("gold_accounts", admin["sql"])

    def test_history_hides_sql_and_tables_from_business_users(self) -> None:
        raw = self.client.get("/history", headers=self.bank).text
        for name in PHYSICAL:
            self.assertNotIn(name, raw)
        item = json.loads(raw)[0]
        self.assertIsNone(item["sql"])
        self.assertEqual(item["tables_used"], [])

    def test_refusals_do_not_name_physical_sources_to_business_users(self) -> None:
        def refuse(*_args, **_kwargs):
            raise HTTPException(status_code=422, detail="Query must read only banking.gold.gold_accounts.")

        with patch("app.main.orchestrator.run", side_effect=refuse):
            business = self.client.post("/query", json={"question": "How many active accounts?"}, headers=self.bank)
            admin = self.client.post("/query", json={"question": "How many active accounts?"}, headers=self.admin)
        self.assertEqual(business.status_code, 422)
        self.assertNotIn("banking.gold", business.json()["detail"])
        self.assertIn("banking.gold.gold_accounts", admin.json()["detail"])


if __name__ == "__main__":
    unittest.main()
