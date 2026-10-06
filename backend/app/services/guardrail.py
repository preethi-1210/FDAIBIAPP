import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from ..config import settings

FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|copy|attach|pragma|replace|merge|call|execute)\b",
    re.IGNORECASE,
)


class GuardrailError(ValueError):
    pass


def validate_sql(sql: str) -> str:
    cleaned = sql.strip().rstrip(";")
    if not cleaned:
        raise GuardrailError("Empty SQL")
    if ";" in cleaned:
        raise GuardrailError("Multiple statements are not allowed")
    if not re.match(r"^\s*select\b", cleaned, re.IGNORECASE):
        raise GuardrailError("Only SELECT queries are allowed")
    if FORBIDDEN.search(cleaned):
        raise GuardrailError("SQL contains a forbidden keyword")
    if "limit" not in cleaned.lower():
        cleaned = f"{cleaned}\nLIMIT {settings.query_row_limit}"
    return cleaned


def explain_and_execute(engine: Engine, sql: str) -> list[dict[str, Any]]:
    safe_sql = validate_sql(sql)
    try:
        with engine.connect() as conn:
            conn.execute(text(f"EXPLAIN {safe_sql}"))
            result = conn.execute(text(safe_sql))
            rows = [dict(r._mapping) for r in result.fetchmany(settings.query_row_limit)]
            return _jsonable(rows)
    except SQLAlchemyError as exc:
        raise GuardrailError(f"SQL failed guardrail/execution: {exc}") from exc


def _jsonable(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        item = {}
        for k, v in row.items():
            item[k] = v if isinstance(v, (str, int, float, bool)) or v is None else str(v)
        out.append(item)
    return out
