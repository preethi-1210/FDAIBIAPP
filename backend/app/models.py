from datetime import date, datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AppUser(Base):
    __tablename__ = "app_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    domain: Mapped[str] = mapped_column(String(40))
    display_name: Mapped[str] = mapped_column(String(120))


class UserDomainTenant(Base):
    __tablename__ = "user_domain_tenants"
    __table_args__ = (
        UniqueConstraint("username", "domain", name="uq_user_domain_tenant"),
        Index("ix_user_domain_tenants_tenant_id", "tenant_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(ForeignKey("app_users.username", ondelete="CASCADE"))
    domain: Mapped[str] = mapped_column(String(40))
    tenant_id: Mapped[str] = mapped_column(String(64))


class UserDomainGrant(Base):
    """An additional business domain an administrator approved for a user.

    The user's primary domain stays in app_users.domain; administrators (domain "all")
    implicitly have every domain and never need grants.
    """

    __tablename__ = "user_domain_grants"
    __table_args__ = (UniqueConstraint("username", "domain", name="uq_user_domain_grant"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(ForeignKey("app_users.username", ondelete="CASCADE"), index=True)
    domain: Mapped[str] = mapped_column(String(40))
    granted_by: Mapped[str] = mapped_column(String(80))
    granted_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow_naive)
    request_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class DomainAccessRequest(Base):
    __tablename__ = "domain_access_requests"
    __table_args__ = (
        # At most one pending request per user and domain, enforced by the database too.
        Index(
            "uq_domain_access_request_pending",
            "username",
            "requested_domain",
            unique=True,
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
        Index("ix_domain_access_requests_status", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(ForeignKey("app_users.username", ondelete="CASCADE"), index=True)
    current_domain: Mapped[str] = mapped_column(String(40))
    requested_domain: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow_naive)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 0 until the requester has viewed the decision; drives the in-app notification badge.
    decision_seen: Mapped[int] = mapped_column(Integer, default=0)


class AccessAuditEvent(Base):
    __tablename__ = "access_audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str] = mapped_column(String(80), index=True)
    action: Mapped[str] = mapped_column(String(40))
    target_username: Mapped[str] = mapped_column(String(80), index=True)
    domain: Mapped[str | None] = mapped_column(String(40), nullable=True)
    request_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow_naive, index=True)


class KnowledgeDocument(Base):
    """An approved knowledge source. Updates raise the version; deletion keeps the record."""

    __tablename__ = "knowledge_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_key: Mapped[str] = mapped_column(String(160), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    domain: Mapped[str] = mapped_column(String(40), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="active")
    approved_by: Mapped[str] = mapped_column(String(80))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow_naive)


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    # JSON array from the embedding model. Empty when the model was unavailable at ingestion.
    embedding: Mapped[str | None] = mapped_column(Text, nullable=True)


class OAuthIdentity(Base):
    __tablename__ = "oauth_identities"
    __table_args__ = (
        UniqueConstraint("issuer", "subject", name="uq_oauth_identity"),
        UniqueConstraint("issuer", "username", name="uq_oauth_user_provider"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    issuer: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(255))
    username: Mapped[str] = mapped_column(ForeignKey("app_users.username", ondelete="CASCADE"))


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(120))
    segment: Mapped[str] = mapped_column(String(40))
    region: Mapped[str] = mapped_column(String(40))
    account_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    signup_date: Mapped[date] = mapped_column(Date)
    churned: Mapped[int] = mapped_column(Integer, default=0)

    orders: Mapped[list["Order"]] = relationship(back_populates="customer")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(40))
    unit_price: Mapped[float] = mapped_column(Float)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(40), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    order_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(40))
    total_amount: Mapped[float] = mapped_column(Float)

    customer: Mapped[Customer] = relationship(back_populates="orders")
    items: Mapped[list["OrderItem"]] = relationship(back_populates="order")


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(40), index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    amount: Mapped[float] = mapped_column(Float)

    order: Mapped[Order] = relationship(back_populates="items")
    product: Mapped[Product] = relationship()


class BankAccount(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(40), index=True, default="banking")
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    account_number: Mapped[str] = mapped_column(String(40), unique=True)
    account_type: Mapped[str] = mapped_column(String(40))
    balance: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    status: Mapped[str] = mapped_column(String(40))
    opened_date: Mapped[date] = mapped_column(Date)

    customer: Mapped[Customer] = relationship()


class BankTransaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(40), index=True, default="banking")
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    transaction_date: Mapped[date] = mapped_column(Date, index=True)
    transaction_type: Mapped[str] = mapped_column(String(40))
    amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    status: Mapped[str] = mapped_column(String(40))
    channel: Mapped[str] = mapped_column(String(40))

    account: Mapped[BankAccount] = relationship()


