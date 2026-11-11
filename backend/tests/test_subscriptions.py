"""Plans, allowances, notices, payments and access control, through the real API on a temporary database.

Only the language model and warehouse are replaced. Nothing here reaches a real service.
"""

import json
import os
import tempfile
import unittest
from datetime import timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app import llm, models
from app.auth import AuthUser, create_access_token, hash_password
from app.config import settings
from app.db import Base, get_db
from app.domains import DOMAINS
from app.llm import classify_provider_error, record_step
from app.main import fastapi_app as app
from app.models import (
    AppUser,
    LlmUsageEvent,
    PaymentTransaction,
    SubscriptionHistory,
    SubscriptionPlan,
    UsageNotification,
    UsageTransaction,
    UserDomainGrant,
    UserSubscription,
)
from app.schemas import QueryResponse
from app.services import subscriptions
from app.services.payments import MockGateway, PaymentError, get_gateway
from app.services.subscriptions import charge, current_state, feature_used, open_gate, percent_used, record_llm_events


def model_step(stage="synthesis", provider="google:gemini-3.5-flash", ok=True, **extra):
    return {"stage": stage, "engine": provider, "ok": ok, **extra}


class Base_(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.mkdtemp(prefix="ryze-sub-")
        self.engine = create_engine(f"sqlite:///{self.directory}/sub.db", poolclass=NullPool, connect_args={"timeout": 10})
        # As PostgreSQL does: SQLite ignores foreign keys unless told otherwise.
        event.listen(self.engine, "connect", lambda connection, _record: connection.execute("PRAGMA foreign_keys=ON"))
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.Session() as db:
            for username, domain in (("bank.user", "banking"), ("ret.user", "retail"), ("boss", "all")):
                db.add(AppUser(username=username, password_hash=hash_password("x"), domain=domain, display_name=username))
            db.commit()
        self.addCleanup(self.engine.dispose)

        def override():
            with self.Session() as session:
                yield session

        app.dependency_overrides[get_db] = override
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app, raise_server_exceptions=True)
        self.bank = AuthUser("bank.user", "banking", "analyst")

    def headers(self, username="bank.user", domain="banking", role="analyst"):
        return {"Authorization": f"Bearer {create_access_token(username, domain, role)}"}

    def use_credits(self, username, used, allowance=100):
        with self.Session() as db:
            _, _, period = current_state(db, username)
            period.credits_used = used
            period.credits_allocated = allowance
            db.commit()


class AllowanceTests(Base_):
    def test_new_users_start_on_free_with_placeholder_allowance_and_no_price_for_paid_plans(self) -> None:
        with self.Session() as db:
            subscription, plan, period = current_state(db, "bank.user")
            self.assertEqual((subscription.plan_code, plan.allowance_confirmed), ("free", False))
            self.assertEqual(period.credits_used, 0)
            for code in ("plus", "pro"):
                paid = db.get(SubscriptionPlan, code)
                self.assertIsNone(paid.monthly_price_minor)
                self.assertIsNone(paid.yearly_price_minor)

    def test_the_same_key_never_deducts_twice(self) -> None:
        with self.Session() as db:
            self.assertEqual(charge(db, "bank.user", "banking", "sql_generation", "k1"), 2)
            self.assertEqual(charge(db, "bank.user", "banking", "sql_generation", "k1"), 0)
            self.assertEqual(charge(db, "bank.user", "banking", "standard_chat", "k2"), 1)
            _, _, period = current_state(db, "bank.user")
            self.assertEqual(period.credits_used, 3)
            self.assertEqual(db.query(UsageTransaction).filter_by(kind="charge").count(), 2)

    def test_the_ledger_always_explains_credits_used(self) -> None:
        with self.Session() as db:
            for index in range(5):
                charge(db, "bank.user", "banking", "forecasting", f"f{index}")
            _, _, period = current_state(db, "bank.user")
            total = sum(row.credits for row in db.query(UsageTransaction).filter_by(period_id=period.id, kind="charge"))
            self.assertEqual(total, period.credits_used)

    def test_only_a_model_or_a_forecast_is_charged(self) -> None:
        self.assertIsNone(feature_used([], False))
        self.assertIsNone(feature_used([{"stage": "router", "engine": "banking metric registry", "ok": True}], False))
        self.assertIsNone(feature_used([model_step(ok=False)], False))
        self.assertEqual(feature_used([model_step()], False), "standard_chat")
        self.assertEqual(feature_used([model_step("planner"), model_step("sql_generation")], False), "sql_generation")
        self.assertEqual(feature_used([], True), "forecasting")

    def test_a_refused_or_unanswered_request_costs_nothing(self) -> None:
        with self.Session() as db:
            gate = open_gate(db, self.bank)
            for status in ("needs_clarification", "insufficient_data", "error", "awaiting_review"):
                summary = subscriptions.settle(db, self.bank, gate, [model_step()], status=status, made_prediction=False, audit_id=f"A{status}")
                self.assertEqual(summary["credits_charged"], 0, status)
            summary = subscriptions.settle(db, self.bank, gate, [model_step()], status="complete", made_prediction=False, audit_id="A1")
            self.assertEqual(summary["credits_charged"], 1)

    def test_the_allowance_resets_when_the_month_ends(self) -> None:
        with self.Session() as db:
            subscription, _, period = current_state(db, "bank.user")
            period.credits_used = 40
            db.commit()
            later = period.period_end + timedelta(minutes=1)
            _, _, renewed = current_state(db, "bank.user", later)
            self.assertNotEqual(renewed.id, period.id)
            self.assertEqual(renewed.credits_used, 0)
            self.assertEqual(db.get(UserSubscription, "bank.user").plan_code, "free")

    def test_a_yearly_plan_resets_its_allowance_monthly(self) -> None:
        with self.Session() as db:
            subscription, _, _ = current_state(db, "bank.user")
            now = subscriptions.utcnow()
            subscriptions.apply_plan(db, subscription, db.get(SubscriptionPlan, "pro"), cycle="yearly", actor="t", action="upgraded", auto_renew=False, source="payment", now=now)
            db.commit()
            _, _, first = current_state(db, "bank.user", now + timedelta(days=1))
            first.credits_used = 9
            db.commit()
            subscription, plan, second = current_state(db, "bank.user", first.period_end + timedelta(hours=1))
            self.assertEqual(plan.code, "pro")
            self.assertEqual(second.credits_used, 0)
            self.assertEqual(second.credits_allocated, 5000)

    def test_an_unrenewed_paid_plan_expires_to_free(self) -> None:
        with self.Session() as db:
            subscription, _, _ = current_state(db, "bank.user")
            subscriptions.apply_plan(db, subscription, db.get(SubscriptionPlan, "plus"), cycle="monthly", actor="t", action="upgraded", auto_renew=False, source="payment")
            db.commit()
            end = subscription.current_period_end
            subscription, plan, _ = current_state(db, "bank.user", end + timedelta(seconds=1))
            self.assertEqual(plan.code, "free")
            self.assertEqual(db.query(SubscriptionHistory).filter_by(username="bank.user", action="expired").count(), 1)


