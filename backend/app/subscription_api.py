"""Subscription, billing and AI usage endpoints. Domain permissions are not read or changed here."""

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from dateutil.relativedelta import relativedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from .auth import AuthUser, get_current_user, require_admin
from .config import settings
from .db import get_db
from .models import (
    AppUser,
    LlmUsageEvent,
    PaymentTransaction,
    PlanFeature,
    SubscriptionHistory,
    SubscriptionPlan,
    UsageNotification,
    UsagePeriod,
    UsageTransaction,
    UserSubscription,
)
from .services.payments import MockGateway, PaymentError, get_gateway
from .services.subscriptions import (
    FEATURES,
    _feature_counts,
    _history,
    apply_plan,
    current_state,
    ensure_default_plans,
    usage_summary,
    utcnow,
)

router = APIRouter(prefix="/v1", tags=["subscriptions"])


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CheckoutBody(_Strict):
    plan_code: str = Field(max_length=30)
    billing_cycle: Literal["monthly", "yearly"]


class VerifyBody(_Strict):
    order_id: str = Field(max_length=120)
    payment: dict[str, str]


class MockPayBody(_Strict):
    order_id: str = Field(max_length=120)
    outcome: Literal["paid", "failed"]


class DowngradeBody(_Strict):
    plan_code: str = Field(max_length=30)


class FeatureBody(_Strict):
    feature: str
    enabled: bool
    credit_cost: int = Field(ge=0, le=100000)
    period_limit: int | None = Field(default=None, ge=0, le=10000000)


class PlanBody(_Strict):
    name: str = Field(min_length=1, max_length=60)
    tagline: str = Field(default="", max_length=200)
    monthly_price_minor: int | None = Field(default=None, ge=0, le=10**10)
    yearly_price_minor: int | None = Field(default=None, ge=0, le=10**11)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    monthly_credits: int = Field(ge=0, le=10**9)
    max_context_messages: int = Field(ge=0, le=24)
    allowed_providers: str | None = Field(default=None, max_length=200)
    fair_use_note: str = Field(default="", max_length=500)
    sort_order: int = Field(default=100, ge=0, le=10000)
    active: bool = True
    features: list[FeatureBody] = Field(default_factory=list)


class AssignBody(_Strict):
    plan_code: str = Field(max_length=30)
    reason: str = Field(min_length=3, max_length=300)


class AdjustBody(_Strict):
    credits: int = Field(ge=-10**8, le=10**8)
    reason: str = Field(min_length=3, max_length=300)


def _require_enabled() -> None:
    if not settings.subscriptions_enabled:
        raise HTTPException(status_code=404, detail="Subscriptions are not enabled.")


def _plan_payload(db: Session, plan: SubscriptionPlan) -> dict[str, Any]:
    rows = {row.feature: row for row in db.query(PlanFeature).filter(PlanFeature.plan_code == plan.code).all()}
    features = []
    for code, meta in FEATURES.items():
        row = rows.get(code)
        features.append({
            "feature": code,
            "label": meta["label"],
            # What the application can do today, whatever a plan's settings say.
            "available": meta["available"],
            "enabled": bool(row and row.enabled),
            "credit_cost": row.credit_cost if row else 0,
            "period_limit": row.period_limit if row else None,
        })
    return {
        "code": plan.code,
        "name": plan.name,
        "tagline": plan.tagline,
        "monthly_price_minor": plan.monthly_price_minor,
        "yearly_price_minor": plan.yearly_price_minor,
        "currency": plan.currency,
        "monthly_credits": plan.monthly_credits,
        "allowance_confirmed": plan.allowance_confirmed,
        "max_context_messages": plan.max_context_messages,
        "allowed_providers": plan.allowed_providers,
        "fair_use_note": plan.fair_use_note,
        "sort_order": plan.sort_order,
        "active": plan.active,
        "features": features,
    }


def _gateway_payload() -> dict[str, Any]:
    gateway = get_gateway()
    return {"available": gateway is not None, "name": gateway.name if gateway else None, "test_mode": bool(gateway and gateway.test_mode)}


