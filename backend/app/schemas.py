from typing import Any, Optional

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str
    domain: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    domain: str
    role: str
    domains: list[str]


class QueryRequest(BaseModel):
    question: str = Field(min_length=3)
    llm_provider: Optional[str] = None


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