class NoticeTests(Base_):
    def crossing(self, used, key):
        self.use_credits("bank.user", used)
        with self.Session() as db:
            charge(db, "bank.user", "banking", "standard_chat", key)
            with Session(bind=self.engine) as session:
                return subscriptions.unseen_notices(session, "bank.user")

    def test_each_threshold_is_raised_once_with_its_level(self) -> None:
        expected = {30: "info", 50: "info", 80: "warning", 90: "critical", 100: "exhausted"}
        for threshold, level in expected.items():
            notices = self.crossing(threshold - 1, f"t{threshold}")
            self.assertEqual([item["threshold"] for item in notices], [threshold])
            self.assertEqual(notices[0]["level"], level)
            with self.Session() as db:
                db.get(UsageNotification, notices[0]["id"]).seen_at = subscriptions.utcnow()
                db.commit()
        self.assertEqual(self.crossing(99, "again"), [])

    def test_consecutive_requests_do_not_repeat_a_notice(self) -> None:
        self.use_credits("bank.user", 29)
        with self.Session() as db:
            for index in range(5):
                charge(db, "bank.user", "banking", "standard_chat", f"c{index}")
            self.assertEqual(db.query(UsageNotification).count(), 1)

    def test_jumping_past_several_thresholds_shows_only_the_highest(self) -> None:
        notices = self.crossing(84, "jump")
        self.assertEqual([item["threshold"] for item in notices], [80])
        with self.Session() as db:
            self.assertEqual(sorted(row.threshold for row in db.query(UsageNotification)), [30, 50, 80])

    def test_notices_describe_the_plan_allowance_and_never_provider_quota(self) -> None:
        notices = self.crossing(79, "wording")
        self.assertIn("Free plan AI allowance", notices[0]["message"])
        self.assertIn("Upgrade to Plus", notices[0]["message"])
        self.assertNotIn("Gemini", notices[0]["message"])
        self.assertNotIn("quota", notices[0]["message"].lower())

    def test_someone_elses_notice_cannot_be_dismissed(self) -> None:
        notices = self.crossing(29, "theirs")
        response = self.client.post(f"/v1/subscription/notices/{notices[0]['id']}/seen", headers=self.headers("ret.user", "retail"))
        self.assertEqual(response.status_code, 404)


