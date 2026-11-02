import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Any, Mapping

from sqlalchemy import inspect, text
from sqlalchemy.exc import SQLAlchemyError

from .config import settings
from .db import engine
from .services.guardrail import GuardrailError, _jsonable, validate_sql

logger = logging.getLogger(__name__)

# Opening a Databricks SQL session costs well over a second, so sessions are kept open per
# catalog/schema and reused. Each session is used by one thread at a time; sessions idle
# longer than _DATABRICKS_IDLE_SECONDS are closed rather than reused because the
# warehouse may already have expired them.
_DATABRICKS_IDLE_SECONDS = 600
_DATABRICKS_MAX_IDLE_PER_NAMESPACE = 8
_databricks_pool_lock = Lock()
_databricks_idle: dict[tuple[str, str, str, str], list[tuple[float, Any]]] = {}
# Independent dashboard/report queries run concurrently on separate pooled sessions.
_query_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="warehouse-query")


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
            f"Databricks SQL Warehouse; domain catalogs: {settings.databricks_domain_catalogs}; "
            f"schema: {settings.databricks_domain_schema}"
            if ready
            else (
                "Set DATABRICKS_SERVER_HOSTNAME, DATABRICKS_TOKEN, and "
                "DATABRICKS_HTTP_PATH or DATABRICKS_WAREHOUSE_ID"
            )
        )
    elif settings.warehouse == "snowflake":
        ready = bool(
            settings.snowflake_account
            and settings.snowflake_user
            and settings.snowflake_password
            and settings.snowflake_warehouse
            and settings.snowflake_database
        )
        connected = False
        detail = (
            f"{settings.snowflake_database}.{settings.snowflake_schema}"
            if ready
            else (
                "Set SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PASSWORD, "
                "SNOWFLAKE_WAREHOUSE, and SNOWFLAKE_DATABASE"
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


def execute_readonly(
    sql: str,
    domain: str | None = None,
    parameters: Mapping[str, Any] | None = None,
    row_limit: int | None = None,
) -> list[dict[str, Any]]:
    safe_sql = validate_sql(sql)
    fetch_limit = settings.query_row_limit if row_limit is None else max(1, min(row_limit, 2000))
    if settings.warehouse == "databricks":
        return _databricks_execute_many(
            [safe_sql],
            domain=domain,
            parameter_sets=[parameters or {}],
            row_limit=fetch_limit,
        )[0]
    if settings.warehouse == "snowflake":
        return _snowflake_execute_many(
            [safe_sql],
            parameter_sets=[parameters or {}],
            row_limit=fetch_limit,
        )[0]
    try:
        with engine.connect() as conn:
            if settings.database_url.startswith(("postgresql", "sqlite")):
                conn.execute(text(f"EXPLAIN {safe_sql}"), dict(parameters or {}))
            result = conn.execute(text(safe_sql), dict(parameters or {}))
            rows = [dict(r._mapping) for r in result.fetchmany(fetch_limit)]
            return _jsonable(rows)
    except SQLAlchemyError as exc:
        raise GuardrailError(f"SQL failed guardrail/execution: {exc}") from exc


def execute_readonly_many(
    statements: list[tuple[str, Mapping[str, Any]]],
    domain: str | None = None,
    row_limit: int | None = None,
) -> list[list[dict[str, Any]]]:
    """Run independent parameterized read-only queries; on Databricks they run concurrently."""
    safe = [(validate_sql(sql), parameters) for sql, parameters in statements]
    fetch_limit = settings.query_row_limit if row_limit is None else max(1, min(row_limit, 2000))
    if settings.warehouse == "databricks":
        return _databricks_execute_many(
            [sql for sql, _parameters in safe],
            domain=domain,
            parameter_sets=[parameters or {} for _sql, parameters in safe],
            row_limit=fetch_limit,
        )
    if settings.warehouse == "snowflake":
        return _snowflake_execute_many(
            [sql for sql, _parameters in safe],
            parameter_sets=[parameters or {} for _sql, parameters in safe],
            row_limit=fetch_limit,
        )
    return [
        execute_readonly(sql, domain=domain, parameters=parameters, row_limit=fetch_limit)
        for sql, parameters in safe
    ]


def execute_readonly_batch(
    queries: list[str],
    domain: str | None = None,
) -> list[list[dict[str, Any]]]:
    safe_queries = [validate_sql(query) for query in queries]
    if settings.warehouse == "databricks":
        return _databricks_execute_many(safe_queries, domain=domain)
    if settings.warehouse == "snowflake":
        return _snowflake_execute_many(safe_queries)
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


def databricks_domain_catalog(domain: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", domain):
        raise GuardrailError("Business domain is not a valid Databricks catalog mapping key")
    mappings: dict[str, str] = {}
    for entry in settings.databricks_domain_catalogs.split(","):
        key, separator, value = entry.strip().partition(":")
        if not separator or not re.fullmatch(r"[a-z][a-z0-9_]*", key):
            raise GuardrailError("DATABRICKS_DOMAIN_CATALOGS must use domain:catalog pairs")
        _validate_identifier(value, "DATABRICKS_DOMAIN_CATALOGS")
        mappings[key] = value
    catalog = mappings.get(domain)
    if not catalog:
        raise GuardrailError(
            f"No Databricks catalog is configured for the {domain} domain. "
            "Set DATABRICKS_DOMAIN_CATALOGS to an authorized catalog for that domain."
        )
    return catalog


def databricks_healthcare_tenant_catalog(tenant_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", tenant_id):
        raise GuardrailError("Healthcare tenant is not a valid Databricks catalog mapping key")
    mappings: dict[str, str] = {}
    for entry in settings.databricks_healthcare_tenant_catalogs.split(","):
        if not entry.strip():
            continue
        key, separator, value = entry.strip().partition(":")
        if (
            not separator
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", key)
            or key in mappings
        ):
            raise GuardrailError(
                "DATABRICKS_HEALTHCARE_TENANT_CATALOGS must use unique tenant_id:catalog pairs"
            )
        _validate_identifier(value, "DATABRICKS_HEALTHCARE_TENANT_CATALOGS")
        mappings[key] = value
    catalog = mappings.get(tenant_id)
    if not catalog:
        raise GuardrailError(
            "No Databricks catalog is configured for this Healthcare tenant. "
            "Set DATABRICKS_HEALTHCARE_TENANT_CATALOGS to an authorized tenant_id:catalog pair."
        )
    return catalog


def execute_databricks_domain_batch(
    domain: str,
    queries: list[str],
    tenant_id: str | None = None,
) -> list[list[dict[str, Any]]]:
    if settings.warehouse != "databricks":
        return execute_readonly_batch(queries)
    configured_catalog = (
        databricks_healthcare_tenant_catalog(tenant_id)
        if domain == "healthcare" and tenant_id
        else databricks_domain_catalog(domain)
    )
    catalog = _validate_identifier(
        configured_catalog,
        "DATABRICKS_DOMAIN_CATALOGS",
    )
    schema = _validate_identifier(
        settings.databricks_domain_schema,
        "DATABRICKS_DOMAIN_SCHEMA",
    )
    if not warehouse_status()["ready"]:
        raise GuardrailError("Databricks warehouse is not configured yet")
    safe_queries = [validate_sql(query) for query in queries]
    try:
        return _databricks_run(
            catalog.strip("`"),
            schema.strip("`"),
            safe_queries,
            None,
            settings.query_row_limit,
        )
    except GuardrailError:
        raise
    except Exception as exc:
        message = (
            str(exc).replace(settings.databricks_token, "[redacted]")
            if settings.databricks_token
            else str(exc)
        )
        raise GuardrailError(
            f"Databricks query failed for {domain}.{settings.databricks_domain_schema}: {message}"
        ) from exc


def warehouse_schema_columns(domain: str | None = None) -> dict[str, list[dict[str, str]]]:
    if settings.warehouse == "snowflake":
        database = _validate_identifier(
            settings.snowflake_database, "SNOWFLAKE_DATABASE", quote='"'
        )
        table_schema = settings.snowflake_schema.replace("'", "''")
        rows = execute_readonly(
            f"""
                SELECT table_name, column_name, data_type
                FROM {database}.information_schema.columns
                WHERE UPPER(table_schema) = UPPER('{table_schema}')
                ORDER BY table_name, ordinal_position
                LIMIT {_SCHEMA_ROW_LIMIT}
            """,
            row_limit=_SCHEMA_ROW_LIMIT,
        )
        return _group_schema_columns(rows)
    if settings.warehouse != "databricks":
        inspector = inspect(engine)
        return {
            table: [
                {"name": str(column["name"]), "type": str(column["type"])}
                for column in inspector.get_columns(table)
            ]
            for table in inspector.get_table_names()
        }

    catalog_name = databricks_domain_catalog(domain) if domain else settings.databricks_catalog
    schema_name = settings.databricks_domain_schema if domain else settings.databricks_schema
    catalog = _validate_identifier(catalog_name, "DATABRICKS_CATALOG")
    table_schema = schema_name.replace("'", "''")
    # Each domain schema has several hundred columns. Without an explicit LIMIT the SQL
    # guardrail appends LIMIT 200, which silently dropped about half of every domain's tables.
    rows = execute_readonly(
        f"""
            SELECT table_name, column_name, data_type, comment
            FROM {catalog}.information_schema.columns
            WHERE table_schema = '{table_schema}'
            ORDER BY table_name, ordinal_position
            LIMIT {_SCHEMA_ROW_LIMIT}
        """,
        row_limit=_SCHEMA_ROW_LIMIT,
    )
    if len(rows) >= _SCHEMA_ROW_LIMIT:
        logger.warning("Schema metadata for %s.%s was truncated at %d columns", catalog_name, schema_name, _SCHEMA_ROW_LIMIT)
    return _group_schema_columns(rows)


_SCHEMA_ROW_LIMIT = 2000


def _group_schema_columns(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    tables: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        lowered = {str(key).lower(): value for key, value in row.items()}
        table = str(lowered["table_name"])
        column = {"name": str(lowered["column_name"]), "type": str(lowered["data_type"])}
        # Unity Catalog column comments carry business definitions for SQL generation.
        if lowered.get("comment"):
            column["description"] = str(lowered["comment"])[:300]
        tables.setdefault(table, []).append(column)
    return tables


def test_warehouse_connection(domain: str | None = None) -> dict[str, Any]:
    status = warehouse_status()
    if status["mode"] not in {"databricks", "snowflake"}:
        return status
    if not status["ready"]:
        return status
    try:
        if status["mode"] == "databricks":
            required_tables = {
                "banking": {"agg_accounts_daily", "agg_transactions_daily"},
                "finance": {"agg_accounts_monthly"},
                "insurance": {"agg_claims_daily", "agg_policies_daily"},
                "retail": {"agg_retail_orders_daily", "fact_retail_orders"},
            }
            checked_domains = []
            configured_domains = [
                entry.strip().partition(":")[0]
                for entry in settings.databricks_domain_catalogs.split(",")
                if entry.strip()
            ]
            domains_to_check = [domain] if domain else configured_domains
            for domain_name in domains_to_check:
                checks = execute_databricks_domain_batch(
                    domain_name,
                    [
                        "SELECT 1 AS connection_test",
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = '"
                        + settings.databricks_domain_schema.replace("'", "''")
                        + "'"
                    ],
                )
                available_tables = {
                    str(row["table_name"]).lower()
                    for row in checks[1]
                }
                missing = required_tables.get(domain_name, set()) - available_tables
                if missing:
                    raise GuardrailError(
                        f"Databricks catalog for {domain_name} is missing expected "
                        f"{settings.databricks_domain_schema} tables: {', '.join(sorted(missing))}"
                    )
                checked_domains.append(domain_name)
            return {
                **status,
                "connected": True,
                "detail": (
                    "Databricks SQL Warehouse connection succeeded; domain catalogs "
                    f"checked: {', '.join(checked_domains)} "
                    f"(schema {settings.databricks_domain_schema})"
                ),
            }
        else:
            tables = (
                "customers",
                "orders",
                "order_items",
                "products",
                "claims",
                "glossary",
                "live_events",
                "accounts",
                "transactions",
                "loans",
            )
            checks = ["SELECT 1 AS connection_test"] + [
                f'SELECT * FROM "{table.upper()}" LIMIT 0' for table in tables
            ]
            _snowflake_execute_many(checks)
    except (GuardrailError, ImportError) as exc:
        secrets = (settings.databricks_token, settings.snowflake_password)
        message = str(exc)
        for secret in secrets:
            if secret:
                message = message.replace(secret, "[redacted]")
        return {**status, "connected": False, "detail": message}
    return {
        **status,
        "connected": True,
        "detail": f"{status['mode'].title()} connection and required table checks succeeded",
    }


def verify_warehouse_domain(domain: str) -> dict[str, Any]:
    if settings.warehouse != "databricks":
        raise GuardrailError(
            f"Live Databricks verification requires WAREHOUSE=databricks; current source is {settings.warehouse}."
        )
    if not warehouse_status()["ready"]:
        raise GuardrailError("Databricks warehouse credentials or HTTP path are not configured.")

    sample_queries = {
        "banking": [
            (
                "SELECT _gold_date, COALESCE(total_balance, 0) AS total_balance, "
                "COALESCE(record_count, 0) AS account_count "
                "FROM agg_accounts_daily ORDER BY _gold_date DESC LIMIT 1"
            ),
        ],
        "finance": [
            "SELECT _gold_month, COALESCE(total_balance, 0) AS total_balance, "
            "COALESCE(record_count, 0) AS account_count "
            "FROM agg_accounts_monthly ORDER BY _gold_month DESC LIMIT 1"
        ],
        "insurance": [
            "SELECT _gold_date, COALESCE(total_premium_amount, 0) AS total_premium, "
            "COALESCE(record_count, 0) AS policy_count "
            "FROM agg_policies_daily ORDER BY _gold_date DESC LIMIT 1"
        ],
        "retail": [
            "SELECT COALESCE(SUM(record_count), 0) AS order_count, "
            "COALESCE(SUM(total_order_amount), 0) AS total_order_amount "
            "FROM agg_retail_orders_daily WHERE _gold_date >= DATE_SUB("
            "CAST(DATE_TRUNC('WEEK', CURRENT_DATE()) AS DATE), 7) "
            "AND _gold_date < CAST(DATE_TRUNC('WEEK', CURRENT_DATE()) AS DATE)"
        ],
    }
    expected_tables = {
        "banking": {"agg_accounts_daily", "agg_transactions_daily"},
        "finance": {"agg_accounts_monthly"},
        "insurance": {"agg_claims_daily", "agg_policies_daily"},
        "retail": {"agg_retail_orders_daily", "fact_retail_orders"},
    }
    if domain not in sample_queries:
        raise GuardrailError(
            f"No live Databricks verification query is configured for {domain}. "
            "Map an authorized catalog and source tables before using this domain."
        )

    catalog = databricks_domain_catalog(domain)
    schema = settings.databricks_domain_schema
    escaped_schema = schema.replace("'", "''")
    identity, table_rows = execute_databricks_domain_batch(
        domain,
        [
            "SELECT current_catalog() AS catalog, current_schema() AS schema",
            "SELECT table_name FROM information_schema.tables "
            f"WHERE table_schema = '{escaped_schema}' ORDER BY table_name",
        ],
    )
    available_tables = {
        str(row["table_name"]).lower()
        for row in table_rows
        if row.get("table_name") is not None
    }
    missing_tables = sorted(expected_tables[domain] - available_tables)
    if missing_tables:
        raise GuardrailError(
            f"Connected to {catalog}.{schema}, but expected tables are missing: "
            f"{', '.join(missing_tables)}"
        )
    sample_results = execute_databricks_domain_batch(domain, sample_queries[domain])
    sample_table = {
        "banking": "agg_accounts_daily",
        "finance": "agg_accounts_monthly",
        "insurance": "agg_policies_daily",
        "retail": "agg_retail_orders_daily",
    }[domain]
    return {
        "status": "passed",
        "warehouse": "databricks",
        "domain": domain,
        "catalog": str(identity[0]["catalog"]) if identity else catalog,
        "schema": str(identity[0]["schema"]) if identity else schema,
        "tables_checked": sorted(expected_tables[domain]),
        "source_table": f"{catalog}.{schema}.{sample_table}",
        "sql": sample_queries[domain],
        "results": sample_results,
        "note": "Representative read-only aggregate queried from the configured domain catalog.",
    }


def _databricks_http_path() -> str:
    return settings.databricks_http_path or (
        f"/sql/1.0/warehouses/{settings.databricks_warehouse_id}"
        if settings.databricks_warehouse_id
        else ""
    )


def _validate_identifier(
    identifier: str, setting_name: str, quote: str = "`"
) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier):
        raise GuardrailError(f"{setting_name} must be a simple SQL identifier")
    return f"{quote}{identifier}{quote}"


def _databricks_execute_many(
    queries: list[str],
    domain: str | None = None,
    parameter_sets: list[Mapping[str, Any]] | None = None,
    row_limit: int | None = None,
) -> list[list[dict[str, Any]]]:
    if not warehouse_status()["ready"]:
        raise GuardrailError("Databricks warehouse is not configured yet")
    try:
        catalog_name = databricks_domain_catalog(domain) if domain else settings.databricks_catalog
        schema_name = settings.databricks_domain_schema if domain else settings.databricks_schema
        fetch_limit = settings.query_row_limit if row_limit is None else max(1, min(row_limit, 2000))
        _validate_identifier(catalog_name, "DATABRICKS_CATALOG")
        _validate_identifier(schema_name, "DATABRICKS_SCHEMA")
        return _databricks_run(catalog_name, schema_name, queries, parameter_sets, fetch_limit)
    except GuardrailError:
        raise
    except Exception as exc:
        message = str(exc).replace(settings.databricks_token, "[redacted]") if settings.databricks_token else str(exc)
        raise GuardrailError(f"Databricks SQL connection or query failed: {message}") from exc


def _databricks_run(
    catalog_name: str,
    schema_name: str,
    queries: list[str],
    parameter_sets: list[Mapping[str, Any]] | None,
    fetch_limit: int,
) -> list[list[dict[str, Any]]]:
    # Sessions are only reused for the same workspace endpoint and namespace.
    namespace = (
        settings.databricks_server_hostname,
        _databricks_http_path(),
        catalog_name,
        schema_name,
    )
    # Bind parameters up front so a missing parameter fails before any session is used.
    statements = []
    for index, query in enumerate(queries):
        parameters = parameter_sets[index] if parameter_sets else None
        statements.append(_qmark_parameters(query, parameters) if parameters else (query, None))
    if len(statements) == 1:
        return [_databricks_query(namespace, *statements[0], fetch_limit)]
    futures = [
        _query_executor.submit(_databricks_query, namespace, sql, values, fetch_limit)
        for sql, values in statements
    ]
    return [future.result() for future in futures]


class _NoServerOperationError(Exception):
    pass


def _databricks_query(
    namespace: tuple[str, str, str, str],
    sql: str,
    values: tuple[Any, ...] | None,
    fetch_limit: int,
) -> list[dict[str, Any]]:
    try:
        from databricks.sql.exc import ServerOperationError
    except ImportError:
        ServerOperationError = _NoServerOperationError
    label = f"{namespace[2]}.{namespace[3]}"

    for attempt in (1, 2):
        conn = _databricks_checkout(namespace)
        reused = conn is not None
        if conn is None:
            conn = _databricks_open(namespace)
        started = time.perf_counter()
        try:
            with conn.cursor() as cur:
                if values is not None:
                    cur.execute(sql, values)
                else:
                    cur.execute(sql)
                columns = [column[0] for column in (cur.description or [])]
                rows = [dict(zip(columns, row)) for row in cur.fetchmany(fetch_limit)]
        except ServerOperationError as exc:
            # The warehouse rejected the statement itself (for example a missing table);
            # the session is still healthy, so keep it and do not retry.
            if "session" not in str(exc).lower():
                _databricks_checkin(namespace, conn)
                raise
            _close_quietly(conn)
            if not (reused and attempt == 1):
                raise
            logger.warning("Pooled Databricks session for %s expired; retrying on a new session", label)
        except Exception:
            _close_quietly(conn)
            if not (reused and attempt == 1):
                raise
            logger.warning("Pooled Databricks session for %s failed; retrying on a new session", label)
        else:
            _databricks_checkin(namespace, conn)
            elapsed_ms = (time.perf_counter() - started) * 1000
            log = logger.warning if elapsed_ms >= settings.slow_query_ms else logger.info
            log(
                "Databricks query on %s took %.0f ms (%s session, %d rows)",
                label, elapsed_ms, "reused" if reused else "new", len(rows),
            )
            return _jsonable(rows)
    raise AssertionError("unreachable")


def _databricks_open(namespace: tuple[str, str, str, str]) -> Any:
    try:
        from databricks import sql as dbsql
    except ImportError as exc:
        raise GuardrailError("Install databricks-sql-connector to use Databricks warehouse") from exc
    started = time.perf_counter()
    # catalog/schema set the session's initial namespace, replacing USE CATALOG/USE SCHEMA.
    conn = dbsql.connect(
        server_hostname=settings.databricks_server_hostname,
        http_path=_databricks_http_path(),
        access_token=settings.databricks_token,
        catalog=namespace[2],
        schema=namespace[3],
    )
    logger.info("Opened Databricks session for %s.%s in %.0f ms", namespace[2], namespace[3], (time.perf_counter() - started) * 1000)
    return conn


def _databricks_checkout(namespace: tuple[str, str, str, str]) -> Any | None:
    now = time.monotonic()
    expired = []
    conn = None
    with _databricks_pool_lock:
        idle = _databricks_idle.get(namespace, [])
        while idle:
            returned_at, candidate = idle.pop()
            if now - returned_at < _DATABRICKS_IDLE_SECONDS:
                conn = candidate
                break
            expired.append(candidate)
    for stale in expired:
        _close_quietly(stale)
    return conn


def _databricks_checkin(namespace: tuple[str, str, str, str], conn: Any) -> None:
    with _databricks_pool_lock:
        idle = _databricks_idle.setdefault(namespace, [])
        if len(idle) < _DATABRICKS_MAX_IDLE_PER_NAMESPACE:
            idle.append((time.monotonic(), conn))
            return
    _close_quietly(conn)


def _close_quietly(conn: Any) -> None:
    try:
        conn.close()
    except Exception:
        logger.debug("Ignoring error while closing a Databricks session", exc_info=True)


def close_warehouse_sessions() -> None:
    with _databricks_pool_lock:
        sessions = [conn for idle in _databricks_idle.values() for _returned_at, conn in idle]
        _databricks_idle.clear()
    for conn in sessions:
        _close_quietly(conn)


def _snowflake_execute_many(
    queries: list[str],
    parameter_sets: list[Mapping[str, Any]] | None = None,
    row_limit: int | None = None,
) -> list[list[dict[str, Any]]]:
    if not warehouse_status()["ready"]:
        raise GuardrailError("Snowflake warehouse is not configured yet")
    try:
        import snowflake.connector
    except ImportError as exc:
        raise GuardrailError("Install snowflake-connector-python to use Snowflake") from exc
    try:
        fetch_limit = settings.query_row_limit if row_limit is None else max(1, min(row_limit, 2000))
        with snowflake.connector.connect(
            account=settings.snowflake_account,
            user=settings.snowflake_user,
            password=settings.snowflake_password,
            role=settings.snowflake_role or None,
            warehouse=settings.snowflake_warehouse,
            database=settings.snowflake_database,
            schema=settings.snowflake_schema,
            network_timeout=settings.query_timeout_seconds,
            login_timeout=settings.query_timeout_seconds,
            autocommit=False,
        ) as conn:
            with conn.cursor() as cursor:
                results = []
                for index, query in enumerate(queries):
                    parameters = parameter_sets[index] if parameter_sets else None
                    if parameters:
                        bound_sql, bound_values = _pyformat_parameters(query, parameters)
                        cursor.execute(bound_sql, bound_values)
                    else:
                        cursor.execute(query)
                    columns = [str(column[0]).lower() for column in (cursor.description or [])]
                    rows = [
                        dict(zip(columns, row, strict=True))
                        for row in cursor.fetchmany(fetch_limit)
                    ]
                    results.append(_jsonable(rows))
                return results
    except GuardrailError:
        raise
    except Exception as exc:
        message = str(exc).replace(settings.snowflake_password, "[redacted]") if settings.snowflake_password else str(exc)
        raise GuardrailError(f"Snowflake connection or query failed: {message}") from exc


def _pyformat_parameters(
    sql: str,
    parameters: Mapping[str, Any],
) -> tuple[str, tuple[Any, ...]]:
    names: list[str] = []

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in parameters:
            raise GuardrailError(f"SQL parameter '{name}' was not supplied")
        names.append(name)
        return "%s"

    bound_sql = re.sub(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)", replace, sql)
    return bound_sql, tuple(parameters[name] for name in names)


def _qmark_parameters(
    sql: str,
    parameters: Mapping[str, Any],
) -> tuple[str, tuple[Any, ...]]:
    names: list[str] = []

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in parameters:
            raise GuardrailError(f"SQL parameter '{name}' was not supplied")
        names.append(name)
        return "?"

    bound_sql = re.sub(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)", replace, sql)
    return bound_sql, tuple(parameters[name] for name in names)
