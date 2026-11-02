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


def execute_readonly(sql: str, domain: str | None = None) -> list[dict[str, Any]]:
    safe_sql = validate_sql(sql)
    if settings.warehouse == "databricks":
        return _databricks_execute_many([safe_sql], domain=domain)[0]
    if settings.warehouse == "snowflake":
        return _snowflake_execute_many([safe_sql])[0]
    try:
        with engine.connect() as conn:
            if settings.database_url.startswith(("postgresql", "sqlite")):
                conn.execute(text(f"EXPLAIN {safe_sql}"))
            result = conn.execute(text(safe_sql))
            rows = [dict(r._mapping) for r in result.fetchmany(settings.query_row_limit)]
            return _jsonable(rows)
    except SQLAlchemyError as exc:
        raise GuardrailError(f"SQL failed guardrail/execution: {exc}") from exc


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


def execute_databricks_domain_batch(
    domain: str,
    queries: list[str],
) -> list[list[dict[str, Any]]]:
    if settings.warehouse != "databricks":
        return execute_readonly_batch(queries)
    catalog = _validate_identifier(
        databricks_domain_catalog(domain),
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
        from databricks import sql as dbsql
    except ImportError as exc:
        raise GuardrailError(
            "Install databricks-sql-connector to use Databricks warehouse"
        ) from exc
    try:
        with dbsql.connect(
            server_hostname=settings.databricks_server_hostname,
            http_path=_databricks_http_path(),
            access_token=settings.databricks_token,
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(f"USE CATALOG {catalog}")
                cur.execute(f"USE SCHEMA {schema}")
                results = []
                for query in safe_queries:
                    cur.execute(query)
                    columns = [column[0] for column in (cur.description or [])]
                    rows = [
                        dict(zip(columns, row))
                        for row in cur.fetchmany(settings.query_row_limit)
                    ]
                    results.append(_jsonable(rows))
                return results
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
            """
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
    rows = execute_readonly(
        f"""
            SELECT table_name, column_name, data_type
            FROM {catalog}.information_schema.columns
            WHERE table_schema = '{table_schema}'
            ORDER BY table_name, ordinal_position
        """
    )
    return _group_schema_columns(rows)


def _group_schema_columns(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    tables: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        lowered = {str(key).lower(): value for key, value in row.items()}
        table = str(lowered["table_name"])
        tables.setdefault(table, []).append(
            {"name": str(lowered["column_name"]), "type": str(lowered["data_type"])}
        )
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
                "banking": {"accounts", "branches", "customers", "products", "transactions"},
                "insurance": {
                    "claims",
                    "customers_with_payment_test_data",
                    "payments",
                    "policies",
                    "products",
                },
                "retail": {"retail_customers", "retail_orders", "retail_products"},
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
                "SELECT COUNT(*) AS total_customers, MIN(credit_score) AS min_score, "
                "MAX(credit_score) AS max_score, AVG(credit_score) AS average_score, "
                "median(credit_score) AS median_score, "
                "ROUND(percentile(credit_score, 0.25)) AS p25, "
                "percentile(credit_score, 0.75) AS p75, "
                "COUNT(DISTINCT credit_score) AS distinct_scores FROM customers"
            ),
            (
                "SELECT CASE "
                "WHEN credit_score BETWEEN 300 AND 399 THEN '300-399' "
                "WHEN credit_score BETWEEN 400 AND 499 THEN '400-499' "
                "WHEN credit_score BETWEEN 500 AND 599 THEN '500-599' "
                "WHEN credit_score BETWEEN 600 AND 699 THEN '600-699' "
                "WHEN credit_score BETWEEN 700 AND 799 THEN '700-799' "
                "WHEN credit_score BETWEEN 800 AND 850 THEN '800-850' "
                "ELSE 'outside-bands' END AS score_range, COUNT(*) AS customers "
                "FROM customers GROUP BY score_range ORDER BY score_range"
            ),
        ],
        "insurance": [
            "SELECT COUNT(*) AS policy_count, COALESCE(SUM(premium_amount), 0) AS total_premium "
            "FROM policies"
        ],
        "retail": [
            "SELECT COUNT(*) AS order_count, COALESCE(SUM(order_amount), 0) AS total_order_amount "
            "FROM retail_orders"
        ],
    }
    expected_tables = {
        "banking": {"accounts", "branches", "customers", "products", "transactions"},
        "insurance": {
            "claims",
            "customers_with_payment_test_data",
            "payments",
            "policies",
            "products",
        },
        "retail": {"retail_customers", "retail_orders", "retail_products"},
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
    sample_table = "customers" if domain == "banking" else "policies" if domain == "insurance" else "retail_orders"
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
        "note": (
            "The configured Banking silver schema has no fact_customers table. "
            "This verified example reads credit_score from customers instead."
            if domain == "banking"
            else "Representative read-only aggregate queried from the configured domain catalog."
        ),
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
) -> list[list[dict[str, Any]]]:
    if not warehouse_status()["ready"]:
        raise GuardrailError("Databricks warehouse is not configured yet")
    try:
        from databricks import sql as dbsql
    except ImportError as exc:
        raise GuardrailError("Install databricks-sql-connector to use Databricks warehouse") from exc
    try:
        catalog_name = databricks_domain_catalog(domain) if domain else settings.databricks_catalog
        schema_name = settings.databricks_domain_schema if domain else settings.databricks_schema
        catalog = _validate_identifier(catalog_name, "DATABRICKS_CATALOG")
        schema = _validate_identifier(schema_name, "DATABRICKS_SCHEMA")
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


def _snowflake_execute_many(queries: list[str]) -> list[list[dict[str, Any]]]:
    if not warehouse_status()["ready"]:
        raise GuardrailError("Snowflake warehouse is not configured yet")
    try:
        import snowflake.connector
    except ImportError as exc:
        raise GuardrailError("Install snowflake-connector-python to use Snowflake") from exc
    try:
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
                for query in queries:
                    cursor.execute(query)
                    columns = [str(column[0]).lower() for column in (cursor.description or [])]
                    rows = [
                        dict(zip(columns, row, strict=True))
                        for row in cursor.fetchmany(settings.query_row_limit)
                    ]
                    results.append(_jsonable(rows))
                return results
    except GuardrailError:
        raise
    except Exception as exc:
        message = str(exc).replace(settings.snowflake_password, "[redacted]") if settings.snowflake_password else str(exc)
        raise GuardrailError(f"Snowflake connection or query failed: {message}") from exc