@router.get("/subscription/plans")
def list_plans(db: Session = Depends(get_db), user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    _require_enabled()
    ensure_default_plans(db)
    plans = db.query(SubscriptionPlan).filter(SubscriptionPlan.active.is_(True)).order_by(SubscriptionPlan.sort_order).all()
    return {"plans": [_plan_payload(db, plan) for plan in plans], "payments": _gateway_payload()}


@router.get("/subscription/me")
def my_subscription(db: Session = Depends(get_db), user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    if not settings.subscriptions_enabled:
        return {"enabled": False}
    subscription, plan, period = current_state(db, user.username)
    counts = _feature_counts(db, period.id)
    tokens = (
        db.query(
            func.count(),
            func.coalesce(func.sum(LlmUsageEvent.input_tokens), 0),
            func.coalesce(func.sum(LlmUsageEvent.output_tokens), 0),
            func.sum(case((LlmUsageEvent.total_tokens.is_(None), 1), else_=0)),
        )
        .filter(LlmUsageEvent.username == user.username, LlmUsageEvent.created_at >= period.period_start)
        .one()
    )
    payload = _plan_payload(db, plan)
    return {
        "enabled": True,
        "usage": usage_summary(db, user),
        "plan": payload,
        "subscription": {
            "status": subscription.status,
            "billing_cycle": subscription.billing_cycle,
            "current_period_start": subscription.current_period_start.isoformat(),
            "current_period_end": subscription.current_period_end.isoformat(),
            "auto_renew": subscription.auto_renew,
            "cancel_at_period_end": subscription.cancel_at_period_end,
            "scheduled_plan_code": subscription.scheduled_plan_code,
            "source": subscription.source,
        },
        "feature_usage": [
            {"feature": item["feature"], "label": item["label"], "used": counts.get(item["feature"], 0), "limit": item["period_limit"]}
            for item in payload["features"]
            if item["enabled"] and item["available"]
        ],
        # The signed-in user's own consumption. Nobody else's appears here.
        "own_tokens": {"attempts": tokens[0], "input": int(tokens[1]), "output": int(tokens[2]), "attempts_without_token_data": int(tokens[3] or 0)},
        "payments": _gateway_payload(),
    }


@router.post("/subscription/notices/{notice_id}/seen")
def notice_seen(notice_id: int, db: Session = Depends(get_db), user: AuthUser = Depends(get_current_user)) -> dict[str, bool]:
    _require_enabled()
    notice = db.get(UsageNotification, notice_id)
    if notice is None or notice.username != user.username:
        raise HTTPException(status_code=404, detail="Notice not found.")
    notice.seen_at = notice.seen_at or utcnow()
    db.commit()
    return {"ok": True}


def _order_for(db: Session, order_id: str, user: AuthUser) -> PaymentTransaction:
    payment = db.query(PaymentTransaction).filter(PaymentTransaction.gateway_order_id == order_id).with_for_update().one_or_none()
    # Someone else's order looks exactly like one that does not exist.
    if payment is None or payment.username != user.username:
        raise HTTPException(status_code=404, detail="Order not found.")
    return payment


@router.post("/billing/checkout")
def checkout(body: CheckoutBody, db: Session = Depends(get_db), user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    _require_enabled()
    gateway = get_gateway()
    if gateway is None:
        raise HTTPException(status_code=503, detail="Online payment is not available yet. Ask an administrator to change your plan.")
    subscription, current, _ = current_state(db, user.username)
    plan = db.get(SubscriptionPlan, body.plan_code)
    if plan is None or not plan.active:
        raise HTTPException(status_code=404, detail="Plan not found.")
    amount = plan.monthly_price_minor if body.billing_cycle == "monthly" else plan.yearly_price_minor
    if amount is None:
        raise HTTPException(status_code=409, detail=f"No {body.billing_cycle} price has been set for {plan.name}.")
    if amount <= 0:
        raise HTTPException(status_code=409, detail=f"{plan.name} is not a paid plan.")
    if plan.sort_order < current.sort_order and subscription.status == "active":
        raise HTTPException(status_code=409, detail="Use a downgrade to move to a lower plan.")
    payment = PaymentTransaction(
        username=user.username, plan_code=plan.code, billing_cycle=body.billing_cycle, amount_minor=amount,
        currency=plan.currency, gateway=gateway.name, gateway_order_id=gateway.create_order(amount, plan.currency),
        status="created",
    )
    db.add(payment)
    db.commit()
    return {
        "order_id": payment.gateway_order_id,
        "amount_minor": amount,
        "currency": plan.currency,
        "plan_code": plan.code,
        "billing_cycle": body.billing_cycle,
        "gateway": gateway.name,
        "test_mode": gateway.test_mode,
    }


@router.post("/billing/mock/pay")
def mock_pay(body: MockPayBody, db: Session = Depends(get_db), user: AuthUser = Depends(get_current_user)) -> dict[str, str]:
    """The mock gateway's checkout page. Its reply still has to pass /billing/verify."""
    _require_enabled()
    gateway = get_gateway()
    if not isinstance(gateway, MockGateway):
        raise HTTPException(status_code=404, detail="Test payments are not enabled.")
    payment = _order_for(db, body.order_id, user)
    return gateway.simulate(payment.gateway_order_id, body.outcome, payment.amount_minor, payment.currency)


@router.post("/billing/verify")
def verify_payment(body: VerifyBody, db: Session = Depends(get_db), user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    _require_enabled()
    gateway = get_gateway()
    if gateway is None:
        raise HTTPException(status_code=503, detail="Online payment is not available yet.")
    payment = _order_for(db, body.order_id, user)
    if payment.gateway != gateway.name:
        raise HTTPException(status_code=409, detail="This order belongs to a different payment gateway.")
    try:
        verified = gateway.verify(payment.gateway_order_id, body.payment)
    except PaymentError as exc:
        payment.failure_reason = str(exc)[:200]
        db.commit()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payment.status == "paid":
        if payment.gateway_payment_id == verified.payment_id:
            return {"status": "paid", "receipt_number": payment.receipt_number, "already_applied": True}
        raise HTTPException(status_code=409, detail="This order has already been paid.")
    if payment.status != "created":
        raise HTTPException(status_code=409, detail="This order can no longer be paid. Please start again.")
    if verified.amount_minor != payment.amount_minor or verified.currency != payment.currency:
        payment.status = "failed"
        payment.failure_reason = "Amount or currency did not match the order."
        db.commit()
        raise HTTPException(status_code=400, detail="The payment did not match the order, so no plan was changed.")
    payment.gateway_payment_id = verified.payment_id
    if verified.status != "paid":
        payment.status = "failed"
        payment.failure_reason = "The gateway reported a failed payment."
        db.commit()
        raise HTTPException(status_code=402, detail="The payment failed and your plan has not changed. No charge was recorded.")
    plan = db.get(SubscriptionPlan, payment.plan_code)
    subscription, current, _ = current_state(db, user.username)
    now = utcnow()
    payment.status = "paid"
    payment.verified_at = now
    payment.receipt_number = f"RCPT-{uuid4().hex[:12].upper()}"
    months = 12 if payment.billing_cycle == "yearly" else 1
    if current.code == plan.code and subscription.billing_cycle == payment.billing_cycle and not subscription.auto_renew:
        # A renewal keeps what is left of the period already paid for, and the usage so far.
        subscription.current_period_end += relativedelta(months=months)
        subscription.cancel_at_period_end = False
        subscription.scheduled_plan_code = None
        subscription.status = "active"
        _history(db, user.username, "renewed", user.username, plan.code, plan.code, f"Payment {payment.receipt_number}")
    else:
        action = "upgraded" if plan.sort_order > current.sort_order else "changed"
        apply_plan(db, subscription, plan, cycle=payment.billing_cycle, actor=user.username, action=action, auto_renew=False, source="payment", now=now, detail=f"Payment {payment.receipt_number}")
    db.commit()
    return {"status": "paid", "receipt_number": payment.receipt_number, "plan_code": plan.code}


@router.post("/subscription/cancel")
def cancel(db: Session = Depends(get_db), user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    _require_enabled()
    subscription, plan, _ = current_state(db, user.username)
    if plan.code == "free":
        raise HTTPException(status_code=409, detail="The Free plan has nothing to cancel.")
    subscription.cancel_at_period_end = True
    _history(db, user.username, "cancel_requested", user.username, plan.code, None, "Access continues until the period ends.")
    db.commit()
    return {"ok": True, "ends_at": subscription.current_period_end.isoformat()}


@router.post("/subscription/resume")
def resume(db: Session = Depends(get_db), user: AuthUser = Depends(get_current_user)) -> dict[str, bool]:
    _require_enabled()
    subscription, plan, _ = current_state(db, user.username)
    subscription.cancel_at_period_end = False
    subscription.scheduled_plan_code = None
    _history(db, user.username, "cancel_withdrawn", user.username, plan.code, plan.code)
    db.commit()
    return {"ok": True}


@router.post("/subscription/downgrade")
def downgrade(body: DowngradeBody, db: Session = Depends(get_db), user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    _require_enabled()
    subscription, plan, _ = current_state(db, user.username)
    target = db.get(SubscriptionPlan, body.plan_code)
    if target is None or not target.active:
        raise HTTPException(status_code=404, detail="Plan not found.")
    if target.sort_order >= plan.sort_order:
        raise HTTPException(status_code=409, detail="That is not a lower plan.")
    subscription.scheduled_plan_code = target.code
    _history(db, user.username, "downgrade_scheduled", user.username, plan.code, target.code, "Takes effect when the period ends.")
    db.commit()
    return {"ok": True, "effective_at": subscription.current_period_end.isoformat()}


def _utc(value: datetime | None) -> str | None:
    # Stored as naive UTC. Without the marker a browser would read it as local time.
    return f"{value.isoformat()}Z" if value else None


def _payment_payload(row: PaymentTransaction) -> dict[str, Any]:
    return {
        "order_id": row.gateway_order_id,
        "username": row.username,
        "plan_code": row.plan_code,
        "billing_cycle": row.billing_cycle,
        "amount_minor": row.amount_minor,
        "currency": row.currency,
        "gateway": row.gateway,
        "status": row.status,
        "receipt_number": row.receipt_number,
        "failure_reason": row.failure_reason,
        "created_at": _utc(row.created_at),
        "verified_at": _utc(row.verified_at),
    }


def _history_payload(row: SubscriptionHistory) -> dict[str, Any]:
    return {
        "username": row.username, "action": row.action, "actor": row.actor, "from_plan": row.from_plan,
        "to_plan": row.to_plan, "detail": row.detail, "created_at": _utc(row.created_at),
    }


@router.get("/billing/history")
def billing_history(db: Session = Depends(get_db), user: AuthUser = Depends(get_current_user)) -> dict[str, Any]:
    _require_enabled()
    payments = db.query(PaymentTransaction).filter(PaymentTransaction.username == user.username).order_by(PaymentTransaction.id.desc()).limit(100).all()
    history = db.query(SubscriptionHistory).filter(SubscriptionHistory.username == user.username).order_by(SubscriptionHistory.id.desc()).limit(100).all()
    return {"payments": [_payment_payload(row) for row in payments], "history": [_history_payload(row) for row in history]}


# Administration

@router.get("/admin/plans")
def admin_plans(db: Session = Depends(get_db), admin: AuthUser = Depends(require_admin)) -> dict[str, Any]:
    _require_enabled()
    ensure_default_plans(db)
    plans = db.query(SubscriptionPlan).order_by(SubscriptionPlan.sort_order).all()
    return {"plans": [_plan_payload(db, plan) for plan in plans], "catalog": FEATURES}


@router.put("/admin/plans/{code}")
def save_plan(code: str, body: PlanBody, db: Session = Depends(get_db), admin: AuthUser = Depends(require_admin)) -> dict[str, Any]:
    _require_enabled()
    if not code.isidentifier() or len(code) > 30:
        raise HTTPException(status_code=422, detail="A plan code uses letters, digits and underscores.")
    unknown = [item.feature for item in body.features if item.feature not in FEATURES]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown features: {', '.join(unknown)}")
    if code == "free" and not body.active:
        raise HTTPException(status_code=409, detail="The Free plan cannot be disabled: users fall back to it.")
    plan = db.get(SubscriptionPlan, code)
    created = plan is None
    if created:
        plan = SubscriptionPlan(code=code)
        db.add(plan)
    for field in ("name", "tagline", "monthly_price_minor", "yearly_price_minor", "monthly_credits", "max_context_messages", "allowed_providers", "fair_use_note", "sort_order", "active"):
        setattr(plan, field, getattr(body, field))
    plan.currency = body.currency.upper()
    plan.allowance_confirmed = True
    plan.updated_by = admin.username
    plan.updated_at = utcnow()
    db.flush()
    for item in body.features:
        row = db.query(PlanFeature).filter(PlanFeature.plan_code == code, PlanFeature.feature == item.feature).one_or_none()
        if row is None:
            row = PlanFeature(plan_code=code, feature=item.feature)
            db.add(row)
        row.enabled, row.credit_cost, row.period_limit = item.enabled, item.credit_cost, item.period_limit
    _history(db, "*", "plan_created" if created else "plan_edited", admin.username, None, code)
    db.commit()
    return _plan_payload(db, plan)


def _known_user(db: Session, username: str) -> None:
    if db.query(AppUser.id).filter(AppUser.username == username).first() is None:
        raise HTTPException(status_code=404, detail="User not found.")


@router.post("/admin/users/{username}/subscription")
def assign_plan(username: str, body: AssignBody, db: Session = Depends(get_db), admin: AuthUser = Depends(require_admin)) -> dict[str, Any]:
    """Changes the plan only. Domain grants stay exactly as they are."""
    _require_enabled()
    _known_user(db, username)
    ensure_default_plans(db)
    plan = db.get(SubscriptionPlan, body.plan_code)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found.")
    subscription, _, _ = current_state(db, username)
    apply_plan(db, subscription, plan, cycle="none", actor=admin.username, action="admin_assigned", auto_renew=True, source="admin", detail=body.reason)
    db.commit()
    return {"ok": True, "plan_code": plan.code}


@router.post("/admin/users/{username}/credits")
def adjust_credits(username: str, body: AdjustBody, db: Session = Depends(get_db), admin: AuthUser = Depends(require_admin)) -> dict[str, Any]:
    _require_enabled()
    _known_user(db, username)
    _, _, period = current_state(db, username)
    period.credits_adjustment += body.credits
    db.add(UsageTransaction(
        idempotency_key=f"adjust:{uuid4().hex}", username=username, domain=None, period_id=period.id, feature=None,
        kind="adjustment", credits=body.credits, actor=admin.username, reason=body.reason,
    ))
    _history(db, username, "credits_adjusted", admin.username, period.plan_code, period.plan_code, f"{body.credits:+d}: {body.reason}")
    db.commit()
    return {"ok": True, "credits_allowance": period.credits_allocated + period.credits_adjustment}


@router.get("/admin/subscriptions")
def admin_subscriptions(db: Session = Depends(get_db), admin: AuthUser = Depends(require_admin)) -> dict[str, Any]:
    _require_enabled()
    accounts = db.query(AppUser.username, AppUser.domain).order_by(AppUser.username).limit(1000).all()
    rows = []
    for username, primary in accounts:
        subscription, plan, period = current_state(db, username)
        rows.append({
            "username": username,
            "administrator": primary == "all",
            "plan_code": plan.code,
            "plan_name": plan.name,
            "status": subscription.status,
            "billing_cycle": subscription.billing_cycle,
            "source": subscription.source,
            "period_end": subscription.current_period_end.isoformat(),
            "credits_used": period.credits_used,
            "credits_allowance": max(0, period.credits_allocated + period.credits_adjustment),
        })
    history = db.query(SubscriptionHistory).order_by(SubscriptionHistory.id.desc()).limit(100).all()
    payments = db.query(PaymentTransaction).order_by(PaymentTransaction.id.desc()).limit(100).all()
    return {"users": rows, "history": [_history_payload(row) for row in history], "payments": [_payment_payload(row) for row in payments]}


def _configured_rates() -> dict[str, tuple[float, float]]:
    """`provider:model=input,output` per million tokens. Absent means the cost is unknown, not zero."""
    rates: dict[str, tuple[float, float]] = {}
    for item in settings.llm_cost_per_million_tokens.split(";"):
        name, _, values = item.strip().partition("=")
        parts = values.split(",")
        if name and len(parts) == 2:
            try:
                rates[name] = (float(parts[0]), float(parts[1]))
            except ValueError:
                continue
    return rates


@router.get("/admin/usage")
def admin_usage(days: int = Query(default=30, ge=1, le=400), db: Session = Depends(get_db), admin: AuthUser = Depends(require_admin)) -> dict[str, Any]:
    """Provider usage as reported per attempt, kept apart from plan credits."""
    _require_enabled()
    since = utcnow() - timedelta(days=days)
    events = db.query(LlmUsageEvent).filter(LlmUsageEvent.created_at >= since).order_by(LlmUsageEvent.id.desc()).limit(50000).all()
    rates = _configured_rates()

    def bucket() -> dict[str, Any]:
        return {"attempts": 0, "retries": 0, "failed": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "without_token_data": 0, "latency_ms": 0, "cost": None}

    groups: dict[str, dict[str, dict[str, Any]]] = {name: defaultdict(bucket) for name in ("model", "domain", "user", "day", "month")}
    quota_events = []
    for event in events:
        engine = f"{event.provider}:{event.model}"
        rate = rates.get(engine)
        keys = {"model": engine, "domain": event.domain, "user": event.username, "day": event.created_at.strftime("%Y-%m-%d"), "month": event.created_at.strftime("%Y-%m")}
        for name, key in keys.items():
            row = groups[name][key]
            row["attempts"] += 1
            row["retries"] += event.retries or 0
            row["failed"] += 0 if event.ok else 1
            row["input_tokens"] += event.input_tokens or 0
            row["output_tokens"] += event.output_tokens or 0
            row["total_tokens"] += event.total_tokens or 0
            row["without_token_data"] += 0 if event.input_tokens is not None or event.output_tokens is not None else 1
            row["latency_ms"] += event.latency_ms or 0
            if rate is not None and (event.input_tokens is not None or event.output_tokens is not None):
                # A provider's total can exceed input plus output: Gemini's includes thinking tokens,
                # which are charged at the output rate.
                billed_output = max(event.output_tokens or 0, (event.total_tokens or 0) - (event.input_tokens or 0))
                cost = ((event.input_tokens or 0) * rate[0] + billed_output * rate[1]) / 1_000_000
                row["cost"] = (row["cost"] or 0) + cost
        if event.error_kind in {"quota_exhausted", "rate_limited"} and len(quota_events) < 100:
            quota_events.append({
                "provider": event.provider, "model": event.model, "kind": event.error_kind,
                "retry_after_seconds": event.retry_after_seconds, "domain": event.domain,
                "username": event.username, "created_at": _utc(event.created_at),
            })
    credits = (
        db.query(UsageTransaction.feature, func.count(), func.coalesce(func.sum(UsageTransaction.credits), 0))
        .filter(UsageTransaction.kind == "charge", UsageTransaction.created_at >= since)
        .group_by(UsageTransaction.feature)
        .all()
    )
    return {
        "days": days,
        "note": "Tokens are as reported by each provider. Cost uses configured rates and is empty when none is set. Provider quota is known only from quota errors.",
        "by_model": [{"key": key, **value} for key, value in sorted(groups["model"].items())],
        "by_domain": [{"key": key, **value} for key, value in sorted(groups["domain"].items())],
        "by_user": [{"key": key, **value} for key, value in sorted(groups["user"].items())],
        "by_day": [{"key": key, **value} for key, value in sorted(groups["day"].items())],
        "by_month": [{"key": key, **value} for key, value in sorted(groups["month"].items())],
        "quota_events": quota_events,
        "plan_credits_by_feature": [{"feature": feature, "charges": count, "credits": int(total)} for feature, count, total in credits],
    }