class GateTests(Base_):
    def test_exhausted_allowance_blocks_models_with_the_agreed_message(self) -> None:
        self.use_credits("bank.user", 100)
        with self.Session() as db:
            gate = open_gate(db, self.bank)
        self.assertEqual(gate.refusal("synthesis"), subscriptions.EXHAUSTED_MESSAGE)

    def test_a_feature_costing_more_than_what_remains_is_refused(self) -> None:
        self.use_credits("bank.user", 99)
        with self.Session() as db:
            gate = open_gate(db, self.bank)
        self.assertIsNone(gate.refusal("synthesis"))
        self.assertEqual(gate.refusal("sql_generation"), subscriptions.EXHAUSTED_MESSAGE)

    def test_a_locked_feature_names_the_plans_that_include_it(self) -> None:
        with self.Session() as db:
            gate = open_gate(db, self.bank)
        self.assertEqual(gate.refusal("deep_research"), "Deep research is available with the Plus and Pro plans.")

    def test_a_periodic_feature_limit_is_enforced(self) -> None:
        with self.Session() as db:
            for index in range(5):
                charge(db, "bank.user", "banking", "forecasting", f"limit{index}")
            gate = open_gate(db, self.bank)
        self.assertIn("all 5", gate.refusal("forecasting"))

    def test_administrators_are_recorded_but_not_limited(self) -> None:
        self.use_credits("boss", 100)
        with self.Session() as db:
            gate = open_gate(db, AuthUser("boss", "banking", "admin"))
        self.assertIsNone(gate.refusal("sql_generation"))

    def test_enforcement_off_limits_nobody(self) -> None:
        self.use_credits("bank.user", 100)
        with patch.object(settings, "subscription_enforcement", False), self.Session() as db:
            self.assertIsNone(open_gate(db, self.bank).refusal("synthesis"))

    def test_switched_off_there_is_no_gate_and_no_records(self) -> None:
        with patch.object(settings, "subscriptions_enabled", False), self.Session() as db:
            self.assertIsNone(open_gate(db, self.bank))
            record_llm_events(db, self.bank, [model_step()], request_id=None, audit_id=None)
            self.assertEqual(db.query(LlmUsageEvent).count(), 0)
        with patch.object(settings, "subscriptions_enabled", False):
            self.assertEqual(self.client.get("/v1/subscription/me", headers=self.headers()).json(), {"enabled": False})

    def test_a_broken_ledger_does_not_stop_a_question(self) -> None:
        with patch.object(subscriptions, "current_state", side_effect=RuntimeError("down")), self.Session() as db:
            self.assertIsNone(open_gate(db, self.bank))

    def test_blocked_stage_makes_no_provider_call(self) -> None:
        with llm.trace_steps() as steps, llm.usage_gate(lambda stage: "No allowance"), patch("app.llm._attempt") as attempt:
            self.assertIsNone(llm.complete_json("s", "u", "ollama", stage="planner"))
            self.assertIsNone(llm.complete_text("s", "u", "ollama", stage="synthesis"))
        attempt.assert_not_called()
        self.assertTrue(all(step["plan_blocked"] for step in steps))