class BankLoan(Base):
    __tablename__ = "loans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(40), index=True, default="banking")
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    loan_type: Mapped[str] = mapped_column(String(40))
    principal_amount: Mapped[float] = mapped_column(Float)
    outstanding_balance: Mapped[float] = mapped_column(Float)
    interest_rate: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(40))
    origination_date: Mapped[date] = mapped_column(Date)
    maturity_date: Mapped[date] = mapped_column(Date)

    customer: Mapped[Customer] = relationship()


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(40), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    claim_date: Mapped[date] = mapped_column(Date)
    amount: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(40))
    policy_number: Mapped[str | None] = mapped_column(String(40), nullable=True)


class LiveEvent(Base):
    __tablename__ = "live_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(40), index=True)
    event_type: Mapped[str] = mapped_column(String(40))
    amount: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Glossary(Base):
    __tablename__ = "glossary"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    domain: Mapped[str] = mapped_column(String(40), index=True)
    business_term: Mapped[str] = mapped_column(String(80))
    table_name: Mapped[str] = mapped_column(String(80))
    column_name: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text)


class QueryHistory(Base):
    __tablename__ = "query_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80))
    domain: Mapped[str] = mapped_column(String(40))
    tenant_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    question: Mapped[str] = mapped_column(Text)
    sql: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    audit_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    tables_used: Mapped[str | None] = mapped_column(String(240), nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    guardrail_ok: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(80), index=True)
    domain: Mapped[str] = mapped_column(String(40), index=True)
    tenant_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow_naive)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow_naive, index=True)


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
    )
    role: Mapped[str] = mapped_column(String(10))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow_naive)


class ConversationQueryState(Base):
    """The last validated query plan in a conversation, so follow-ups keep its metric and filters."""

    __tablename__ = "conversation_query_states"

    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    plan_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow_naive)


class HitlReview(Base):
    __tablename__ = "hitl_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    audit_id: Mapped[str] = mapped_column(String(40))
    username: Mapped[str] = mapped_column(String(80))
    domain: Mapped[str] = mapped_column(String(40))
    tenant_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    question: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="pending")
    reason: Mapped[str] = mapped_column(String(160), default="prediction_or_sensitive")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    # Approval workflow: the requester's conversation, the reviewing administrator's decision,
    # and when the approved request was resumed (status "completed").
    conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class HealthcareFacility(Base):
    __tablename__ = "healthcare_facilities"
    __table_args__ = (Index("ix_hc_facilities_tenant_region", "tenant_id", "facility_region"),)

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    facility_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    facility_type: Mapped[str] = mapped_column(String(40))
    facility_region: Mapped[str] = mapped_column(String(40))


