from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from mcp.server.fastmcp import FastMCP

from .auth import AuthUser, get_current_user
from .config import settings
from .db import SessionLocal
from .services.orchestrator import Orchestrator

mcp = FastMCP("FD AIBI business assistant")
orchestrator = Orchestrator()


def _authenticated_user(db) -> AuthUser:
    if not settings.mcp_access_token:
        raise RuntimeError("Set MCP_ACCESS_TOKEN to a valid, domain-scoped application JWT.")
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials=settings.mcp_access_token,
    )
    return get_current_user(credentials, db)


@mcp.tool()
def list_business_tables() -> list[str]:
    """List business table names available to the configured user's domain."""
    with SessionLocal() as db:
        _authenticated_user(db)
        if not orchestrator._ready:
            orchestrator.warmup(db)
        return sorted(orchestrator.graph.tables)


@mcp.tool()
def ask_business(question: str) -> str:
    """Run a guarded, read-only business question as the configured JWT user."""
    if not 3 <= len(question) <= 2000:
        raise ValueError("Business questions must contain between 3 and 2000 characters.")
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