class AskFlowTests(Base_):
    """The real /v1/query route with the orchestrator's own run(), around a replaced _run."""

    def ask(self, inner, username="bank.user", domain="banking", role="analyst", provider=None):
        with patch.object(app_orchestrator(), "_run", inner):
            return self.client.post("/v1/query", json={"question": "How much money moved?", "llm_provider": provider}, headers=self.headers(username, domain, role))

    @staticmethod
    def answer(status="complete", **fields):
        return QueryResponse(answer="ok", plan={"route": "test"}, status=status, audit_id=fields.pop("audit_id", None) or f"AU-{os.urandom(4).hex()}", **fields)

    def test_a_question_using_a_model_is_charged_and_recorded(self) -> None:
        def inner(*_args, **_kwargs):
            record_step("synthesis", "google:gemini-3.5-flash", latency_ms=20, input_tokens=10, output_tokens=5, total_tokens=18)
            return self.answer()

        response = self.ask(inner)
        self.assertEqual(response.status_code, 200, response.text)
        usage = response.json()["usage"]
        self.assertEqual((usage["credits_charged"], usage["feature"], usage["credits_used"]), (1, "standard_chat", 1))
        self.assertEqual(usage["kind"], "plan_allowance")
        with self.Session() as db:
            event = db.query(LlmUsageEvent).one()
            self.assertEqual((event.provider, event.model, event.input_tokens, event.output_tokens, event.total_tokens), ("google", "gemini-3.5-flash", 10, 5, 18))

    def test_a_rule_answer_costs_nothing_and_works_at_the_limit(self) -> None:
        self.use_credits("bank.user", 100)

        def inner(*_args, **_kwargs):
            record_step("router", "banking metric registry")
            return self.answer()

        response = self.ask(inner)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["usage"]["credits_charged"], 0)

    def test_missing_token_data_stays_null(self) -> None:
        def inner(*_args, **_kwargs):
            record_step("synthesis", "ollama:llama3.2")
            return self.answer()

        self.ask(inner)
        with self.Session() as db:
            event = db.query(LlmUsageEvent).one()
            self.assertIsNone(event.input_tokens)
            self.assertIsNone(event.total_tokens)
        own = self.client.get("/v1/subscription/me", headers=self.headers()).json()["own_tokens"]
        self.assertEqual(own["attempts_without_token_data"], 1)

    def test_gemini_429_then_ollama_records_both_attempts_and_the_real_provider(self) -> None:
        def inner(*_args, **_kwargs):
            record_step("synthesis", "google:gemini-3.5-flash", ok=False, detail="ClientError", error_kind="quota_exhausted", retry_after_seconds=29)
            record_step("synthesis", "ollama:llama3.2", input_tokens=3, output_tokens=2, total_tokens=5)
            return self.answer()

        self.ask(inner)
        with self.Session() as db:
            rows = db.query(LlmUsageEvent).order_by(LlmUsageEvent.id).all()
            self.assertEqual([(row.provider, row.ok, row.error_kind) for row in rows], [("google", False, "quota_exhausted"), ("ollama", True, None)])
            self.assertEqual(rows[0].retry_after_seconds, 29)
        report = self.client.get("/v1/admin/usage", headers=self.headers("boss", "banking", "admin")).json()
        self.assertEqual(report["quota_events"][0]["kind"], "quota_exhausted")

    def test_a_request_stopped_by_the_plan_returns_402_not_a_misleading_error(self) -> None:
        self.use_credits("bank.user", 100)
        from fastapi import HTTPException

        def inner(*_args, **_kwargs):
            llm.complete_json("s", "u", None, stage="sql_generation")
            raise HTTPException(status_code=422, detail="The language model could not generate SQL.")

        response = self.ask(inner)
        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.json()["detail"], subscriptions.EXHAUSTED_MESSAGE)

    def test_an_ordinary_422_keeps_its_contract(self) -> None:
        from fastapi import HTTPException

        def inner(*_args, **_kwargs):
            raise HTTPException(status_code=422, detail="Not available")

        response = self.ask(inner)
        self.assertEqual((response.status_code, response.json()["detail"]), (422, "Not available"))

    def test_context_is_trimmed_to_the_plan(self) -> None:
        seen = {}

        def inner(_db, _question, _user, _override, _persist, context, *_rest):
            seen["length"] = len(context)
            return self.answer()

        with self.Session() as db:
            conversation = models.Conversation(id="8f1f2a50-6b4d-4f39-8d11-111111111111", username="bank.user", domain="banking", title="t")
            db.add(conversation)
            for index in range(20):
                db.add(models.ConversationMessage(conversation_id=conversation.id, role="user", content=str(index)))
            db.commit()
        with patch.object(app_orchestrator(), "_run", inner):
            self.client.post("/v1/query", json={"question": "How much money moved?", "conversation_id": "8f1f2a50-6b4d-4f39-8d11-111111111111"}, headers=self.headers())
        self.assertEqual(seen["length"], 6)

    def test_a_plan_can_restrict_providers(self) -> None:
        with self.Session() as db:
            current_state(db, "bank.user")
            db.get(SubscriptionPlan, "free").allowed_providers = "ollama"
            db.commit()
        response = self.ask(lambda *_args, **_kwargs: self.answer(), provider="google")
        self.assertEqual(response.status_code, 402)
        self.assertEqual(self.ask(lambda *_args, **_kwargs: self.answer(), provider="ollama").status_code, 200)


def app_orchestrator():
    from app import main

    return main.orchestrator


