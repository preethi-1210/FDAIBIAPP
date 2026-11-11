"""Restrictive policy for SQL that is about to run, enforced on the parsed statement.

Holds whatever wrote the SQL, a compiled template or a language model. Failure-kind classification
lets callers report what went wrong and decide whether trying again could help.
"""

import re

from sqlglot import exp, parse
from sqlglot.errors import ParseError, TokenError

from .guardrail import MAX_ROW_LIMIT, GuardrailError, reject_unsafe_sql

# Banking's stricter additions to the policy every statement already passes.
_DENIED_FUNCTIONS = re.compile(r"^(?:sql_keywords|stack|explode_outer)$", re.IGNORECASE)
_DENIED_SCHEMAS = frozenset({"information_schema", "sys", "system"})


def enforce_select_policy(
    sql: str,
    allowed_tables: frozenset[str],
    catalog: str,
    schema: str,
) -> str:
    """Accept only one bounded read-only SELECT over approved tables, or raise GuardrailError."""
    try:
        statements = [statement for statement in parse(sql, read="databricks") if statement is not None]
    except (ParseError, TokenError) as exc:
        raise GuardrailError("The SQL could not be parsed, so it was not run.") from exc
    if len(statements) != 1:
        raise GuardrailError("Exactly one SQL statement is allowed.")
    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.Union)):
        raise GuardrailError("Only SELECT statements are allowed.")
    for denied in (exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter, exp.Command, exp.Set, exp.Use):
        if tree.find(denied) is not None:
            raise GuardrailError("Only read-only SELECT statements are allowed.")
    if any(star.find_ancestor(exp.Count) is None for star in tree.find_all(exp.Star)):
        raise GuardrailError("Wildcard column selection is not allowed; select the required columns.")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            raise GuardrailError("Table functions are not allowed.")
        name = table.name.lower()
        if not (table.db or table.catalog) and name in cte_names:
            continue
        if table.db.lower() in _DENIED_SCHEMAS or table.catalog.lower() in _DENIED_SCHEMAS:
            raise GuardrailError("System metadata cannot be queried.")
        if (table.catalog and table.catalog.lower() != catalog) or (table.db and table.db.lower() != schema):
            raise GuardrailError(f"Only {catalog}.{schema} may be queried; '{table.sql()}' is outside it.")
        if name not in allowed_tables:
            raise GuardrailError(f"Table '{table.name}' does not exist or is not approved for this question.")
    reject_unsafe_sql(tree)
    for function in tree.find_all(exp.Func):
        name = function.sql_name() if not isinstance(function, exp.Anonymous) else str(function.name)
        if _DENIED_FUNCTIONS.match(name or ""):
            raise GuardrailError(f"Function '{name}' is not allowed.")
    if tree.find(exp.Join) is not None:
        for join in tree.find_all(exp.Join):
            if (join.kind or "").upper() == "CROSS" or not (join.args.get("on") or join.args.get("using")):
                raise GuardrailError("Every join must state its join condition.")

    limit = tree.args.get("limit")
    if limit is None:
        raise GuardrailError("A row limit is required.")
    value = limit.expression
    if not isinstance(value, exp.Literal) or not value.is_int or int(value.this) > MAX_ROW_LIMIT:
        raise GuardrailError(f"The row limit must be a number no greater than {MAX_ROW_LIMIT}.")
    return sql


def classify_sql_error(message: str) -> str:
    """invalid_query | missing_object | permission_denied | timeout | infrastructure | policy."""
    text = message.lower()
    if any(marker in text for marker in ("unresolved_column", "parse_syntax_error", "datatype_mismatch", "syntax error", "missing_aggregation", "unresolved_routine", "ambiguous_reference")):
        return "invalid_query"
    if any(marker in text for marker in ("table_or_view_not_found", "schema_not_found", "catalog_not_found", "does not exist")):
        return "missing_object"
    if any(marker in text for marker in ("permission_denied", "insufficient_permissions", "not authorized", "access denied", "user does not have")):
        return "permission_denied"
    if any(marker in text for marker in ("statement_timeout", "timed out", "timeout", "cancelled", "canceled", "deadline")):
        return "timeout"
    if any(marker in text for marker in ("connection", "failing repeatedly", "not configured", "ssl", "certificate", "temporarily unavailable", "503", "502", "cannot create the resource", "try again later")):
        return "infrastructure"
    return "policy" if "not allowed" in text or "not approved" in text else "invalid_query"


# Only these may succeed with another attempt at writing the query.
REGENERABLE = frozenset({"invalid_query", "missing_object"})
USER_MESSAGES = {
    "invalid_query": "The generated query was not valid for the banking schema, so no result is available.",
    "missing_object": "That table or column does not exist in the approved banking data.",
    "permission_denied": "The warehouse denied permission for this query.",
    "timeout": "The warehouse query timed out before returning a result.",
    "infrastructure": "The warehouse could not be reached. Please try again shortly.",
    "policy": "The query was not allowed by the data access policy, so it was not run.",
}
