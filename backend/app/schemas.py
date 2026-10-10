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
        pattern="^(auto|openai|grok|xai|anthropic|ollama)$",
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
