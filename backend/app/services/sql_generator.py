import re
from typing import Any

from ..config import settings
from ..llm import complete_json, resolve_provider
from ..sensitive_data import requested_sensitive_type, sensitive_field_candidates
from .conversation_context import format_conversation_context


def _is_postgres() -> bool:
    return settings.warehouse != "databricks" and settings.database_url.startswith("postgresql")


def generate_sql(
    question: str,
    schema_ctx: dict[str, Any],
    is_prediction: bool,
    domain: str,
    llm_override: str | None = None,
    role: str = "analyst",
    conversation_context: list[dict[str, str]] | None = None,
) -> str:
    if requested_sensitive_type(question) == "admin_only" and role == "admin":
        return _generate_admin_sensitive_sql(question, schema_ctx, domain)

    if settings.warehouse == "databricks" and _is_credit_score_distribution(question):
        return _generate_credit_score_distribution_sql(schema_ctx)

    schema_text = _schema_text(schema_ctx)
    context = format_conversation_context(conversation_context)
    scope_instruction = (
        "The active Databricks catalog/schema is already selected for the authorized business domain. "
        "Do not add a domain predicate unless a domain column exists in the provided schema."
        if settings.warehouse == "databricks"
        else f"Always filter domain = '{domain}'."
    )
    llm_sql = complete_json(
        system=(
            "You are the SQL Generator for a multi-domain BI platform. "
            f"Use ONLY the provided schema. {scope_instruction} "
            "Return JSON {\"sql\": \"SELECT ...\"}. Read-only SELECT. LIMIT 200. "
            "Never use SELECT * or table.*; explicitly select only the columns needed to answer the question."
        ),
        user=(
            f"Recent conversation for interpreting follow-up references:\n{context}\n"
            f"Current question: {question}\nSchema:\n{schema_text}\nPrediction={is_prediction}"
            if context
            else f"Question: {question}\nSchema:\n{schema_text}\nPrediction={is_prediction}"
        ),
        override=llm_override,
    )
    if llm_sql and llm_sql.get("sql"):
        return str(llm_sql["sql"]).strip().rstrip(";")

    if settings.warehouse == "databricks":
        return _generate_databricks_fallback(question, schema_ctx, domain, is_prediction)

    q = question.lower()
    d = _quote(domain)
    week = (
        "CURRENT_DATE - INTERVAL '7 days'"
        if _is_postgres() or settings.warehouse in {"databricks", "snowflake"}
        else "DATE('now', '-7 days')"
    )
    recency = (
        "CAST(CURRENT_DATE - MAX(o.order_date) AS INT) AS days_since_order"
        if _is_postgres() or settings.warehouse in {"databricks", "snowflake"}
        else "CAST(julianday('now') - julianday(MAX(o.order_date)) AS INT) AS days_since_order"
    )
    month_expr = (
        "date_format(order_date, 'yyyy-MM')"
        if settings.warehouse == "databricks"
        else (
            "to_char(order_date, 'YYYY-MM')"
            if _is_postgres() or settings.warehouse == "snowflake"
            else "strftime('%Y-%m', order_date)"
        )
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

    if domain == "banking" and any(word in q for word in ("loan", "lending", "npl")):
        dimension, dimension_alias = (
            ("c.region", "region")
            if "region" in q
            else (
                ("c.segment", "segment")
                if "segment" in q
                else ("l.loan_type", "loan_type")
            )
        )
        delinquent_filter = "AND l.status = 'delinquent'" if "npl" in q else ""
        return f"""
            SELECT {dimension} AS {dimension_alias},
                   COUNT(l.id) AS loan_count,
                   COALESCE(SUM(l.principal_amount), 0) AS principal_amount,
                   COALESCE(SUM(l.outstanding_balance), 0) AS outstanding_balance
            FROM loans l
            JOIN customers c ON c.id = l.customer_id
            WHERE l.domain = {d} AND c.domain = {d} {delinquent_filter}
            GROUP BY {dimension}
            ORDER BY outstanding_balance DESC
            LIMIT 200
        """.strip()

    if domain == "banking" and any(word in q for word in ("account", "deposit", "balance")):
        dimension, dimension_alias = (
            ("c.region", "region")
            if "region" in q
            else (
                ("c.segment", "segment")
                if "segment" in q
                else ("a.account_type", "account_type")
            )
        )
        return f"""
            SELECT {dimension} AS {dimension_alias},
                   COUNT(a.id) AS account_count,
                   COALESCE(SUM(a.balance), 0) AS total_balance,
                   COALESCE(AVG(a.balance), 0) AS average_balance
            FROM accounts a
            JOIN customers c ON c.id = a.customer_id
            WHERE a.domain = {d} AND c.domain = {d}
            GROUP BY {dimension}
            ORDER BY total_balance DESC
            LIMIT 200
        """.strip()

    if domain == "banking" and any(word in q for word in ("transaction", "transactions")):
        time_filter = (
            f"AND t.transaction_date >= {week}"
            if "week" in q and (_is_postgres() or settings.warehouse in {"databricks", "snowflake"})
            else (
                "AND t.transaction_date >= DATE('now', '-7 days')"
                if "week" in q
                else ""
            )
        )
        return f"""
            SELECT t.transaction_type,
                   COUNT(t.id) AS transaction_count,
                   COALESCE(SUM(t.amount), 0) AS net_amount
            FROM transactions t
            JOIN accounts a ON a.id = t.account_id
            JOIN customers c ON c.id = a.customer_id
            WHERE t.domain = {d} AND c.domain = {d} {time_filter}
            GROUP BY t.transaction_type
            ORDER BY transaction_count DESC
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


def _is_credit_score_distribution(question: str) -> bool:
    normalized = question.lower().replace("_", " ")
    return "credit" in normalized and "score" in normalized and any(
        word in normalized for word in ("distribution", "range", "bucket", "breakdown")
    )


def _generate_credit_score_distribution_sql(schema_ctx: dict[str, Any]) -> str:
    tables = schema_ctx.get("tables", {})
    selected = next(
        (
            table
            for table in ("customers", "banking_customers")
            if any(
                str(column.get("name", "")).lower() == "credit_score"
                for column in tables.get(table, [])
            )
        ),
        None,
    )
    if selected is None:
        raise ValueError(
            "No credit_score column is available in the authorized source schema."
        )
    return f"""
        SELECT CASE
                   WHEN credit_score BETWEEN 300 AND 399 THEN '300-399'
                   WHEN credit_score BETWEEN 400 AND 499 THEN '400-499'
                   WHEN credit_score BETWEEN 500 AND 599 THEN '500-599'
                   WHEN credit_score BETWEEN 600 AND 699 THEN '600-699'
                   WHEN credit_score BETWEEN 700 AND 799 THEN '700-799'
                   WHEN credit_score BETWEEN 800 AND 850 THEN '800-850'
                   ELSE 'outside-bands'
               END AS score_range,
               COUNT(*) AS customers
        FROM `{selected}`
        WHERE credit_score IS NOT NULL
        GROUP BY score_range
        ORDER BY score_range
        LIMIT 200
    """.strip()


def _generate_databricks_fallback(
    question: str,
    schema_ctx: dict[str, Any],
    domain: str,
    is_prediction: bool,
) -> str:
    tables = {
        str(table).lower(): {
            str(column.get("name", "")).lower()
            for column in columns
        }
        for table, columns in schema_ctx.get("tables", {}).items()
    }
    q = question.lower()

    def table_with(*required: str, preferred: tuple[str, ...]) -> str | None:
        return next(
            (
                table
                for table in preferred
                if table in tables and set(required).issubset(tables[table])
            ),
            None,
        )

    if is_prediction:
        raise ValueError(
            "Prediction SQL needs a model-generated query based on the available labeled "
            "source columns. Configure a language model or provide a supported analytics question."
        )
    if domain == "banking":
        if any(term in q for term in ("credit score", "credit_score")):
            if "customers" in tables and "credit_score" in tables["customers"]:
                selected = "customers"
            elif "banking_customers" in tables and "credit_score" in tables["banking_customers"]:
                selected = "banking_customers"
            else:
                selected = None
            if selected:
                return _generate_credit_score_distribution_sql(
                    {"tables": {selected: [{"name": "credit_score"}]}}
                )
        account_table = table_with("balance", "account_type", preferred=("accounts",))
        if account_table and any(term in q for term in ("account", "deposit", "balance")):
            return (
                f"SELECT account_type, SUM(balance) AS total_balance, COUNT(*) AS account_count "
                f"FROM `{account_table}` GROUP BY account_type ORDER BY total_balance DESC LIMIT 200"
            )
        transaction_table = table_with("amount", "transaction_type", preferred=("transactions",))
        if transaction_table and any(term in q for term in ("transaction", "spend", "amount")):
            return (
                f"SELECT transaction_type, SUM(amount) AS total_amount, COUNT(*) AS transaction_count "
                f"FROM `{transaction_table}` GROUP BY transaction_type ORDER BY total_amount DESC LIMIT 200"
            )
    elif domain == "insurance":
        policies = table_with("premium_amount", "status", preferred=("policies",))
        claims = table_with("claim_amount", "claim_status", preferred=("claims",))
        if policies and any(term in q for term in ("premium", "policy")):
            return (
                f"SELECT status, SUM(premium_amount) AS total_premium, COUNT(*) AS policy_count "
                f"FROM `{policies}` GROUP BY status ORDER BY total_premium DESC LIMIT 200"
            )
        if claims and any(term in q for term in ("claim", "loss")):
            return (
                f"SELECT claim_status, SUM(claim_amount) AS total_claim_amount, COUNT(*) AS claim_count "
                f"FROM `{claims}` GROUP BY claim_status ORDER BY total_claim_amount DESC LIMIT 200"
            )
    elif domain == "retail":
        orders = table_with("order_amount", "region", preferred=("retail_orders",))
        if orders and any(term in q for term in ("revenue", "sales", "order", "region")):
            return (
                f"SELECT region, SUM(order_amount) AS revenue, COUNT(*) AS order_count "
                f"FROM `{orders}` GROUP BY region ORDER BY revenue DESC LIMIT 200"
            )

    raise ValueError(
        f"No safe deterministic Databricks query is available for that {domain} question "
        "without a configured language model. Use a supported metric or configure an LLM."
    )


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
    rag_context = schema_ctx.get("rag_context", [])
    if rag_context:
        lines.extend(["Retrieved business context:", *rag_context])
    return "\n".join(lines)
