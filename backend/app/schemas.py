from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str
    domain: Optional[str] = None


class SignupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[a-zA-Z0-9_.-]+$")
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=12, max_length=128)
    domain: str = Field(pattern=r"^(banking|finance|insurance|retail)$")


class OidcOnboardingRequest(BaseModel):
    # "all" (administrator) is deliberately not accepted here.
    domain: str = Field(pattern=r"^(banking|finance|healthcare|insurance|retail)$")


_BUSINESS_DOMAIN = r"^(banking|finance|healthcare|insurance|retail)$"


class DomainAccessRequestCreate(BaseModel):
    domain: str = Field(pattern=_BUSINESS_DOMAIN)
    reason: str = Field(min_length=10, max_length=500)


class AccessDecision(BaseModel):
    note: Optional[str] = Field(default=None, max_length=500)


class DomainGrantCreate(BaseModel):
    domain: str = Field(pattern=_BUSINESS_DOMAIN)
    note: Optional[str] = Field(default=None, max_length=500)


class RoleUpdate(BaseModel):
    role: str = Field(pattern=r"^(admin|analyst)$")
    primary_domain: Optional[str] = Field(default=None, pattern=_BUSINESS_DOMAIN)


class SwitchDomainRequest(BaseModel):
    domain: str = Field(pattern=_BUSINESS_DOMAIN)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    domain: str
    role: str
    domains: list[str]


class QueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    conversation_id: UUID | None = None
    llm_provider: Optional[str] = Field(
        default=None,
        pattern="^(auto|google|openai|grok|xai|anthropic|ollama)$",
    )


class PredictionPayload(BaseModel):
    type: str
    labels: list[Any] = []
    probabilities: list[float] = []
    values: list[float] = []
    probability_label: Any | None = None


class UncertaintyPayload(BaseModel):
    confidence_range: list[float]


class QueryResponse(BaseModel):
    answer: str
    sql: Optional[str] = None
    table: list[dict[str, Any]] = []
    plan: dict[str, Any] = {}
    schema_used: list[str] = []
    prediction: Optional[PredictionPayload] = None
    uncertainty: Optional[UncertaintyPayload] = None
    error: Optional[str] = None
    domain: Optional[str] = None
    llm_provider: Optional[str] = None
    audit_id: Optional[str] = None
    chart: Optional[dict[str, Any]] = None
    lineage: Optional[dict[str, Any]] = None
    guardrails: Optional[dict[str, Any]] = None
    hitl: Optional[dict[str, Any]] = None
    model_gov: Optional[dict[str, Any]] = None
    conversation_id: UUID | None = None
    # complete | no_data | insufficient_data | needs_clarification | awaiting_review | error
    status: str = "complete"
    # Which engine actually handled each stage (planner, SQL, execution, prediction, synthesis).
    trace: list[dict[str, Any]] = Field(default_factory=list)
    # What the answer rests on: its kind, sources, metric definition, period and row count.
    evidence: dict[str, Any] = Field(default_factory=dict)
    # Plan allowance after this request: credits charged, percent used, new notices. Never provider quota.
    usage: Optional[dict[str, Any]] = None


class ReportPeriod(BaseModel):
    start: str
    end: str
    comparison_start: str
    comparison_end: str
    label: str
    comparison_label: str


class ReportAvailability(BaseModel):
    start: str | None
    end: str | None


class ReportTrendPoint(BaseModel):
    label: str
    value: float | None
    comparison_value: float | None


class ReportMetric(BaseModel):
    key: str
    label: str
    current: float | None
    comparison: float | None
    unit: str
    definition: str
    change_percent: float | None
    higher_is_better: bool | None
    trend: list[ReportTrendPoint] = Field(default_factory=list)


class ReportBreakdown(BaseModel):
    label: str
    value: float


class ReportRow(BaseModel):
    period: str
    category: str
    region: str
    metric: str
    value: float


class ReportFilterOptions(BaseModel):
    categories: list[str]
    regions: list[str]


class ReportResponse(BaseModel):
    domain: str
    domain_label: str
    period: ReportPeriod
    availability: ReportAvailability
    metrics: list[ReportMetric]
    trend: list[ReportTrendPoint]
    categories: list[ReportBreakdown]
    regions: list[ReportBreakdown]
    rows: list[ReportRow]
    filter_options: ReportFilterOptions
    updated_at: str
