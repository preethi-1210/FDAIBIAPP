"""Subscription plans, AI allowances and usage accounting. All of it is separate from domain access:
a plan decides how much AI a user may consume, never which business data they may see.

Three things are recorded and never mixed:
- provider usage (llm_usage_events): what each model call consumed, as the provider reported it;
- plan credits (usage_periods, usage_transactions): this application's own allowance;
- provider quota: only what a provider actually said, in a 429 or similar. Credits are not quota.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from dateutil.relativedelta import relativedelta
from fastapi import HTTPException
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..auth import AuthUser
from ..config import settings
from ..models import (
    LlmUsageEvent,
    PlanFeature,
    SubscriptionHistory,
    SubscriptionPlan,
    UsageNotification,
    UsagePeriod,
    UsageTransaction,
    UserSubscription,
)

logger = logging.getLogger(__name__)
_MODEL_PROVIDERS = {"google", "openai", "grok", "anthropic", "ollama"}
# What a plan may be said to include. A feature the application cannot do yet is shown as planned,
# whatever a plan says, so no card can sell it.
FEATURES: dict[str, dict[str, Any]] = {
    "standard_chat": {"label": "Standard Ask AI messages", "available": True},
    "sql_generation": {"label": "Complex SQL analysis", "available": True},
    "forecasting": {"label": "Forecasting and predictive analytics", "available": True},
    "long_context": {"label": "Longer conversation context", "available": True},
    "advanced_reasoning": {"label": "Advanced reasoning", "available": False},
    "deep_research": {"label": "Deep research", "available": False},
    "multi_agent": {"label": "Multi-agent workflows", "available": False},
    "image_generation": {"label": "Image generation", "available": False},
    "priority_processing": {"label": "Priority processing", "available": False},
}
_STAGE_FEATURES = {"sql_generation": "sql_generation", "feature_builder": "forecasting"}
# Placeholders for an administrator to replace. Prices start empty, so nothing can be bought until
# someone has decided what it costs.
_DEFAULT_PLANS: tuple[dict[str, Any], ...] = (
    {
        "code": "free", "name": "Free", "tagline": "Explore Ask AI", "credits": 100, "context": 6, "order": 10,
        "features": {
            "standard_chat": (True, 1, None), "sql_generation": (True, 2, None), "forecasting": (True, 5, 5),
            "long_context": (False, 0, None), "advanced_reasoning": (False, 5, None), "deep_research": (False, 20, None),
            "multi_agent": (False, 10, None), "image_generation": (False, 10, None), "priority_processing": (False, 0, None),
        },
    },
    {
        "code": "plus", "name": "Plus", "tagline": "Higher usage limits for regular business users", "credits": 1000, "context": 12, "order": 20,
        "features": {
            "standard_chat": (True, 1, None), "sql_generation": (True, 2, None), "forecasting": (True, 5, None),
            "long_context": (True, 0, None), "advanced_reasoning": (False, 5, None), "deep_research": (True, 20, None),
            "multi_agent": (True, 10, None), "image_generation": (False, 10, None), "priority_processing": (True, 0, None),
        },
    },
    {
        "code": "pro", "name": "Pro", "tagline": "The highest usage allowance, for power users", "credits": 5000, "context": 24, "order": 30,
        "features": {
            "standard_chat": (True, 1, None), "sql_generation": (True, 2, None), "forecasting": (True, 5, None),
            "long_context": (True, 0, None), "advanced_reasoning": (True, 5, None), "deep_research": (True, 20, None),
            "multi_agent": (True, 10, None), "image_generation": (True, 10, None), "priority_processing": (True, 0, None),
        },
    },
)
_FAIR_USE = "Generous, not unlimited: allowances, rate limits and provider limits still apply."


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def thresholds() -> list[int]:
    values = {int(item) for item in settings.subscription_notice_thresholds.split(",") if item.strip().isdigit()}
    return sorted(value for value in values if 0 < value <= 100)


def ensure_default_plans(db: Session) -> None:
    """Create the starting plans once. Plans an administrator has edited are never overwritten."""
    if db.query(SubscriptionPlan.code).first() is not None:
        return
    for item in _DEFAULT_PLANS:
        db.add(SubscriptionPlan(
            code=item["code"], name=item["name"], tagline=item["tagline"], currency=settings.subscription_currency,
            monthly_credits=item["credits"], max_context_messages=item["context"], sort_order=item["order"],
            fair_use_note=_FAIR_USE, allowance_confirmed=False, active=True, updated_by="system",
            monthly_price_minor=0 if item["code"] == "free" else None,
            yearly_price_minor=0 if item["code"] == "free" else None,
        ))
    try:
        db.flush()  # Plans first: their features refer to them.
        for item in _DEFAULT_PLANS:
            for feature, (enabled, cost, limit) in item["features"].items():
                db.add(PlanFeature(plan_code=item["code"], feature=feature, enabled=enabled, credit_cost=cost, period_limit=limit))
        db.commit()
    except IntegrityError:
        db.rollback()
        # Only a concurrent process creating them is expected. Anything else is a real fault.
        if db.query(SubscriptionPlan.code).first() is None:
            raise


def _history(db: Session, username: str, action: str, actor: str, from_plan: str | None = None, to_plan: str | None = None, detail: str | None = None) -> None:
    db.add(SubscriptionHistory(username=username, action=action, actor=actor, from_plan=from_plan, to_plan=to_plan, detail=detail))


def _month_after(start: datetime, count: int = 1) -> datetime:
    return start + relativedelta(months=count)


def _open_usage_period(db: Session, username: str, plan: SubscriptionPlan, start: datetime) -> UsagePeriod:
    period = UsagePeriod(
        username=username, plan_code=plan.code, period_start=start, period_end=_month_after(start),
        credits_allocated=plan.monthly_credits, credits_adjustment=0, credits_used=0,
    )
    db.add(period)
    db.flush()
    return period


def apply_plan(
    db: Session,
    subscription: UserSubscription,
    plan: SubscriptionPlan,
    *,
    cycle: str,
    actor: str,
    action: str,
    auto_renew: bool,
    source: str,
    now: datetime | None = None,
    period_end: datetime | None = None,
    detail: str | None = None,
) -> None:
    """Put a user on a plan now, with a fresh allowance. The caller commits."""
    now = now or utcnow()
    previous = subscription.plan_code
    open_periods = db.query(UsagePeriod).filter(UsagePeriod.username == subscription.username, UsagePeriod.period_end > now).all()
    for period in open_periods:
        period.period_end = now
    db.flush()
    subscription.plan_code = plan.code
    subscription.status = "active"
    subscription.billing_cycle = cycle
    subscription.current_period_start = now
    subscription.current_period_end = period_end or _month_after(now, 12 if cycle == "yearly" else 1)
    subscription.auto_renew = auto_renew
    subscription.cancel_at_period_end = False
    subscription.scheduled_plan_code = None
    subscription.source = source
    subscription.updated_at = now
    _open_usage_period(db, subscription.username, plan, now)
    _history(db, subscription.username, action, actor, previous, plan.code, detail)


def _roll_subscription(db: Session, subscription: UserSubscription, now: datetime) -> None:
    """A billing period has ended: renew it, or apply what was scheduled, or expire it."""
    free = db.get(SubscriptionPlan, "free")
    scheduled = subscription.scheduled_plan_code
    ending = subscription.plan_code
    if subscription.auto_renew and not subscription.cancel_at_period_end and not scheduled:
        months = 12 if subscription.billing_cycle == "yearly" else 1
        while subscription.current_period_end <= now:
            subscription.current_period_start = subscription.current_period_end
            subscription.current_period_end = _month_after(subscription.current_period_start, months)
        subscription.updated_at = now
        return
    target = db.get(SubscriptionPlan, scheduled) if scheduled else None
    if target is not None and (target.monthly_price_minor or 0) == 0:
        apply_plan(db, subscription, target, cycle="none", actor="system", action="downgraded", auto_renew=True, source="schedule", now=now)
        return
    note = f"{target.name} needs a payment to start." if target is not None else "No renewal payment was received."
    action = "canceled" if subscription.cancel_at_period_end else "expired"
    apply_plan(db, subscription, free, cycle="none", actor="system", action=action, auto_renew=True, source="default", now=now, detail=f"{ending} ended. {note}")


def current_state(db: Session, username: str, now: datetime | None = None) -> tuple[UserSubscription, SubscriptionPlan, UsagePeriod]:
    """The user's subscription and usage period, created or rolled forward as needed."""
    now = now or utcnow()
    ensure_default_plans(db)
    subscription = db.get(UserSubscription, username)
    if subscription is None:
        free = db.get(SubscriptionPlan, "free")
        subscription = UserSubscription(
            username=username, plan_code=free.code, status="active", billing_cycle="none",
            current_period_start=now, current_period_end=_month_after(now), auto_renew=True,
            cancel_at_period_end=False, source="default", updated_at=now,
        )
        db.add(subscription)
        try:
            _open_usage_period(db, username, free, now)
            _history(db, username, "created", "system", None, free.code)
            db.commit()
        except IntegrityError:  # A concurrent request for the same new user won.
            db.rollback()
            subscription = db.get(UserSubscription, username)
    if subscription.current_period_end <= now:
        _roll_subscription(db, subscription, now)
        db.commit()
    plan = db.get(SubscriptionPlan, subscription.plan_code)
    period = (
        db.query(UsagePeriod)
        .filter(UsagePeriod.username == username, UsagePeriod.period_start <= now, UsagePeriod.period_end > now)
        .order_by(UsagePeriod.id.desc())
        .first()
    )
    if period is None:
        # Allowances reset monthly, on yearly plans too, counted from the start of the billing period.
        start = subscription.current_period_start
        while _month_after(start) <= now:
            start = _month_after(start)
        try:
            period = _open_usage_period(db, username, plan, start)
            db.commit()
        except IntegrityError:
            db.rollback()
            period = db.query(UsagePeriod).filter(UsagePeriod.username == username, UsagePeriod.period_start == start).one()
    return subscription, plan, period