class HealthcarePatient(Base):
    __tablename__ = "healthcare_patients"
    __table_args__ = (
        Index("ix_hc_patients_tenant_status", "tenant_id", "patient_status"),
        Index("ix_hc_patients_tenant_region", "tenant_id", "postal_region"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    patient_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    age_band: Mapped[str] = mapped_column(String(20))
    sex: Mapped[str] = mapped_column(String(20))
    postal_region: Mapped[str] = mapped_column(String(40))
    enrollment_date: Mapped[date] = mapped_column(Date)
    patient_status: Mapped[str] = mapped_column(String(40))


class HealthcareProvider(Base):
    __tablename__ = "healthcare_providers"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "facility_key"],
            ["healthcare_facilities.tenant_id", "healthcare_facilities.facility_key"],
            ondelete="RESTRICT",
        ),
        Index("ix_hc_providers_tenant_specialty", "tenant_id", "provider_specialty"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider_specialty: Mapped[str] = mapped_column(String(60))
    facility_key: Mapped[str] = mapped_column(String(64))
    provider_region: Mapped[str] = mapped_column(String(40))


class HealthcareEncounter(Base):
    __tablename__ = "healthcare_encounters"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "patient_key"],
            ["healthcare_patients.tenant_id", "healthcare_patients.patient_key"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "provider_key"],
            ["healthcare_providers.tenant_id", "healthcare_providers.provider_key"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "facility_key"],
            ["healthcare_facilities.tenant_id", "healthcare_facilities.facility_key"],
            ondelete="RESTRICT",
        ),
        Index("ix_hc_encounters_tenant_date", "tenant_id", "encounter_date"),
        Index("ix_hc_encounters_tenant_status", "tenant_id", "encounter_status"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    encounter_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    patient_key: Mapped[str] = mapped_column(String(64))
    provider_key: Mapped[str] = mapped_column(String(64))
    facility_key: Mapped[str] = mapped_column(String(64))
    encounter_date: Mapped[date] = mapped_column(Date)
    encounter_type: Mapped[str] = mapped_column(String(40))
    encounter_status: Mapped[str] = mapped_column(String(40))


class HealthcareDiagnosis(Base):
    __tablename__ = "healthcare_diagnoses"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "encounter_key"],
            ["healthcare_encounters.tenant_id", "healthcare_encounters.encounter_key"],
            ondelete="CASCADE",
        ),
        Index("ix_hc_diagnoses_tenant_category", "tenant_id", "diagnosis_category"),
        Index("ix_hc_diagnoses_tenant_date", "tenant_id", "diagnosis_date"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    diagnosis_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    encounter_key: Mapped[str] = mapped_column(String(64))
    diagnosis_code: Mapped[str] = mapped_column(String(40))
    diagnosis_category: Mapped[str] = mapped_column(String(60))
    diagnosis_date: Mapped[date] = mapped_column(Date)


class HealthcareProcedure(Base):
    __tablename__ = "healthcare_procedures"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "encounter_key"],
            ["healthcare_encounters.tenant_id", "healthcare_encounters.encounter_key"],
            ondelete="CASCADE",
        ),
        Index("ix_hc_procedures_tenant_category", "tenant_id", "procedure_category"),
        Index("ix_hc_procedures_tenant_date", "tenant_id", "procedure_date"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    procedure_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    encounter_key: Mapped[str] = mapped_column(String(64))
    procedure_code: Mapped[str] = mapped_column(String(40))
    procedure_category: Mapped[str] = mapped_column(String(60))
    procedure_date: Mapped[date] = mapped_column(Date)


class HealthcareClaim(Base):
    __tablename__ = "healthcare_claims"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "encounter_key"],
            ["healthcare_encounters.tenant_id", "healthcare_encounters.encounter_key"],
            ondelete="CASCADE",
        ),
        Index("ix_hc_claims_tenant_date", "tenant_id", "service_date"),
        Index("ix_hc_claims_tenant_status", "tenant_id", "claim_status"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    claim_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    encounter_key: Mapped[str] = mapped_column(String(64))
    service_date: Mapped[date] = mapped_column(Date)
    claim_amount: Mapped[float] = mapped_column(Float)
    approved_amount: Mapped[float] = mapped_column(Float)
    claim_status: Mapped[str] = mapped_column(String(40))
    payer_key: Mapped[str] = mapped_column(String(64))


class HealthcareQualityMetric(Base):
    __tablename__ = "healthcare_quality_metrics"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "facility_key"],
            ["healthcare_facilities.tenant_id", "healthcare_facilities.facility_key"],
            ondelete="RESTRICT",
        ),
        Index("ix_hc_quality_tenant_date", "tenant_id", "metric_date"),
        Index("ix_hc_quality_tenant_name", "tenant_id", "metric_name"),
    )

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    metric_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    facility_key: Mapped[str] = mapped_column(String(64))
    metric_date: Mapped[date] = mapped_column(Date)
    metric_name: Mapped[str] = mapped_column(String(80))
    metric_value: Mapped[float] = mapped_column(Float)
    reporting_period: Mapped[str] = mapped_column(String(20))


class SubscriptionPlan(Base):
    """A plan an administrator can edit. A missing price means it cannot be bought yet."""

    __tablename__ = "subscription_plans"

    code: Mapped[str] = mapped_column(String(30), primary_key=True)
    name: Mapped[str] = mapped_column(String(60))
    tagline: Mapped[str] = mapped_column(String(200), default="")
    monthly_price_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    yearly_price_minor: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    monthly_credits: Mapped[int] = mapped_column(Integer)
    # False until an administrator saves the plan: the starting values are placeholders.
    allowance_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    max_context_messages: Mapped[int] = mapped_column(Integer, default=12)
    allowed_providers: Mapped[str | None] = mapped_column(String(200), nullable=True)
    fair_use_note: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow_naive)


