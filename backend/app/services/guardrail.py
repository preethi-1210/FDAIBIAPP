import logging
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlglot import exp, parse
from sqlglot.dialects.dialect import Dialect
from sqlglot.errors import ParseError, TokenError
from sqlglot.tokens import Token, TokenType

from ..config import settings

# sqlglot quotes unsupported statements in its warnings, and SQL does not belong in the logs.
logging.getLogger("sqlglot").setLevel(logging.ERROR)

MAX_ROW_LIMIT = 2000
# Reach outside the warehouse, reveal secrets or identity, call models, or read files. Matched on
# the parsed function name, so spacing, case, comments and quoting make no difference.
_DENIED_FUNCTIONS = frozenset({
    "secret", "try_secret", "list_secrets",
    "java_method", "reflect", "try_reflect",
    "identifier", "cloud_files", "input_file_name",
    "current_user", "session_user", "user", "is_member", "is_account_group_member",
    "load_extension", "readfile", "writefile",
    "pg_sleep", "pg_ls_dir", "pg_stat_file", "pg_terminate_backend", "pg_cancel_backend",
    "lo_import", "lo_export", "dblink", "dblink_exec", "set_config",
    "query_to_xml", "table_to_xml", "schema_to_xml", "database_to_xml",
})
_DENIED_PREFIXES = ("ai_", "http_", "read_", "vector_", "pg_read_", "system$")
# Writes, script execution, and anything else that is not a read of tables.
_DENIED_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter, exp.Command,
    exp.Set, exp.Use, exp.Copy, exp.Into, exp.QueryTransform,
)
_PARSE_FAILURES = (ParseError, TokenError, ValueError, RecursionError)


class GuardrailError(ValueError):
    pass


def effective_row_limit(row_limit: int | None = None) -> int:
    """QUERY_ROW_LIMIT unless the caller asks for its own limit, never above MAX_ROW_LIMIT."""
    requested = settings.query_row_limit if row_limit is None else row_limit
    return max(1, min(requested, MAX_ROW_LIMIT))


def _dialect() -> str:
    if settings.warehouse in {"databricks", "snowflake"}:
        return settings.warehouse
    return "postgres" if settings.database_url.startswith("postgresql") else "sqlite"


def reject_unsafe_sql(tree: exp.Expression) -> None:
    """Raise if a parsed statement contains anything beyond reading tables with safe functions.

    Messages name the construct only: rejected SQL can contain secrets, so it is never repeated.
    """
    if tree.find(*_DENIED_NODES) is not None:
        raise GuardrailError("Only read-only SELECT statements are allowed.")
    for function in tree.find_all(exp.Func):
        name = (function.name if isinstance(function, exp.Anonymous) else function.sql_name()).lower()
        if name in _DENIED_FUNCTIONS or name.startswith(_DENIED_PREFIXES):
            raise GuardrailError(f"Function '{name}' is not allowed.")
        # Catalog functions run arbitrary user code, including system.ai.* and Python UDFs.
        if isinstance(function.parent, exp.Dot) and function.parent.expression is function:
            raise GuardrailError("Functions from a catalog or schema are not allowed.")
    for table in tree.find_all(exp.Table):
        if isinstance(table.this, exp.Identifier) and re.search(r"[/:]", table.name):
            raise GuardrailError("Reading files directly is not allowed.")


def _outer_limit_token(tokens: list[Token]) -> Token | None:
    """The number in the statement's own LIMIT, as opposed to one inside parentheses."""
    depth = 0
    found = None
    for index, token in enumerate(tokens):
        if token.token_type == TokenType.L_PAREN:
            depth += 1
        elif token.token_type == TokenType.R_PAREN:
            depth -= 1
        elif token.token_type == TokenType.LIMIT and depth == 0:
            found = tokens[index + 1]
    return found


def _check_limits(tree: exp.Expression) -> None:
    for node in tree.find_all(exp.Limit, exp.Fetch):
        value = node.args.get("expression") if isinstance(node, exp.Limit) else None
        valid = isinstance(value, exp.Literal) and value.is_int and int(value.this) > 0 and not node.args.get("offset")
        if not valid:
            raise GuardrailError("A row limit must be LIMIT followed by a positive whole number.")


def _check_limit_syntax(tokens: list[Token]) -> None:
    """Forms the parser accepts but gives no usable limit for: a bare LIMIT and LIMIT offset, count."""
    malformed = tokens[-1].token_type == TokenType.LIMIT or any(
        limit.token_type == TokenType.LIMIT
        and number.token_type == TokenType.NUMBER
        and comma.token_type == TokenType.COMMA
        for limit, number, comma in zip(tokens, tokens[1:], tokens[2:])
    )
    if malformed:
        raise GuardrailError("A row limit must be LIMIT followed by a positive whole number.")


def validate_sql(sql: str, max_rows: int | None = None) -> str:
    """The one SQL policy: every statement passes here, and again as it leaves for the warehouse.

    Returns the statement with its result bounded. The original text is kept apart from the limit,
    so parameters and warehouse-specific syntax are unaffected. Limits inside subqueries, CTEs and
    UNION branches do not bound the result, so only the statement's own limit counts.
    """
    if not sql or not sql.strip():
        raise GuardrailError("Empty SQL")
    dialect = _dialect()
    try:
        tokens = Dialect.get_or_raise(dialect).tokenize(sql)
        statements = [
            statement for statement in parse(sql, read=dialect)
            if statement is not None and not isinstance(statement, exp.Semicolon)
        ]
    except _PARSE_FAILURES as exc:
        raise GuardrailError("The SQL could not be parsed, so it was not run.") from exc
    if not statements:
        raise GuardrailError("Empty SQL")
    if len(statements) > 1 or any(";" in comment for token in tokens for comment in token.comments):
        raise GuardrailError("Multiple statements are not allowed")
    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.SetOperation, exp.Subquery)):
        raise GuardrailError("Only SELECT queries are allowed")
    reject_unsafe_sql(tree)
    _check_limits(tree)

    while tokens and tokens[-1].token_type == TokenType.SEMICOLON:
        tokens = tokens[:-1]
    _check_limit_syntax(tokens)
    end = tokens[-1].end + 1  # Also drops a trailing comment, which could swallow an added LIMIT.
    ceiling = MAX_ROW_LIMIT if max_rows is None else effective_row_limit(max_rows)
    if tree.args.get("limit") is None:
        return f"{sql[:end].strip()}\nLIMIT {min(effective_row_limit(), ceiling)}"
    number = _outer_limit_token(tokens)
    if number is None or number.token_type != TokenType.NUMBER:
        raise GuardrailError("A row limit must be LIMIT followed by a positive whole number.")
    if int(number.text) > ceiling:
        return f"{sql[:number.start]}{ceiling}{sql[number.end + 1:end]}".strip()
    return sql[:end].strip()


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