def _allowance(period: UsagePeriod) -> int:
    return max(0, period.credits_allocated + period.credits_adjustment)


def percent_used(period: UsagePeriod) -> int:
    allowance = _allowance(period)
    if allowance == 0:
        return 100
    # Rounded down, so 100% is only ever shown when the allowance really is used up.
    return min(100, period.credits_used * 100 // allowance)


def _plans_with_feature(db: Session, feature: str) -> list[str]:
    rows = (
        db.query(SubscriptionPlan.name)
        .join(PlanFeature, PlanFeature.plan_code == SubscriptionPlan.code)
        .filter(PlanFeature.feature == feature, PlanFeature.enabled.is_(True), SubscriptionPlan.active.is_(True))
        .order_by(SubscriptionPlan.sort_order)
        .all()
    )
    return [row[0] for row in rows]


EXHAUSTED_MESSAGE = (
    "Your current plan's AI allowance has been exhausted. Upgrade your plan to continue using AI "
    "capabilities or wait for your next usage reset."
)


def _feature_counts(db: Session, period_id: int) -> dict[str, int]:
    rows = (
        db.query(UsageTransaction.feature, func.count())
        .filter(UsageTransaction.period_id == period_id, UsageTransaction.kind == "charge")
        .group_by(UsageTransaction.feature)
        .all()
    )
    return {feature: count for feature, count in rows if feature}


@dataclass
class UsageGate:
    """What this user's plan allows for one request, read once at its start."""

    username: str
    domain: str
    exempt: bool
    enforce: bool
    plan_code: str
    plan_name: str
    context_limit: int
    remaining: int
    features: dict[str, tuple[bool, int, int | None]]
    counts: dict[str, int]
    available_on: dict[str, list[str]]

    def refusal(self, stage_or_feature: str) -> str | None:
        """Why this stage may not use a model, or None. Rule-based answers never reach this."""
        if self.exempt or not self.enforce:
            return None
        feature = _STAGE_FEATURES.get(stage_or_feature, stage_or_feature if stage_or_feature in FEATURES else "standard_chat")
        enabled, cost, limit = self.features.get(feature, (False, 0, None))
        label = FEATURES[feature]["label"]
        if not enabled:
            plans = self.available_on.get(feature) or []
            where = f"available with the {' and '.join(plans)} plan{'s' if len(plans) > 1 else ''}" if plans else "not part of any current plan"
            return f"{label} is {where}."
        if limit is not None and self.counts.get(feature, 0) >= limit:
            return f"You have used all {limit} {label.lower()} operations in your {self.plan_name} plan for this period."
        if cost > self.remaining:
            return EXHAUSTED_MESSAGE
        return None


def _session_like(db: Session) -> Session:
    # Its own transaction: accounting must survive a rollback of the request, and the reverse.
    bind = db.get_bind()
    # SQLite allows one writer at a time, so a request holding a write would block this session
    # until it timed out. PostgreSQL has no such limit and keeps the two fully separate.
    if bind.dialect.name == "sqlite" and db.in_transaction():
        db.commit()
    return Session(bind=bind, expire_on_commit=False)


def open_gate(db: Session, user: AuthUser) -> UsageGate | None:
    """None means no limits apply: switched off, or accounting itself is failing. A fault in
    accounting never stops someone asking a question; it is logged for an administrator."""
    if not settings.subscriptions_enabled:
        return None
    try:
        with _session_like(db) as session:
            _, plan, period = current_state(session, user.username)
            rows = session.query(PlanFeature).filter(PlanFeature.plan_code == plan.code).all()
            return UsageGate(
                username=user.username,
                domain=user.domain,
                exempt=user.role == "admin",
                enforce=settings.subscription_enforcement,
                plan_code=plan.code,
                plan_name=plan.name,
                context_limit=plan.max_context_messages,
                remaining=_allowance(period) - period.credits_used,
                features={row.feature: (row.enabled, row.credit_cost, row.period_limit) for row in rows},
                counts=_feature_counts(session, period.id),
                available_on={feature: _plans_with_feature(session, feature) for feature in FEATURES},
            )
    except Exception as exc:
        logger.warning("Subscription check failed, continuing without limits: %s", type(exc).__name__)
        return None


def provider_refusal(db: Session, gate: UsageGate | None, provider: str | None) -> str | None:
    """A plan may restrict which providers it reaches. Recorded by plan, not assumed by provider."""
    if gate is None or gate.exempt or not gate.enforce or not provider:
        return None
    with _session_like(db) as session:
        plan = session.get(SubscriptionPlan, gate.plan_code)
        allowed = [item.strip() for item in (plan.allowed_providers or "").split(",") if item.strip()]
    if allowed and provider.lower() not in allowed:
        return f"The {provider} model provider is not included in your {gate.plan_name} plan."
    return None


def feature_used(steps: list[dict[str, Any]], made_prediction: bool) -> str | None:
    """The one feature a finished request is charged for; None if no model or forecast ran."""
    if made_prediction:
        return "forecasting"
    used = [step for step in steps if step.get("ok") and ":" in step.get("engine", "") and step["engine"].split(":")[0] in _MODEL_PROVIDERS]
    if not used:
        return None
    return "sql_generation" if any(step["stage"] == "sql_generation" for step in used) else "standard_chat"


def charge(
    db: Session,
    username: str,
    domain: str,
    feature: str,
    key: str,
    audit_id: str | None = None,
    now: datetime | None = None,
) -> int:
    """Deduct a feature's cost once for this key. Returns the credits deducted by this call."""
    with _session_like(db) as session:
        _, plan, period = current_state(session, username, now)
        row = session.query(PlanFeature).filter(PlanFeature.plan_code == plan.code, PlanFeature.feature == feature).one_or_none()
        cost = row.credit_cost if row is not None else 0
        session.add(UsageTransaction(
            idempotency_key=key, username=username, domain=domain, period_id=period.id, feature=feature,
            kind="charge", credits=cost, audit_id=audit_id, actor="system",
        ))
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            return 0
        # Added in the database, so concurrent requests cannot overwrite each other's deduction.
        session.execute(update(UsagePeriod).where(UsagePeriod.id == period.id).values(credits_used=UsagePeriod.credits_used + cost))
        session.commit()
        session.refresh(period)
        _raise_notifications(session, period)
        return cost


def _raise_notifications(session: Session, period: UsagePeriod) -> None:
    """One notice per threshold per period, and only the highest of several crossed at once."""
    percent = percent_used(period)
    crossed = [value for value in thresholds() if value <= percent]
    if not crossed:
        return
    existing = {row[0] for row in session.query(UsageNotification.threshold).filter(UsageNotification.period_id == period.id).all()}
    now = utcnow()
    for value in crossed:
        if value in existing:
            continue
        session.add(UsageNotification(username=period.username, period_id=period.id, threshold=value, seen_at=None if value == crossed[-1] else now))
        try:
            session.commit()
        except IntegrityError:  # A concurrent request recorded it first.
            session.rollback()


def _next_plan(session: Session, plan: SubscriptionPlan) -> SubscriptionPlan | None:
    return (
        session.query(SubscriptionPlan)
        .filter(SubscriptionPlan.active.is_(True), SubscriptionPlan.sort_order > plan.sort_order)
        .order_by(SubscriptionPlan.sort_order)
        .first()
    )


def notice_message(threshold: int, plan: SubscriptionPlan, next_plan: SubscriptionPlan | None) -> tuple[str, str]:
    """(level, text). These describe the plan allowance, never a provider's quota."""
    if threshold >= 100:
        return "exhausted", EXHAUSTED_MESSAGE
    base = f"You have consumed {threshold}% of your {plan.name} plan AI allowance."
    if threshold >= 90:
        return "critical", f"{base} You are close to your limit."
    if threshold >= 80:
        upgrade = f" Upgrade to {next_plan.name} for a higher allowance." if next_plan else ""
        return "warning", base + upgrade
    return "info", base


def unseen_notices(session: Session, username: str) -> list[dict[str, Any]]:
    rows = (
        session.query(UsageNotification, UsagePeriod)
        .join(UsagePeriod, UsagePeriod.id == UsageNotification.period_id)
        .filter(UsageNotification.username == username, UsageNotification.seen_at.is_(None))
        .order_by(UsageNotification.id)
        .all()
    )
    notices = []
    for notice, period in rows:
        plan = session.get(SubscriptionPlan, period.plan_code)
        level, message = notice_message(notice.threshold, plan, _next_plan(session, plan))
        notices.append({"id": notice.id, "threshold": notice.threshold, "level": level, "message": message, "created_at": notice.created_at.isoformat()})
    return notices


def settle(
    db: Session,
    user: AuthUser,
    gate: UsageGate | None,
    steps: list[dict[str, Any]],
    *,
    status: str,
    made_prediction: bool,
    audit_id: str | None,
) -> dict[str, Any] | None:
    """Charge a finished request and describe the result for the response. Never raises."""
    if gate is None:
        return None
    try:
        feature = feature_used(steps, made_prediction) if status in {"complete", "no_data"} else None
        charged = 0
        if feature and not gate.exempt:
            charged = charge(db, user.username, user.domain, feature, f"ask:{user.username}:{audit_id or uuid4().hex}", audit_id)
        summary = usage_summary(db, user)
        summary.update({"credits_charged": charged, "feature": feature})
        blocked = next((step.get("detail") for step in steps if step.get("plan_blocked")), None)
        if blocked:
            summary["limited_by_plan"] = blocked
        return summary
    except Exception as exc:
        logger.warning("Usage accounting failed for a finished request: %s", type(exc).__name__)
        return None


def usage_summary(db: Session, user: AuthUser) -> dict[str, Any]:
    with _session_like(db) as session:
        subscription, plan, period = current_state(session, user.username)
        exempt = user.role == "admin"
        return {
            "plan": plan.code,
            "plan_name": plan.name,
            "exempt": exempt,
            "percent_used": None if exempt else percent_used(period),
            "credits_used": period.credits_used,
            "credits_allowance": _allowance(period),
            "credits_remaining": max(0, _allowance(period) - period.credits_used),
            "resets_at": period.period_end.isoformat(),
            "notices": [] if exempt else unseen_notices(session, user.username),
            "kind": "plan_allowance",
        }


def record_llm_events(
    db: Session,
    user: AuthUser,
    steps: list[dict[str, Any]],
    *,
    request_id: str | None,
    audit_id: str | None,
) -> None:
    """Persist every provider attempt in a request, failed ones too. Never raises."""
    rows = []
    for step in steps:
        engine = step.get("engine") or ""
        provider, _, model = engine.partition(":")
        if provider not in _MODEL_PROVIDERS or not model:
            continue
        rows.append(LlmUsageEvent(
            username=user.username, domain=user.domain, provider=provider, model=model[:120], stage=step.get("stage") or "llm",
            ok=bool(step.get("ok")), error_kind=step.get("error_kind"), retries=step.get("retries") or 0,
            retry_after_seconds=step.get("retry_after_seconds"), input_tokens=step.get("input_tokens"),
            output_tokens=step.get("output_tokens"), total_tokens=step.get("total_tokens"),
            latency_ms=step.get("latency_ms"), request_id=request_id, audit_id=audit_id,
        ))
    if not rows or not settings.subscriptions_enabled:
        return
    try:
        with _session_like(db) as session:
            session.add_all(rows)
            session.commit()
    except Exception as exc:
        logger.warning("LLM usage could not be recorded: %s", type(exc).__name__)


def require_no_refusal(reason: str | None) -> None:
    if reason:
        # 402 tells the client to offer plans; the established status codes keep their meanings.
        raise HTTPException(status_code=402, detail=reason)
