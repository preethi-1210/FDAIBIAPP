import re
from typing import Any

from ..config import settings
from ..llm import complete_json, resolve_provider
from ..sensitive_data import requested_sensitive_type, sensitive_field_candidates


def _is_postgres() -> bool:
    return settings.warehouse != "databricks" and settings.database_url.startswith("postgresql")


def generate_sql(
    question: str,
    schema_ctx: dict[str, Any],
    is_prediction: bool,
    domain: str,
    llm_override: str | None = None,
    role: str = "analyst",
) -> str:
    if requested_sensitive_type(question) == "admin_only" and role == "admin":
        return _generate_admin_sensitive_sql(question, schema_ctx, domain)

    schema_text = _schema_text(schema_ctx)
    llm_sql = complete_json(
        system=(
            "You are the SQL Generator for a multi-domain BI platform. "
            "Use ONLY the provided schema. Always filter domain = '"
            + domain
            + "'. Return JSON {\"sql\": \"SELECT ...\"}. Read-only SELECT. LIMIT 200. "
            "Never use SELECT * or table.*; explicitly select only the columns needed to answer the question."
        ),
        user=f"Question: {question}\nSchema:\n{schema_text}\nPrediction={is_prediction}",
        override=llm_override,
    )
    if llm_sql and llm_sql.get("sql"):
        return str(llm_sql["sql"]).strip().rstrip(";")

    q = question.lower()
    d = _quote(domain)
    week = (
        "CURRENT_DATE - INTERVAL '7 days'"
        if _is_postgres() or settings.warehouse == "databricks"
        else "DATE('now', '-7 days')"
    )
    recency = (
        "CAST(CURRENT_DATE - MAX(o.order_date) AS INT) AS days_since_order"
        if _is_postgres() or settings.warehouse == "databricks"
        else "CAST(julianday('now') - julianday(MAX(o.order_date)) AS INT) AS days_since_order"
    )
    month_expr = (
        "date_format(order_date, 'yyyy-MM')"
        if settings.warehouse == "databricks"
        else ("to_char(order_date, 'YYYY-MM')" if _is_postgres() else "strftime('%Y-%m', order_date)")
    )
    amount_alias = {
        "banking": "deposits",
        "finance": "aum",
        "insurance": "premium",
        "retail": "revenue",
    }.get(domain, "revenue")

    if is_prediction and any(w in q for w in ("churn", "likely", "customer", "client", "policyholder")):
        return f"""
            SELECT c.id AS customer_id, c.name, c.segment, c.region, c.churned,
                   COUNT(o.id) AS order_count,
                   COALESCE(SUM(o.total_amount), 0) AS {amount_alias},
                   {recency}
            FROM customers c
            LEFT JOIN orders o ON o.customer_id = c.id AND o.domain = {d}
            WHERE c.domain = {d}
            GROUP BY c.id, c.name, c.segment, c.region, c.churned
            LIMIT 200
        """.strip()

    if "claim" in q or "loss ratio" in q:
        return f"""
            SELECT p.category, p.name AS product,
                   COALESCE(SUM(o.total_amount), 0) AS premium,
                   COALESCE((SELECT SUM(cl.amount) FROM claims cl
                             JOIN customers c2 ON c2.id = cl.customer_id
                             WHERE cl.domain = {d} AND c2.domain = {d}), 0) AS claims
            FROM products p
            LEFT JOIN order_items oi ON oi.product_id = p.id AND oi.domain = {d}
            LEFT JOIN orders o ON o.id = oi.order_id AND o.status != 'cancelled'
            WHERE p.domain = {d}
            GROUP BY p.category, p.name
            ORDER BY premium DESC
            LIMIT 200
        """.strip()

    if "last week" in q:
        return f"""
            SELECT COUNT(*) AS event_count, COALESCE(SUM(total_amount), 0) AS {amount_alias}
            FROM orders
            WHERE domain = {d} AND order_date >= {week} AND status != 'cancelled'
        """.strip()

    if "region" in q:
        return f"""
            SELECT c.region, COUNT(o.id) AS events, COALESCE(SUM(o.total_amount), 0) AS {amount_alias}
            FROM orders o
            JOIN customers c ON c.id = o.customer_id
            WHERE o.domain = {d} AND o.status != 'cancelled'
            GROUP BY c.region
            ORDER BY {amount_alias} DESC
            LIMIT 200
        """.strip()

    if any(w in q for w in ("product", "category", "instrument", "policy")):
        return f"""
            SELECT p.category, p.name AS product, SUM(oi.quantity) AS units, SUM(oi.amount) AS {amount_alias}
            FROM order_items oi
            JOIN products p ON p.id = oi.product_id
            JOIN orders o ON o.id = oi.order_id
            WHERE oi.domain = {d} AND o.status != 'cancelled'
            GROUP BY p.category, p.name
            ORDER BY {amount_alias} DESC
            LIMIT 200
        """.strip()

    if "segment" in q:
        return f"""
            SELECT c.segment, COUNT(DISTINCT c.id) AS customers,
                   COALESCE(SUM(o.total_amount), 0) AS {amount_alias}
            FROM customers c
            LEFT JOIN orders o ON o.customer_id = c.id AND o.domain = {d} AND o.status != 'cancelled'
            WHERE c.domain = {d}
            GROUP BY c.segment
            ORDER BY {amount_alias} DESC
        """.strip()

    if any(w in q for w in ("month", "trend", "aum", "revenue", "sales", "premium", "deposit", "notional")):
        return f"""
            SELECT {month_expr} AS month,
                   COUNT(*) AS events,
                   COALESCE(SUM(total_amount), 0) AS {amount_alias}
            FROM orders
            WHERE domain = {d} AND status != 'cancelled'
            GROUP BY {month_expr}
            ORDER BY month
            LIMIT 200
        """.strip()

    _ = resolve_provider(llm_override)
    return f"""
        SELECT COUNT(*) AS event_count, COALESCE(SUM(total_amount), 0) AS {amount_alias}
        FROM orders
        WHERE domain = {d} AND status != 'cancelled'
    """.strip()


