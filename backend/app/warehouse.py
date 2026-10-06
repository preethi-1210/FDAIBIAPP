import re
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from .config import settings
from .db import engine
from .services.guardrail import GuardrailError, _jsonable, validate_sql


def warehouse_status() -> dict[str, Any]:
    ready = True
    detail = "SQLite/Postgres via SQLAlchemy"
    connected = True
    warehouse_id = None
    if settings.warehouse == "databricks":
        http_path = _databricks_http_path()
        ready = bool(
            settings.databricks_server_hostname
            and http_path
            and settings.databricks_token
        )
        connected = False
        warehouse_id = settings.databricks_warehouse_id or None
        detail = (
            f"{settings.databricks_catalog}.{settings.databricks_schema}"
            if ready
            else (
                "Set DATABRICKS_SERVER_HOSTNAME, DATABRICKS_TOKEN, and "
                "DATABRICKS_HTTP_PATH or DATABRICKS_WAREHOUSE_ID"
            )
        )
    return {
        "mode": settings.warehouse,
        "ready": ready,
        "connected": connected,
        "detail": detail,
        "warehouse_id": warehouse_id,
        "genie": False,
    }


def execute_readonly(sql: str) -> list[dict[str, Any]]:
    safe_sql = validate_sql(sql)
    if settings.warehouse == "databricks":
        return _databricks_execute_many([safe_sql])[0]
    try:
        with engine.connect() as conn:
            if settings.database_url.startswith(("postgresql", "sqlite")):
                conn.execute(text(f"EXPLAIN {safe_sql}"))
            result = conn.execute(text(safe_sql))
            rows = [dict(r._mapping) for r in result.fetchmany(settings.query_row_limit)]
            return _jsonable(rows)
    except SQLAlchemyError as exc:
        raise GuardrailError(f"SQL failed guardrail/execution: {exc}") from exc


def execute_readonly_batch(queries: list[str]) -> list[list[dict[str, Any]]]:
    safe_queries = [validate_sql(query) for query in queries]
    if settings.warehouse == "databricks":
        return _databricks_execute_many(safe_queries)
    try:
        with engine.connect() as conn:
            results = []
            for sql in safe_queries:
                if settings.database_url.startswith(("postgresql", "sqlite")):
                    conn.execute(text(f"EXPLAIN {sql}"))
                result = conn.execute(text(sql))
                rows = [dict(row._mapping) for row in result.fetchmany(settings.query_row_limit)]
                results.append(_jsonable(rows))
            return results
    except SQLAlchemyError as exc:
        raise GuardrailError(f"SQL failed guardrail/execution: {exc}") from exc


def warehouse_schema_columns() -> dict[str, list[dict[str, str]]]:
    if settings.warehouse != "databricks":
        inspector = inspect(engine)
        return {
            table: [
                {"name": str(column["name"]), "type": str(column["type"])}
                for column in inspector.get_columns(table)
            ]
            for table in inspector.get_table_names()
        }

    catalog = _validate_identifier(settings.databricks_catalog, "DATABRICKS_CATALOG")
    table_schema = settings.databricks_schema.replace("'", "''")
    rows = execute_readonly(
        f"""
            SELECT table_name, column_name, data_type
            FROM {catalog}.information_schema.columns
            WHERE table_schema = '{table_schema}'
            ORDER BY table_name, ordinal_position
        """
    )
    tables: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        table = str(row["table_name"])
        tables.setdefault(table, []).append(
            {"name": str(row["column_name"]), "type": str(row["data_type"])}
        )
    return tables


def test_warehouse_connection() -> dict[str, Any]:
    status = warehouse_status()
    if status["mode"] != "databricks":
        return status
    if not status["ready"]:
        return status
    try:
        tables = ("customers", "orders", "order_items", "products", "claims", "glossary", "live_events")
        checks = ["SELECT 1 AS connection_test"] + [
            f"SELECT * FROM `{table}` LIMIT 0" for table in tables
        ]
        _databricks_execute_many(checks)
    except (GuardrailError, ImportError) as exc:
        message = str(exc).replace(settings.databricks_token, "[redacted]") if settings.databricks_token else str(exc)
        return {**status, "connected": False, "detail": message}
    return {**status, "connected": True, "detail": "Databricks SQL Warehouse connection succeeded"}


def _databricks_http_path() -> str:
    return settings.databricks_http_path or (
        f"/sql/1.0/warehouses/{settings.databricks_warehouse_id}"
        if settings.databricks_warehouse_id
        else ""
    )


def _validate_identifier(identifier: str, setting_name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier):
        raise GuardrailError(f"{setting_name} must be a simple SQL identifier")
    return f"`{identifier}`"


def _databricks_execute_many(queries: list[str]) -> list[list[dict[str, Any]]]:
    if not warehouse_status()["ready"]:
        raise GuardrailError("Databricks warehouse is not configured yet")
    try:
        from databricks import sql as dbsql
    except ImportError as exc:
        raise GuardrailError("Install databricks-sql-connector to use Databricks warehouse") from exc
    try:
        catalog = _validate_identifier(settings.databricks_catalog, "DATABRICKS_CATALOG")
        schema = _validate_identifier(settings.databricks_schema, "DATABRICKS_SCHEMA")
        with dbsql.connect(
            server_hostname=settings.databricks_server_hostname,
            http_path=_databricks_http_path(),
            access_token=settings.databricks_token,
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(f"USE CATALOG {catalog}")
                cur.execute(f"USE SCHEMA {schema}")
                results = []
                for query in queries:
                    cur.execute(query)
                    cols = [column[0] for column in (cur.description or [])]
                    rows = [dict(zip(cols, row)) for row in cur.fetchmany(settings.query_row_limit)]
                    results.append(_jsonable(rows))
                return results
    except GuardrailError:
        raise
    except Exception as exc:
        message = str(exc).replace(settings.databricks_token, "[redacted]") if settings.databricks_token else str(exc)
        raise GuardrailError(f"Databricks SQL connection or query failed: {message}") from exc
