"""Versioned tool contracts for the Ask AI workflow and the MCP server.

Tools read only what the authenticated user may see. The user comes from ToolContext (set by the
server after authentication), never from tool arguments, and no tool accepts SQL from a caller.
Generated SQL goes through the orchestrator's validation pipeline, not through a tool here.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from ..auth import AuthUser
from ..llm import record_step
from ..models import Conversation, HitlReview
from ..sensitive_data import is_sensitive_column
from ..warehouse import execute_readonly
from . import access
from .guardrail import GuardrailError
from .knowledge import search_knowledge
from .metrics import calculate_metric, match_metric

logger = logging.getLogger(__name__)
_ALL_ROLES = ("admin", "analyst")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArguments(_Strict):
    pass


class ListArguments(_Strict):
    limit: int = Field(default=10, ge=1, le=50)


class MetricArguments(_Strict):
    question: str = Field(min_length=3, max_length=2000)


class KnowledgeArguments(_Strict):
    query: str = Field(min_length=2, max_length=500)
    limit: int = Field(default=3, ge=1, le=10)


class ToolResult(BaseModel):
    ok: bool
    tool: str
    version: str
    data: Any = None
    error_code: Literal["invalid_arguments", "forbidden", "unavailable", "failed"] | None = None
    error: str | None = None


@dataclass
class ToolContext:
    db: Session
    user: AuthUser
    schema_tables: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    execute: Callable[..., list[dict[str, Any]]] = execute_readonly


@dataclass(frozen=True)
class ToolSpec:
    name: str
    version: str
    description: str
    arguments: type[BaseModel]
    handler: Callable[[ToolContext, Any], Any]
    roles: tuple[str, ...] = _ALL_ROLES
    source: str = "postgres"


def _my_access(context: ToolContext, _arguments: NoArguments) -> dict[str, Any]:
    return {
        **access.domain_overview(context.db, context.user),
        "requests": access.list_requests(context.db, username=context.user.username, limit=20),
    }


def _my_conversations(context: ToolContext, arguments: ListArguments) -> list[dict[str, Any]]:
    query = context.db.query(Conversation).filter(
        Conversation.username == context.user.username,
        Conversation.domain == context.user.domain,
    )
    if context.user.domain == "healthcare":
        query = query.filter(Conversation.tenant_id == context.user.tenant_id)
    rows = query.order_by(Conversation.updated_at.desc()).limit(arguments.limit).all()
    return [{"id": row.id, "title": row.title, "updated_at": str(row.updated_at)} for row in rows]


def _my_reviews(context: ToolContext, arguments: ListArguments) -> list[dict[str, Any]]:
    rows = (
        context.db.query(HitlReview)
        .filter(HitlReview.username == context.user.username)
        .order_by(HitlReview.id.desc())
        .limit(arguments.limit)
        .all()
    )
    return [
        {
            "audit_id": row.audit_id,
            "question": row.question,
            "status": row.status,
            "reason": row.reason,
            "reviewed_by": row.reviewed_by,
        }
        for row in rows
    ]


def _domain_tables(context: ToolContext, _arguments: NoArguments) -> dict[str, list[str]]:
    return {
        table: [column["name"] for column in columns if not is_sensitive_column(str(column["name"]))]
        for table, columns in sorted(context.schema_tables.items())
    }


def _calculate(context: ToolContext, arguments: MetricArguments) -> dict[str, Any]:
    matched = match_metric(arguments.question, context.user.domain)
    if matched is None:
        return {"matched": False}
    if isinstance(matched, str):
        return {"matched": True, "refusal": matched}
    return {
        "matched": True,
        **calculate_metric(
            matched,
            arguments.question,
            execute=lambda sql, domain, parameters, limit: context.execute(sql, domain, parameters, limit),
        ),
    }


def _knowledge(context: ToolContext, arguments: KnowledgeArguments) -> list[dict[str, Any]]:
    return search_knowledge(context.db, arguments.query, context.user.domain, arguments.limit)


TOOLS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        ToolSpec("postgres.my_access", "1", "The signed-in user's authorized domains and access requests.", NoArguments, _my_access),
        ToolSpec("postgres.my_conversations", "1", "Titles of the signed-in user's conversations in the active domain.", ListArguments, _my_conversations),
        ToolSpec("postgres.my_reviews", "1", "Status of the signed-in user's human-review requests.", ListArguments, _my_reviews),
        ToolSpec("metadata.tables", "1", "Tables and non-sensitive columns of the active domain's governed schema.", NoArguments, _domain_tables, source="unity_catalog"),
        ToolSpec("metrics.calculate", "1", "An approved business metric, computed in code from the warehouse.", MetricArguments, _calculate, source="databricks"),
        ToolSpec("knowledge.search", "1", "Approved definitions and documents for the active domain, with sources.", KnowledgeArguments, _knowledge, source="knowledge"),
    )
}


def invoke_tool(name: str, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    """Validate, authorize, run and audit one tool call. Failures are returned, never fabricated."""
    spec = TOOLS.get(name)
    if spec is None:
        return ToolResult(ok=False, tool=name, version="-", error_code="forbidden", error="This tool is not available.")
    label = f"{spec.name}@{spec.version}"
    started = time.perf_counter()

    def finish(result: ToolResult) -> ToolResult:
        record_step(
            "tool",
            label,
            ok=result.ok,
            detail=result.error,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
        logger.info("tool %s user=%s domain=%s ok=%s", label, context.user.username, context.user.domain, result.ok)
        return result

    if context.user.role not in spec.roles:
        return finish(ToolResult(ok=False, tool=spec.name, version=spec.version, error_code="forbidden", error="This role may not use this tool."))
    try:
        parsed = spec.arguments.model_validate(arguments)
    except ValidationError as exc:
        fields = ", ".join(str(error["loc"][0]) for error in exc.errors() if error["loc"]) or "arguments"
        return finish(ToolResult(ok=False, tool=spec.name, version=spec.version, error_code="invalid_arguments", error=f"Invalid arguments: {fields}."))
    try:
        data = spec.handler(context, parsed)
    except GuardrailError as exc:
        return finish(ToolResult(ok=False, tool=spec.name, version=spec.version, error_code="unavailable", error=str(exc)[:500]))
    except Exception as exc:
        logger.exception("Tool %s failed", label)
        return finish(ToolResult(ok=False, tool=spec.name, version=spec.version, error_code="failed", error=f"The tool failed ({type(exc).__name__})."))
    return finish(ToolResult(ok=True, tool=spec.name, version=spec.version, data=data))