class IsolationTests(Base_):
    def test_a_plan_grants_no_domain(self) -> None:
        with self.Session() as db:
            subscription, _, _ = current_state(db, "bank.user")
            subscriptions.apply_plan(db, subscription, db.get(SubscriptionPlan, "pro"), cycle="monthly", actor="t", action="upgraded", auto_renew=False, source="payment")
            db.commit()
            self.assertEqual(db.query(UserDomainGrant).filter_by(username="bank.user").count(), 0)
        for domain in DOMAINS:
            if domain == "banking":
                continue
            response = self.client.get("/v1/subscription/me", headers=self.headers("bank.user", domain))
            self.assertEqual(response.status_code, 403, domain)

    def test_admin_plan_change_leaves_grants_alone(self) -> None:
        with self.Session() as db:
            db.add(UserDomainGrant(username="bank.user", domain="retail", granted_by="boss"))
            db.commit()
        admin = self.headers("boss", "banking", "admin")
        response = self.client.post("/v1/admin/users/bank.user/subscription", json={"plan_code": "pro", "reason": "pilot user"}, headers=admin)
        self.assertEqual(response.status_code, 200, response.text)
        with self.Session() as db:
            self.assertEqual([row.domain for row in db.query(UserDomainGrant).filter_by(username="bank.user")], ["retail"])

    def test_business_users_see_only_their_own_usage(self) -> None:
        with self.Session() as db:
            current_state(db, "bank.user")
            current_state(db, "ret.user")
            record_llm_events(db, self.bank, [model_step(input_tokens=100, output_tokens=1, total_tokens=101)], request_id=None, audit_id=None)
            record_llm_events(db, AuthUser("ret.user", "retail", "analyst"), [model_step(input_tokens=7, output_tokens=1, total_tokens=8)], request_id=None, audit_id=None)
        body = self.client.get("/v1/subscription/me", headers=self.headers("ret.user", "retail"))
        self.assertEqual(body.json()["own_tokens"]["input"], 7)
        self.assertNotIn("bank.user", body.text)

    def test_cost_counts_thinking_tokens_a_provider_reports_only_in_its_total(self) -> None:
        with self.Session() as db:
            # As reported by the real Gemini API on 2026-11-05: 2067 in, 85 out, 2606 total.
            record_llm_events(db, self.bank, [model_step(input_tokens=2067, output_tokens=85, total_tokens=2606)], request_id=None, audit_id=None)
        with patch.object(settings, "llm_cost_per_million_tokens", "google:gemini-3.5-flash=1,10"):
            report = self.client.get("/v1/admin/usage", headers=self.headers("boss", "banking", "admin")).json()
        self.assertAlmostEqual(report["by_model"][0]["cost"], (2067 + 539 * 10) / 1_000_000)

    def test_every_admin_endpoint_refuses_a_business_user(self) -> None:
        headers = self.headers()
        calls = [
            ("get", "/v1/admin/plans", None),
            ("get", "/v1/admin/usage", None),
            ("get", "/v1/admin/subscriptions", None),
            ("put", "/v1/admin/plans/free", {"name": "x", "monthly_credits": 999999, "max_context_messages": 6}),
            ("post", "/v1/admin/users/bank.user/subscription", {"plan_code": "pro", "reason": "myself"}),
            ("post", "/v1/admin/users/bank.user/credits", {"credits": 1000, "reason": "myself"}),
        ]
        for method, path, body in calls:
            response = getattr(self.client, method)(path, json=body, headers=headers) if body else getattr(self.client, method)(path, headers=headers)
            self.assertEqual(response.status_code, 403, path)

    def test_a_forged_admin_claim_in_the_token_is_ignored(self) -> None:
        self.assertEqual(self.client.get("/v1/admin/usage", headers=self.headers("bank.user", "banking", "admin")).status_code, 403)

    def test_admin_usage_reports_by_domain_user_model_and_period(self) -> None:
        with self.Session() as db:
            record_llm_events(db, self.bank, [model_step(input_tokens=1000, output_tokens=500, total_tokens=1500), model_step(ok=False, error_kind="rate_limited")], request_id=None, audit_id=None)
            record_llm_events(db, AuthUser("ret.user", "retail", "analyst"), [model_step(provider="ollama:llama3.2", input_tokens=4, output_tokens=2, total_tokens=6)], request_id=None, audit_id=None)
        admin = self.headers("boss", "banking", "admin")
        with patch.object(settings, "llm_cost_per_million_tokens", "google:gemini-3.5-flash=1,2"):
            report = self.client.get("/v1/admin/usage", headers=admin).json()
        by_model = {row["key"]: row for row in report["by_model"]}
        self.assertAlmostEqual(by_model["google:gemini-3.5-flash"]["cost"], 0.002)
        self.assertIsNone(by_model["ollama:llama3.2"]["cost"])
        self.assertEqual(by_model["google:gemini-3.5-flash"]["failed"], 1)
        self.assertEqual({row["key"] for row in report["by_domain"]}, {"banking", "retail"})
        self.assertEqual({row["key"] for row in report["by_user"]}, {"bank.user", "ret.user"})
        self.assertEqual(by_model["google:gemini-3.5-flash"]["total_tokens"], 1500)
        self.assertEqual(len(report["by_day"]), 1)
        self.assertEqual(len(report["by_month"]), 1)


