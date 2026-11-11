"""Optional MCP server. It exposes approved tools over the same services the app uses.

It has no SQL engine of its own: banking metrics and history run through the banking workflow, so
authorization, the parsed SQL policy, result verification and redaction apply unchanged. Each call
re-authenticates the configured token, and errors are raised as MCP tool errors.
"""

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from mcp.server.fastmcp import FastMCP

from .auth import AuthUser, get_current_user
from .config import settings
from .db import SessionLocal
from .sensitive_data import redact_text, require_sensitive_request_access
from .services.banking_agent import BankingAgent
from .services.banking_registry import METRICS, REGISTRY_VERSION
from .services.orchestrator import Orchestrator
from .services.policy import detect_prompt_injection, normalize_input, other_domain_named
from .services.reliability import DeadlineExceededError, request_deadline
from .services.tools import ToolContext, invoke_tool
from .warehouse import execute_readonly

mcp = FastMCP("FD AIBI business assistant")
orchestrator = Orchestrator()
_QUESTION_LIMIT = 2000


def _authenticated_user(db) -> AuthUser:
    if not settings.mcp_access_token:
        raise RuntimeError("Set MCP_ACCESS_TOKEN to a valid, domain-scoped application JWT.")
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials=settings.mcp_access_token,
    )
    try:
        return get_current_user(credentials, db)
    except HTTPException as exc:
        raise RuntimeError(f"Not authorized: {exc.detail}") from exc


def _banking_user(db) -> AuthUser:
    user = _authenticated_user(db)
    if user.domain != "banking":
        raise RuntimeError(f"Not authorized: this tool is for the banking domain, and the token is for {user.domain}.")
    return user


def _invoke(name: str, arguments: dict, db, user: AuthUser):
    """Run a governed tool as the authenticated user; MCP adds no access of its own."""
    context = ToolContext(db, user, orchestrator.graph.domain_tables.get(user.domain, {}))
    result = invoke_tool(name, arguments, context)
    if not result.ok:
        raise RuntimeError(f"{result.error_code}: {result.error}")
    return result.data


def _checked_question(question: str, user: AuthUser) -> str:
    """The same input checks as the chat path, which run before any tool or model."""
    if not 3 <= len(question) <= _QUESTION_LIMIT:
        raise ValueError(f"Questions must contain between 3 and {_QUESTION_LIMIT} characters.")
    cleaned = redact_text(normalize_input(question))
    if detect_prompt_injection(cleaned):
        raise RuntimeError("Refused: this request tries to change the assistant's instructions or controls.")
    named = other_domain_named(cleaned, user.domain)
    if named:
        raise RuntimeError(f"Refused: {named} data is outside the authorized {user.domain} source.")
    try:
        require_sensitive_request_access(cleaned, user.role)
    except HTTPException as exc:
        raise RuntimeError(f"Refused: {exc.detail}") from exc
    return cleaned


def _run_banking(question: str, user: AuthUser) -> dict:
    cleaned = _checked_question(question, user)
    agent = BankingAgent(lambda sql, domain, parameters, limit: execute_readonly(sql, domain, parameters, limit))
    try:
        with request_deadline(settings.ask_deadline_seconds):
            # Model planning stays off: an MCP call either maps to the registry or is refused.
            result = agent.run(cleaned, user, None, None, approved=False)
    except DeadlineExceededError as exc:
        raise RuntimeError(f"Timed out: {exc}") from exc
    if result is None:
        raise RuntimeError("Unsupported: this is not a question the approved banking metrics can answer.")
    if result.needs_review:
        return {"status": "awaiting_review", "answer": "Forecasts for business users require administrator approval in the application.", "history": result.table}
    return {
        "status": result.status,
        "answer": redact_text(result.answer, user.role),
        "rows": result.table,
        "evidence": result.evidence,
        "sql": result.sql,
        "forecast": result.prediction.model_dump() if result.prediction else None,
    }


@mcp.tool()
def banking_schema() -> dict:
    """List the approved banking metrics with their definitions, breakdowns and filter values."""
    with SessionLocal() as db:
        _banking_user(db)
    return {
        "registry_version": REGISTRY_VERSION,
        "metrics": [
            {
                "id": metric.id,
                "label": metric.label,
                "definition": metric.definition,
                "supports_time": metric.supports_time,
                "forecastable": metric.forecastable,
                "dimensions": {dimension.id: list(dimension.values) for dimension in metric.source.dimensions},
                "example": metric.example_question,
            }
            for metric in METRICS
        ],
    }


@mcp.tool()
def banking_metric(question: str) -> dict:
    """Answer a banking metric, breakdown, trend, comparison or forecast from approved queries only."""
    with SessionLocal() as db:
        user = _banking_user(db)
    return _run_banking(question, user)


@mcp.tool()
def banking_history(metric_id: str) -> dict:
    """Return the monthly history of a forecastable banking metric, from its approved query."""
    if metric_id not in {metric.id for metric in METRICS if metric.forecastable}:
        raise ValueError(f"'{metric_id}' has no approved history. Use banking_schema to see forecastable metrics.")
    with SessionLocal() as db:
        user = _banking_user(db)
    metric = next(item for item in METRICS if item.id == metric_id)
    return _run_banking(f"Show the monthly trend of {metric.label.lower()}", user)


@mcp.tool()
def list_business_tables() -> list[str]:
    """List business table names available to the configured user's domain."""
    with SessionLocal() as db:
        user = _authenticated_user(db)
        if not orchestrator._ready:
            orchestrator.warmup(db)
        return sorted(_invoke("metadata.tables", {}, db, user))


@mcp.tool()
def business_metric(question: str) -> dict:
    """Calculate an approved business metric named in the question for the user's domain."""
    with SessionLocal() as db:
        return _invoke("metrics.calculate", {"question": question}, db, _authenticated_user(db))


@mcp.tool()
def search_knowledge(query: str) -> list[dict]:
    """Search approved definitions and documents for the user's domain, with sources."""
    with SessionLocal() as db:
        return _invoke("knowledge.search", {"query": query}, db, _authenticated_user(db))


@mcp.tool()
def ask_business(question: str) -> str:
    """Run a guarded, read-only business question as the configured JWT user."""
    if not settings.mcp_allow_generated_sql:
        raise RuntimeError("Disabled: set MCP_ALLOW_GENERATED_SQL to expose generated SQL over MCP. Use the approved tools instead.")
    if not 3 <= len(question) <= _QUESTION_LIMIT:
        raise ValueError(f"Business questions must contain between 3 and {_QUESTION_LIMIT} characters.")
    with SessionLocal() as db:
        user = _authenticated_user(db)
        if not orchestrator._ready:
            orchestrator.warmup(db)
        try:
            return orchestrator.run(db, question, user).model_dump_json()
        except HTTPException as exc:
            raise RuntimeError(f"Business request denied or unavailable: {exc.detail}") from exc


if __name__ == "__main__":
    mcp.run(transport="stdio")