class PlanFeature(Base):
    __tablename__ = "plan_features"
    __table_args__ = (UniqueConstraint("plan_code", "feature", name="uq_plan_feature"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_code: Mapped[str] = mapped_column(String(30), ForeignKey("subscription_plans.code", ondelete="CASCADE"), index=True)
    feature: Mapped[str] = mapped_column(String(40))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    credit_cost: Mapped[int] = mapped_column(Integer, default=0)
    period_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)


class UserSubscription(Base):
    """One current subscription per user. Unrelated to domain grants: a plan never unlocks data."""

    __tablename__ = "user_subscriptions"

    username: Mapped[str] = mapped_column(String(80), primary_key=True)
    plan_code: Mapped[str] = mapped_column(String(30), ForeignKey("subscription_plans.code"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="active")
    billing_cycle: Mapped[str] = mapped_column(String(10), default="none")
    current_period_start: Mapped[datetime] = mapped_column(DateTime)
    current_period_end: Mapped[datetime] = mapped_column(DateTime, index=True)
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)
    scheduled_plan_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="default")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow_naive)


class UsagePeriod(Base):
    """A monthly allowance and how much of it is used. Yearly plans also reset monthly."""

    __tablename__ = "usage_periods"
    __table_args__ = (UniqueConstraint("username", "period_start", name="uq_usage_period_user_start"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), index=True)
    plan_code: Mapped[str] = mapped_column(String(30))
    period_start: Mapped[datetime] = mapped_column(DateTime)
    period_end: Mapped[datetime] = mapped_column(DateTime)
    credits_allocated: Mapped[int] = mapped_column(Integer)
    credits_adjustment: Mapped[int] = mapped_column(Integer, default=0)
    credits_used: Mapped[int] = mapped_column(Integer, default=0)


class UsageTransaction(Base):
    """The ledger behind credits_used. The unique key is what prevents a double deduction."""

    __tablename__ = "usage_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)
    username: Mapped[str] = mapped_column(String(80), index=True)
    domain: Mapped[str | None] = mapped_column(String(40), nullable=True)
    period_id: Mapped[int] = mapped_column(Integer, ForeignKey("usage_periods.id"), index=True)
    feature: Mapped[str | None] = mapped_column(String(40), nullable=True)
    kind: Mapped[str] = mapped_column(String(20))
    credits: Mapped[int] = mapped_column(Integer)
    audit_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    actor: Mapped[str] = mapped_column(String(80), default="system")
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow_naive, index=True)


class LlmUsageEvent(Base):
    """One provider attempt, as the provider reported it. Holds no question text or results.
    Token columns are NULL when the provider sent none, which is different from zero."""

    __tablename__ = "llm_usage_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), index=True)
    domain: Mapped[str] = mapped_column(String(40), index=True)
    provider: Mapped[str] = mapped_column(String(30), index=True)
    model: Mapped[str] = mapped_column(String(120))
    stage: Mapped[str] = mapped_column(String(40))
    ok: Mapped[bool] = mapped_column(Boolean)
    error_kind: Mapped[str | None] = mapped_column(String(30), nullable=True)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    retry_after_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    audit_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow_naive, index=True)


class UsageNotification(Base):
    """A threshold crossed. The constraint means each is raised once per usage period."""

    __tablename__ = "usage_notifications"
    __table_args__ = (UniqueConstraint("period_id", "threshold", name="uq_usage_notification_period_threshold"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), index=True)
    period_id: Mapped[int] = mapped_column(Integer, ForeignKey("usage_periods.id"))
    threshold: Mapped[int] = mapped_column(Integer)
    seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow_naive)


class SubscriptionHistory(Base):
    __tablename__ = "subscription_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), index=True)
    action: Mapped[str] = mapped_column(String(30))
    actor: Mapped[str] = mapped_column(String(80))
    from_plan: Mapped[str | None] = mapped_column(String(30), nullable=True)
    to_plan: Mapped[str | None] = mapped_column(String(30), nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow_naive, index=True)


class PaymentTransaction(Base):
    """An order and its outcome. Status becomes paid only after server-side verification."""

    __tablename__ = "payment_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), index=True)
    plan_code: Mapped[str] = mapped_column(String(30))
    billing_cycle: Mapped[str] = mapped_column(String(10))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    gateway: Mapped[str] = mapped_column(String(20))
    gateway_order_id: Mapped[str] = mapped_column(String(120), unique=True)
    gateway_payment_id: Mapped[str | None] = mapped_column(String(120), unique=True, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="created")
    receipt_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow_naive, index=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
