"""Ask AI experience: out-of-scope detection and the streamed progress of a real analysis."""

import json
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import cache
from app.auth import AuthUser, create_access_token, hash_password
from app.config import settings
from app.db import Base, get_db
from app.llm import record_step, report_progress, trace_steps
from app.main import fastapi_app
from app.models import AppUser, QueryHistory
from app.schemas import QueryResponse
from app.services.business_scope import is_out_of_scope
from app.services.orchestrator import Orchestrator

OFF_TOPIC = [
    "Tell me a chocolate cake recipe.",
    "What is the weather tomorrow?",
    "Tell me a joke.",
    "I need to travel from Coimbatore to Chennai.",
    "i need to travel coimbatore to chennai",
    "Write a romantic poem.",
    "Book a train to Bangalore",
]
BUSINESS = [
    "What is the total transaction amount?",
    "How many accounts do we have?",
    "What is the transaction failure rate?",
    "Show monthly transaction trends",
    "Total spend at travel merchants last month",
    "How many travel insurance claims were approved?",
    "Show revenue by store for the holiday season",
    "What is the loan default rate?",
    "How many encounters were recorded?",
    "Forecast next month's premium",
    "What can you do?",
]


class BusinessScopeTests(unittest.TestCase):
    def test_plainly_unrelated_questions_are_out_of_scope(self) -> None:
        for question in OFF_TOPIC:
            with self.subTest(question=question):
                self.assertTrue(is_out_of_scope(question))

    def test_business_questions_supported_or_not_are_never_out_of_scope(self) -> None:
        for question in BUSINESS:
            with self.subTest(question=question):
                self.assertFalse(is_out_of_scope(question))


def _forbidden(*_args, **_kwargs):
    raise AssertionError("An out-of-scope question must not reach a planner, tool or warehouse")


class OutOfScopeWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.orchestrator = Orchestrator()
        self.orchestrator._ready = True
        self.orchestrator.graph.resolve = _forbidden
        for target in (
            "app.services.orchestrator.execute_readonly",
            "app.services.orchestrator.execute_healthcare_tenant_readonly",
            "app.services.orchestrator.invoke_tool",
            "app.services.orchestrator.generate_sql",
            "app.services.orchestrator.BankingAgent.run",
            "app.services.orchestrator.complete_text",
            "app.llm.complete_json",
        ):
            patcher = patch(target, side_effect=_forbidden)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_every_domain_answers_without_querying_and_names_the_domain(self) -> None:
        for domain, label in (("banking", "Banking"), ("finance", "Finance"), ("insurance", "Insurance"), ("retail", "Retail"), ("healthcare", "Healthcare")):
            with self.subTest(domain=domain):
                user = AuthUser(username=f"{domain}.user", domain=domain, role="analyst", tenant_id="t1" if domain == "healthcare" else None)
                response = self.orchestrator.run(self.db, "Tell me a joke", user)
                self.assertEqual(response.status, "out_of_scope")
                self.assertIsNone(response.sql)
                self.assertEqual(response.table, [])
                self.assertIsNone(response.llm_provider)
                self.assertIn(label, response.answer)
                self.assertEqual(response.evidence["kind"], "none")
        # Saved like any other answer, so recent history keeps working.
        self.assertEqual(self.db.query(QueryHistory).count(), 5)

    def test_another_domain_named_is_still_refused_first(self) -> None:
        user = AuthUser(username="bank.user", domain="banking", role="analyst")
        with self.assertRaises(HTTPException) as raised:
            self.orchestrator.run(self.db, "Tell me a joke using the retail domain data", user)
        self.assertEqual(raised.exception.status_code, 422)


class QueryStreamTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        Session = sessionmaker(bind=self.engine)
        with Session() as db:
            db.add(AppUser(username="bank.user", password_hash=hash_password("bank-password"), domain="banking", display_name="B"))
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

    def stream(self, question: str = "Show total transaction amount", headers: dict | None = None) -> list[dict]:
        response = self.client.post("/query/stream", json={"question": question}, headers=self.bank if headers is None else headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/x-ndjson")
        return [json.loads(line) for line in response.text.splitlines() if line]

    def test_stages_are_streamed_in_order_without_engines_then_the_result(self) -> None:
        def fake_run(db, question, user, llm_provider=None, **kwargs):
            with trace_steps():  # As Orchestrator.run opens one for every request.
                report_progress({"type": "phase", "phase": "connected"})
                record_step("planner", "google:gemini-2.5-flash", detail="secret provider detail")
                record_step("sql_execution", "banking.gold", detail="1 row(s)")
            return QueryResponse(answer="INR 10.00", status="complete", domain=user.domain, conversation_id=kwargs.get("conversation_id"))

        with patch("app.main.orchestrator.run", side_effect=fake_run):
            events = self.stream()
        self.assertEqual(
            [event["type"] for event in events], ["phase", "step", "step", "result"],
        )
        self.assertEqual(events[1], {"type": "step", "stage": "planner", "ok": True})
        self.assertEqual(events[3]["data"]["answer"], "INR 10.00")
        streamed_progress = json.dumps(events[:3])
        self.assertNotIn("gemini", streamed_progress)
        self.assertNotIn("secret", streamed_progress)

    def test_a_refusal_is_streamed_as_an_error_with_its_status(self) -> None:
        def refuse(*_args, **_kwargs):
            raise HTTPException(status_code=422, detail="Retail data is outside the authorized banking source.")

        with patch("app.main.orchestrator.run", side_effect=refuse):
            events = self.stream()
        self.assertEqual(events, [{"type": "error", "status": 422, "detail": "Retail data is outside the authorized banking source."}])

    def test_an_unexpected_failure_never_reports_success(self) -> None:
        with patch("app.main.orchestrator.run", side_effect=RuntimeError("boom")):
            events = self.stream()
        self.assertEqual(events, [{"type": "error", "status": 500, "detail": "The analysis could not be completed."}])

    def test_rate_limit_applies_to_the_stream(self) -> None:
        fake = lambda db, question, user, llm_provider=None, **kwargs: QueryResponse(answer="ok")  # noqa: E731
        with patch("app.main.orchestrator.run", side_effect=fake), patch.object(settings, "rate_limit_query_per_minute", 1), patch.dict(cache._local_rate_windows, clear=True):
            self.assertEqual(self.stream()[-1]["type"], "result")
            self.assertEqual(self.stream()[-1]["status"], 429)

    def test_the_stream_requires_authentication(self) -> None:
        response = self.client.post("/query/stream", json={"question": "Show total transaction amount"})
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