class PaymentTests(Base_):
    def setUp(self) -> None:
        super().setUp()
        for setting, value in (("payment_gateway", "mock"), ("demo_mode", True)):
            patcher = patch.object(settings, setting, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        with self.Session() as db:
            subscriptions.ensure_default_plans(db)
            plus = db.get(SubscriptionPlan, "plus")
            plus.monthly_price_minor, plus.yearly_price_minor = 50000, 500000
            db.commit()

    def order(self, plan="plus", cycle="monthly", username="bank.user", domain="banking"):
        response = self.client.post("/v1/billing/checkout", json={"plan_code": plan, "billing_cycle": cycle}, headers=self.headers(username, domain))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def pay(self, order_id, outcome="paid"):
        return self.client.post("/v1/billing/mock/pay", json={"order_id": order_id, "outcome": outcome}, headers=self.headers()).json()

    def verify(self, order_id, payment, username="bank.user", domain="banking"):
        return self.client.post("/v1/billing/verify", json={"order_id": order_id, "payment": payment}, headers=self.headers(username, domain))

    def plan_of(self, username="bank.user"):
        with self.Session() as db:
            return current_state(db, username)[1].code

    def test_checkout_alone_changes_nothing(self) -> None:
        order = self.order()
        self.assertTrue(order["test_mode"])
        self.assertEqual(self.plan_of(), "free")

    def test_a_verified_payment_activates_the_plan_with_a_receipt(self) -> None:
        order = self.order()
        response = self.verify(order["order_id"], self.pay(order["order_id"]))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["receipt_number"].startswith("RCPT-"))
        self.assertEqual(self.plan_of(), "plus")
        with self.Session() as db:
            _, plan, period = current_state(db, "bank.user")
            self.assertEqual(period.credits_allocated, plan.monthly_credits)

    def test_a_browser_claiming_success_without_a_valid_signature_is_refused(self) -> None:
        order = self.order()
        forged = {"payment_id": "x", "status": "paid", "amount_minor": str(order["amount_minor"]), "currency": "INR", "signature": "0" * 64}
        self.assertEqual(self.verify(order["order_id"], forged).status_code, 400)
        self.assertEqual(self.verify(order["order_id"], {"status": "paid"}).status_code, 400)
        self.assertEqual(self.plan_of(), "free")

    def test_a_tampered_amount_is_refused(self) -> None:
        order = self.order()
        payment = self.pay(order["order_id"])
        payment["amount_minor"] = "1"
        self.assertEqual(self.verify(order["order_id"], payment).status_code, 400)
        self.assertEqual(self.plan_of(), "free")

    def test_a_correctly_signed_wrong_amount_is_refused(self) -> None:
        order = self.order()
        payment = MockGateway().simulate(order["order_id"], "paid", 1, "INR")
        self.assertEqual(self.verify(order["order_id"], payment).status_code, 400)
        self.assertEqual(self.plan_of(), "free")

    def test_a_signature_for_another_order_is_refused(self) -> None:
        cheap, other = self.order(), self.order()
        self.assertEqual(self.verify(other["order_id"], self.pay(cheap["order_id"])).status_code, 400)

    def test_someone_elses_order_looks_like_it_does_not_exist(self) -> None:
        order = self.order()
        payment = self.pay(order["order_id"])
        self.assertEqual(self.verify(order["order_id"], payment, "ret.user", "retail").status_code, 404)
        self.assertEqual(self.plan_of("ret.user"), "free")

    def test_a_failed_payment_changes_nothing_and_is_recorded(self) -> None:
        order = self.order()
        self.assertEqual(self.verify(order["order_id"], self.pay(order["order_id"], "failed")).status_code, 402)
        self.assertEqual(self.plan_of(), "free")
        with self.Session() as db:
            self.assertEqual(db.query(PaymentTransaction).one().status, "failed")

    def test_replaying_a_payment_does_not_apply_it_twice(self) -> None:
        order = self.order()
        payment = self.pay(order["order_id"])
        self.assertEqual(self.verify(order["order_id"], payment).status_code, 200)
        with self.Session() as db:
            _, _, period = current_state(db, "bank.user")
            period.credits_used = 7
            db.commit()
        again = self.verify(order["order_id"], payment)
        self.assertTrue(again.json()["already_applied"])
        with self.Session() as db:
            self.assertEqual(current_state(db, "bank.user")[2].credits_used, 7)
        other = self.pay(order["order_id"])
        self.assertEqual(self.verify(order["order_id"], other).status_code, 409)

    def test_a_plan_without_a_price_cannot_be_bought(self) -> None:
        response = self.client.post("/v1/billing/checkout", json={"plan_code": "pro", "billing_cycle": "monthly"}, headers=self.headers())
        self.assertEqual(response.status_code, 409)
        self.assertIn("No monthly price", response.json()["detail"])

    def test_free_cannot_be_bought_and_lower_plans_need_a_downgrade(self) -> None:
        self.assertEqual(self.client.post("/v1/billing/checkout", json={"plan_code": "free", "billing_cycle": "monthly"}, headers=self.headers()).status_code, 409)
        order = self.order()
        self.verify(order["order_id"], self.pay(order["order_id"]))
        with self.Session() as db:
            db.get(SubscriptionPlan, "free").monthly_price_minor = 100
            db.commit()
        self.assertEqual(self.client.post("/v1/billing/checkout", json={"plan_code": "free", "billing_cycle": "monthly"}, headers=self.headers()).status_code, 409)

    def test_the_mock_gateway_is_refused_outside_demo_mode_and_real_names_do_not_pretend(self) -> None:
        with patch.object(settings, "demo_mode", False):
            self.assertIsNone(get_gateway())
            self.assertEqual(self.client.post("/v1/billing/checkout", json={"plan_code": "plus", "billing_cycle": "monthly"}, headers=self.headers()).status_code, 503)
        for name in ("razorpay", "stripe", ""):
            with patch.object(settings, "payment_gateway", name):
                self.assertIsNone(get_gateway(), name)

    def test_the_mock_signature_depends_on_every_field(self) -> None:
        gateway = MockGateway()
        payment = gateway.simulate("o1", "paid", 100, "INR")
        self.assertEqual(gateway.verify("o1", payment).amount_minor, 100)
        for field, value in (("status", "failed"), ("amount_minor", "101"), ("currency", "USD"), ("payment_id", "z")):
            with self.assertRaises(PaymentError):
                gateway.verify("o1", {**payment, field: value})

    def test_cancel_keeps_access_until_the_period_ends(self) -> None:
        order = self.order()
        self.verify(order["order_id"], self.pay(order["order_id"]))
        self.assertEqual(self.client.post("/v1/subscription/cancel", headers=self.headers()).status_code, 200)
        self.assertEqual(self.plan_of(), "plus")
        with self.Session() as db:
            subscription, _, _ = current_state(db, "bank.user")
            _, plan, _ = current_state(db, "bank.user", subscription.current_period_end + timedelta(seconds=1))
            self.assertEqual(plan.code, "free")

    def test_a_cancel_can_be_withdrawn(self) -> None:
        order = self.order()
        self.verify(order["order_id"], self.pay(order["order_id"]))
        self.client.post("/v1/subscription/cancel", headers=self.headers())
        self.client.post("/v1/subscription/resume", headers=self.headers())
        with self.Session() as db:
            self.assertFalse(db.get(UserSubscription, "bank.user").cancel_at_period_end)

    def test_a_downgrade_takes_effect_when_the_period_ends(self) -> None:
        order = self.order()
        self.verify(order["order_id"], self.pay(order["order_id"]))
        self.assertEqual(self.client.post("/v1/subscription/downgrade", json={"plan_code": "free"}, headers=self.headers()).status_code, 200)
        self.assertEqual(self.plan_of(), "plus")
        with self.Session() as db:
            end = db.get(UserSubscription, "bank.user").current_period_end
            self.assertEqual(current_state(db, "bank.user", end + timedelta(seconds=1))[1].code, "free")

    def test_renewing_keeps_usage_and_extends_the_period(self) -> None:
        first = self.order()
        self.verify(first["order_id"], self.pay(first["order_id"]))
        with self.Session() as db:
            subscription, _, period = current_state(db, "bank.user")
            period.credits_used = 11
            end = subscription.current_period_end
            db.commit()
        second = self.order()
        self.assertEqual(self.verify(second["order_id"], self.pay(second["order_id"])).status_code, 200)
        with self.Session() as db:
            subscription, _, period = current_state(db, "bank.user")
            self.assertGreater(subscription.current_period_end, end)
            self.assertEqual(period.credits_used, 11)

    def test_history_shows_only_your_own_payments(self) -> None:
        order = self.order()
        self.verify(order["order_id"], self.pay(order["order_id"]))
        mine = self.client.get("/v1/billing/history", headers=self.headers()).json()
        self.assertEqual(mine["payments"][0]["status"], "paid")
        self.assertTrue(mine["payments"][0]["created_at"].endswith("Z"))
        self.assertTrue(mine["history"][0]["created_at"].endswith("Z"))
        theirs = self.client.get("/v1/billing/history", headers=self.headers("ret.user", "retail")).json()
        self.assertEqual(theirs["payments"], [])