def _generate_admin_sensitive_sql(
    question: str,
    schema_ctx: dict[str, Any],
    domain: str,
) -> str:
    candidates = sensitive_field_candidates(question)
    table_schemas = schema_ctx.get("tables", {})
    matches: list[tuple[int, str, str, list[str]]] = []

    for table, columns in table_schemas.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
            continue
        names = [
            str(column["name"])
            for column in columns
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(column.get("name", "")))
        ]
        normalized = {_normalize_identifier(name): name for name in names}
        if "domain" not in normalized:
            continue
        for candidate_index, candidate in enumerate(candidates):
            field = normalized.get(candidate)
            if field:
                rank = _sensitive_table_rank(table, question)
                matches.append((rank * 100 + candidate_index, table, field, names))

    if not matches:
        description = "account or payment-card identifier" if "account" in question.lower() or "card" in question.lower() else "policy or member identifier"
        raise ValueError(
            f"No authorized {description} column is available in the configured data schema. "
            "Configure the approved field in the source data and restart the backend."
        )

    _, table, sensitive_field, column_names = min(matches, key=lambda match: match[0])
    available = {_normalize_identifier(name): name for name in column_names}
    display_columns = [
        available[name]
        for name in ("name", "display_name", "customer_name", "holder_name", "insured_name", "region", "segment")
        if name in available and available[name].lower() != sensitive_field.lower()
    ]
    selected_columns = [*display_columns, sensitive_field]
    quote = "`" if settings.warehouse == "databricks" else '"'
    projection = ", ".join(f"{quote}{column}{quote}" for column in selected_columns)
    domain_column = available["domain"]
    return (
        f"SELECT {projection} FROM {quote}{table}{quote} "
        f"WHERE {quote}{domain_column}{quote} = {_quote(domain)} LIMIT 200"
    )


def _normalize_identifier(identifier: str) -> str:
    snake_case = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", identifier)
    return re.sub(r"[^a-zA-Z0-9]+", "_", snake_case).strip("_").lower()


def _sensitive_table_rank(table: str, question: str) -> int:
    name = table.lower()
    q = question.lower()
    if "policy" in q or "member" in q:
        priorities = ("policies", "insurance_policies", "claims", "customers")
    elif "card" in q:
        priorities = ("payment_cards", "cards", "customers")
    else:
        priorities = ("accounts", "bank_accounts", "customers")
    for index, preferred in enumerate(priorities):
        if name == preferred:
            return index
    return len(priorities)


def _quote(domain: str) -> str:
    return "'" + domain.replace("'", "") + "'"


def _schema_text(schema_ctx: dict[str, Any]) -> str:
    lines = []
    for table, cols in schema_ctx.get("tables", {}).items():
        col_s = ", ".join(f"{c['name']} {c['type']}" for c in cols)
        lines.append(f"{table}({col_s})")
    return "\n".join(lines)
