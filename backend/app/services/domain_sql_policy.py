"""Parsed table and column allowlists for model-written SQL, one per business domain.

Every column is resolved through CTEs, subqueries, unions and correlated subqueries with sqlglot
scope analysis, and must belong to an approved table of the user's domain. Anything that cannot be
resolved is refused: this check fails closed, unlike the earlier text-based column check.

Allowlists are reviewed files in app/sql_allowlists/, generated from Unity Catalog names by
generate_sql_allowlists.py. At run time they are intersected with the live schema the question was
given, so a table or column that disappears from the warehouse is refused too.

SQL_ALLOWLIST_MODE: off, shadow (log what would be refused, refuse nothing) or enforce.
"""

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlglot import exp, parse
from sqlglot.errors import OptimizeError, ParseError, SqlglotError, TokenError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope

from ..config import settings
from .guardrail import GuardrailError

logger = logging.getLogger(__name__)

ALLOWLIST_DIR = Path(__file__).resolve().parents[1] / "sql_allowlists"
DOMAINS = ("banking", "finance", "insurance", "retail", "healthcare")
MODES = ("off", "shadow", "enforce")
_DENIED_SCHEMAS = frozenset({"information_schema", "sys", "system"})


@dataclass(frozen=True)
class DomainAllowlist:
    domain: str
    # Columns any authorized user's SQL may reference, per table.
    columns: dict[str, frozenset[str]]
    # Columns only administrators' SQL may reference (sensitive fields), per table.
    restricted: dict[str, frozenset[str]]

    @property
    def tables(self) -> frozenset[str]:
        return frozenset(self.columns)

    def narrowed_to(self, live_tables: dict[str, Any]) -> "DomainAllowlist":
        """Keep only tables and columns that also exist in the live schema the question was given."""
        live = {
            str(table).lower(): {str(column.get("name", "")).lower() for column in columns}
            for table, columns in live_tables.items()
        }
        return DomainAllowlist(
            self.domain,
            {table: columns & live[table] for table, columns in self.columns.items() if table in live},
            {table: columns & live[table] for table, columns in self.restricted.items() if table in live},
        )


class AllowlistMissingError(GuardrailError):
    pass


@lru_cache(maxsize=None)
def load_allowlist(domain: str) -> DomainAllowlist:
    if domain not in DOMAINS:
        raise AllowlistMissingError(f"No SQL allowlist exists for the {domain} domain.")
    path = ALLOWLIST_DIR / f"{domain}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AllowlistMissingError(f"No SQL allowlist exists for the {domain} domain.") from exc
    columns: dict[str, frozenset[str]] = {}
    restricted: dict[str, frozenset[str]] = {}
    for table, entry in data["tables"].items():
        columns[table.lower()] = frozenset(name.lower() for name in entry.get("columns", []))
        restricted[table.lower()] = frozenset(name.lower() for name in entry.get("restricted", []))
    return DomainAllowlist(domain, columns, restricted)


def mode() -> str:
    value = settings.sql_allowlist_mode.strip().lower()
    return value if value in MODES else "enforce"


def _refuse(message: str) -> None:
    raise GuardrailError(message)


def _check_tables(tree: exp.Expression, allowlist: DomainAllowlist, catalog: str, schema: str) -> None:
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            _refuse("Table functions are not allowed.")
        name = table.name.lower()
        if not (table.db or table.catalog) and name in cte_names:
            continue
        if table.db.lower() in _DENIED_SCHEMAS or table.catalog.lower() in _DENIED_SCHEMAS:
            _refuse("System metadata cannot be queried.")
        if (table.catalog and table.catalog.lower() != catalog.lower()) or (table.db and table.db.lower() != schema.lower()):
            _refuse(f"Only the authorized {allowlist.domain} source may be queried; '{table.name}' is outside it.")
        if name not in allowlist.tables:
            _refuse(f"Table '{table.name}' is not approved for {allowlist.domain} questions.")


def _source_of(scope: Scope, qualifier: str) -> Any:
    """The table or derived scope a qualifier names, searching outward for correlated references."""
    current: Scope | None = scope
    while current is not None:
        if qualifier in current.sources:
            return current.sources[qualifier]
        current = current.parent
    return None


def enforce_domain_policy(sql: str, allowlist: DomainAllowlist, catalog: str, schema: str, role: str) -> None:
    """Raise GuardrailError unless every table and column in sql is approved for this domain."""
    try:
        statements = [statement for statement in parse(sql, read="databricks") if statement is not None]
    except (ParseError, TokenError, ValueError, RecursionError) as exc:
        raise GuardrailError("The SQL could not be parsed, so it was not run.") from exc
    if len(statements) != 1:
        _refuse("Exactly one SQL statement is allowed.")
    tree = statements[0]
    if any(star.find_ancestor(exp.Count) is None for star in tree.find_all(exp.Star)):
        _refuse("Select the columns needed; '*' cannot be checked against the approved columns.")
    _check_tables(tree, allowlist, catalog, schema)

    # Qualification works on bare table names; catalog and schema were checked above.
    bare = tree.copy()
    for table in bare.find_all(exp.Table):
        table.set("catalog", None)
        table.set("db", None)
    mapping = {
        table: {column: "STRING" for column in allowlist.columns[table] | allowlist.restricted.get(table, frozenset())}
        for table in allowlist.tables
    }
    try:
        qualified = qualify(
            bare, schema=mapping, dialect="databricks", validate_qualify_columns=True,
            quote_identifiers=False, identify=False, infer_schema=False,
        )
        scopes = list(traverse_scope(qualified))
    except OptimizeError as exc:
        raise GuardrailError(f"The SQL names a column that is not approved for {allowlist.domain} questions: {str(exc)[:160]}") from exc
    except (SqlglotError, ValueError, KeyError, RecursionError) as exc:
        raise GuardrailError("The SQL's columns could not be resolved, so it was not run.") from exc

    for scope in scopes:
        projections = {select.alias_or_name.lower() for select in scope.expression.selects} if isinstance(scope.expression, exp.Select) else set()
        for column in scope.columns:
            name = column.name.lower()
            if not column.table:
                if name in projections:
                    continue
                _refuse(f"Column '{column.name}' could not be resolved to an approved table.")
            source = _source_of(scope, column.table)
            if isinstance(source, Scope):
                continue  # A CTE or subquery column; its own scope's columns are checked.
            if not isinstance(source, exp.Table):
                _refuse(f"Column '{column.name}' could not be resolved to an approved table.")
            table = source.name.lower()
            if name in allowlist.columns.get(table, frozenset()):
                continue
            if name in allowlist.restricted.get(table, frozenset()):
                if role == "admin":
                    continue
                _refuse(f"Column '{column.name}' of {table} is restricted to administrators.")
            _refuse(f"Column '{column.name}' of {table} is not approved for {allowlist.domain} questions.")


def check_model_sql(
    sql: str,
    domain: str,
    catalog: str,
    schema: str,
    role: str,
    live_tables: dict[str, Any],
) -> str | None:
    """Apply the domain allowlist as SQL_ALLOWLIST_MODE says. Returns the refusal reason, if any.

    Raises GuardrailError only in enforce mode. In shadow mode a refusal is logged by reason, never
    with the SQL, and the query continues.
    """
    current = mode()
    if current == "off":
        return None
    try:
        enforce_domain_policy(sql, load_allowlist(domain).narrowed_to(live_tables), catalog, schema, role)
        return None
    except GuardrailError as exc:
        if current == "enforce":
            raise
        logger.warning("SQL allowlist (shadow mode) would refuse a %s query: %s", domain, exc)
        return str(exc)