class AdminTests(Base_):
    def setUp(self) -> None:
        super().setUp()
        self.admin = self.headers("boss", "banking", "admin")

    def plan_body(self, **changes):
        return {"name": "Plus", "tagline": "t", "monthly_price_minor": 100, "yearly_price_minor": 1000, "currency": "inr", "monthly_credits": 777, "max_context_messages": 12, "fair_use_note": "n", "sort_order": 20, "active": True, "features": [{"feature": "forecasting", "enabled": False, "credit_cost": 9, "period_limit": 3}], **changes}

    def test_editing_a_plan_is_saved_confirmed_and_audited(self) -> None:
        self.client.get("/v1/admin/plans", headers=self.admin)
        saved = self.client.put("/v1/admin/plans/plus", json=self.plan_body(), headers=self.admin)
        self.assertEqual(saved.status_code, 200, saved.text)
        body = saved.json()
        self.assertEqual((body["monthly_credits"], body["currency"], body["allowance_confirmed"]), (777, "INR", True))
        forecasting = next(item for item in body["features"] if item["feature"] == "forecasting")
        self.assertEqual((forecasting["enabled"], forecasting["credit_cost"], forecasting["period_limit"]), (False, 9, 3))
        with self.Session() as db:
            self.assertEqual(db.query(SubscriptionHistory).filter_by(action="plan_edited", actor="boss").count(), 1)

    def test_seeding_never_overwrites_an_edited_plan(self) -> None:
        self.client.get("/v1/admin/plans", headers=self.admin)
        self.client.put("/v1/admin/plans/plus", json=self.plan_body(), headers=self.admin)
        with self.Session() as db:
            subscriptions.ensure_default_plans(db)
            self.assertEqual(db.get(SubscriptionPlan, "plus").monthly_credits, 777)

    def test_a_new_plan_can_be_created(self) -> None:
        self.client.get("/v1/admin/plans", headers=self.admin)
        response = self.client.put("/v1/admin/plans/team", json=self.plan_body(name="Team", sort_order=40), headers=self.admin)
        self.assertEqual(response.status_code, 200, response.text)
        names = [item["name"] for item in self.client.get("/v1/subscription/plans", headers=self.headers()).json()["plans"]]
        self.assertEqual(names, ["Free", "Plus", "Pro", "Team"])

    def test_invalid_plan_values_are_refused(self) -> None:
        for change in ({"monthly_price_minor": -1}, {"monthly_credits": -5}, {"max_context_messages": 999}, {"unknown": 1}, {"features": [{"feature": "made_up", "enabled": True, "credit_cost": 1}]}):
            self.assertEqual(self.client.put("/v1/admin/plans/plus", json=self.plan_body(**change), headers=self.admin).status_code, 422, change)

    def test_free_cannot_be_disabled(self) -> None:
        self.client.get("/v1/admin/plans", headers=self.admin)
        self.assertEqual(self.client.put("/v1/admin/plans/free", json=self.plan_body(name="Free", active=False), headers=self.admin).status_code, 409)

    def test_an_adjustment_changes_the_allowance_and_is_in_the_ledger(self) -> None:
        response = self.client.post("/v1/admin/users/bank.user/credits", json={"credits": 50, "reason": "pilot extension"}, headers=self.admin)
        self.assertEqual(response.json()["credits_allowance"], 150)
        with self.Session() as db:
            row = db.query(UsageTransaction).filter_by(kind="adjustment").one()
            self.assertEqual((row.credits, row.actor, row.reason), (50, "boss", "pilot extension"))

    def test_a_large_negative_adjustment_cannot_make_the_allowance_negative(self) -> None:
        self.client.post("/v1/admin/users/bank.user/credits", json={"credits": -500, "reason": "correction"}, headers=self.admin)
        with self.Session() as db:
            self.assertEqual(percent_used(current_state(db, "bank.user")[2]), 100)

    def test_unknown_users_are_refused(self) -> None:
        self.assertEqual(self.client.post("/v1/admin/users/nobody/credits", json={"credits": 1, "reason": "test"}, headers=self.admin).status_code, 404)
        self.assertEqual(self.client.post("/v1/admin/users/nobody/subscription", json={"plan_code": "pro", "reason": "test"}, headers=self.admin).status_code, 404)

    def test_a_card_never_presents_an_unbuilt_feature_as_available(self) -> None:
        plans = self.client.get("/v1/subscription/plans", headers=self.headers()).json()["plans"]
        for plan in plans:
            flags = {item["feature"]: item["available"] for item in plan["features"]}
            for feature in ("image_generation", "deep_research", "multi_agent", "advanced_reasoning", "priority_processing"):
                self.assertFalse(flags[feature], feature)
            self.assertNotIn("unlimited", json.dumps(plan).lower().replace("not unlimited", ""))


class ProviderErrorTests(unittest.TestCase):
    def test_only_an_error_naming_a_quota_counts_as_exhausted(self) -> None:
        quota = "429 RESOURCE_EXHAUSTED. You exceeded your current quota. Please retry in 29.5s. 'retryDelay': '29s'"
        self.assertEqual(classify_provider_error(quota), ("quota_exhausted", 29))
        self.assertEqual(classify_provider_error("Error code: 429 - model is overloaded")[0], "rate_limited")
        self.assertEqual(classify_provider_error("500 token repeat limit reached"), (None, None))

    def test_gemini_totals_include_thinking_tokens_when_reported(self) -> None:
        with llm.trace_steps() as steps:
            llm._call_info.set({})
            llm._note_usage(10, 5, 40)
            llm._record_llm("synthesis", "google", True, 0.0)
        self.assertEqual(steps[0]["total_tokens"], 40)
        with llm.trace_steps() as steps:
            llm._call_info.set({})
            llm._note_usage(10, 5)
            llm._record_llm("synthesis", "ollama", True, 0.0)
        self.assertEqual(steps[0]["total_tokens"], 15)


if __name__ == "__main__":
    unittest.main()
