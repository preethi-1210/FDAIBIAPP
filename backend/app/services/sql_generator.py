import logging
import re
from typing import Any

from ..config import settings
from ..llm import complete_json, failed_providers, last_failure, record_step, resolve_provider
from ..sensitive_data import requested_sensitive_type, sensitive_field_candidates
from .conversation_context import format_conversation_context


def _is_postgres() -> bool:
    return settings.warehouse != "databricks" and settings.database_url.startswith("postgresql")


def _generate_databricks_domain_prompt(
    question: str,
    schema_ctx: dict[str, Any],
    domain: str,
    is_prediction: bool,
) -> str | None:
    if is_prediction:
        return None

    tables = {
        str(table).lower(): {
            str(column.get("name", "")).lower()
            for column in columns
        }
        for table, columns in schema_ctx.get("tables", {}).items()
    }
    q = question.lower()

    def has_columns(table: str, *columns: str) -> bool:
        return table in tables and set(columns).issubset(tables[table])

    if (
        domain == "banking"
        and any(term in q for term in ("transaction", "transactions"))
        and "last week" in q
        and has_columns("agg_transactions_daily", "_gold_date", "record_count", "total_amount")
    ):
        return """
            SELECT COALESCE(SUM(record_count), 0) AS transaction_count,
                   COALESCE(SUM(total_amount), 0) AS transaction_volume
            FROM agg_transactions_daily
            WHERE _gold_date >= DATE_SUB(
                CAST(DATE_TRUNC('WEEK', CURRENT_DATE()) AS DATE), 7)
              AND _gold_date < CAST(DATE_TRUNC('WEEK', CURRENT_DATE()) AS DATE)
        """.strip()

    if (
        domain == "finance"
        and "account" in q
        and "balance" in q
        and "month" in q
        and has_columns("agg_accounts_monthly", "_gold_month", "total_balance", "record_count")
    ):
        return """
            SELECT _gold_month AS month,
                   COALESCE(total_balance, 0) AS total_balance,
                   COALESCE(record_count, 0) AS account_count
            FROM agg_accounts_monthly
            ORDER BY _gold_month DESC
            LIMIT 12
        """.strip()

    if (
        domain == "insurance"
        and "premium" in q
        and "status" in q
        and has_columns("dim_policies", "status", "premium_amount")
    ):
        return """
            SELECT status AS policy_status,
                   COALESCE(SUM(premium_amount), 0) AS total_premium,
                   COUNT(*) AS policy_count
            FROM dim_policies
            GROUP BY status
            ORDER BY total_premium DESC
        """.strip()

    if domain == "retail":
        asks_order_count = (
            any(term in q for term in ("order", "orders"))
            and any(term in q for term in ("how many", "count", "number of", "total"))
        )
        if asks_order_count and "last week" in q:
            return _generate_databricks_fallback(
                question,
                schema_ctx,
                domain,
                is_prediction,
            )

    return None


logger = logging.getLogger(__name__)


def generate_sql(
    question: str,
    schema_ctx: dict[str, Any],
    is_prediction: bool,
    domain: str,
    llm_override: str | None = None,
    role: str = "analyst",
    conversation_context: list[dict[str, str]] | None = None,
) -> str:
    # Records which engine produced the SQL so the response never credits an LLM for a template.
    source: list[str] = []
    sql = _generate_sql(
        question, schema_ctx, is_prediction, domain, llm_override, role, conversation_context, source
    )
    # The LLM call records itself; only non-LLM sources need an explicit entry.
    if not source or not source[0].startswith("llm:"):
        record_step("sql_generation", source[0] if source else "deterministic fallback template")
    return sql


def _generate_sql(
    question: str,
    schema_ctx: dict[str, Any],
    is_prediction: bool,
    domain: str,
    llm_override: str | None,
    role: str,
    conversation_context: list[dict[str, str]] | None,
    source: list[str],
) -> str:
    if requested_sensitive_type(question) == "admin_only" and role == "admin":
        source.append("approved sensitive-field template")
        return _generate_admin_sensitive_sql(question, schema_ctx, domain)

    if settings.warehouse == "databricks" and _is_credit_score_distribution(question):
        source.append("verified query template")
        return _generate_credit_score_distribution_sql(schema_ctx)

    if settings.warehouse == "databricks":
        prompt_sql = _generate_databricks_domain_prompt(
            question,
            schema_ctx,
            domain,
            is_prediction,
        )
        if prompt_sql:
            source.append("verified query template")
            return prompt_sql

    schema_text = _schema_text(schema_ctx)
    context = format_conversation_context(conversation_context)
    if domain == "healthcare":
        scope_instruction = (
            "Use only the provided healthcare_* tables. Every source has tenant_id; include it in joins "
            "and filters, but do not invent a tenant value. The server independently enforces the "
            "authenticated tenant on every source. Return aggregate analytics only; never select "
            "patient_key or other synthetic record keys."
        )
    elif settings.warehouse == "databricks":
        scope_instruction = (
            "The active Databricks catalog/schema is already selected for the authorized business domain. "
            "Do not add a domain predicate unless a domain column exists in the provided schema. "
            "Never substitute a different business entity or metric just because a requested source is unavailable. "
            "If the provided schema cannot answer the question, return JSON with an error instead of SQL."
        )
    else:
        scope_instruction = f"Always filter domain = '{domain}'."
    llm_sql = complete_json(
        system=(
            "You are the SQL Generator for a multi-domain BI platform. "
            f"Use ONLY the provided schema. {scope_instruction} "
            "Return JSON {\"sql\": \"SELECT ...\"}. Read-only SELECT. LIMIT 200. "
            "Never use SELECT * or table.*; explicitly select only the columns needed to answer the question."
            + (
                " This is a prediction request: do not predict in SQL. For a future value of a metric, return "
                "the actual history as one row per period (a date or yyyy-MM period column plus the metric), "
                "ordered by period and covering all available history. For outcomes of individual records "
                "(for example churn), return one row per record with the historical outcome label and "
                "descriptive feature columns."
                if is_prediction
                else ""
            )
        ),
        user=(
            f"Recent conversation for interpreting follow-up references:\n{context}\n"
            f"Current question: {question}\nSchema:\n{schema_text}\nPrediction={is_prediction}"
            if context
            else f"Question: {question}\nSchema:\n{schema_text}\nPrediction={is_prediction}"
        ),
        override=llm_override,
        stage="sql_generation",
    )
    logger.debug("SQL generator provider=%s returned=%s", resolve_provider(llm_override), bool(llm_sql))
    if llm_sql and llm_sql.get("error"):
        raise ValueError(str(llm_sql["error"]))
    if llm_sql and llm_sql.get("sql"):
        source.append(f"llm:{resolve_provider(llm_override)}")
        return str(llm_sql["sql"]).strip().rstrip(";")

    if domain == "healthcare":
        return _generate_healthcare_fallback(question, schema_ctx, is_prediction)

    if settings.warehouse == "databricks":
        try:
            return _generate_databricks_fallback(question, schema_ctx, domain, is_prediction)
        except ValueError as exc:
            provider = resolve_provider(llm_override)
            if provider:
                # Say which configured model(s) failed instead of implying none is configured.
                tried = failed_providers("sql_generation") or [provider]
                reason = last_failure("sql_generation") or "no usable SQL was returned"
                raise ValueError(
                    f"The language model{'s' if len(tried) > 1 else ''} ({' and '.join(tried)}) could not "
                    f"generate SQL for this question (last error: {reason}), and no verified query template "
                    "covers it. No query was run; please try again."
                ) from exc
            raise

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
        # Count orders for the previous calendar week using the Gold aggregate.
        daily_orders = table_with(
            "record_count",
            "_gold_date",
            preferred=("agg_retail_orders_daily",),
        )
        asks_order_count = (
            any(term in q for term in ("order", "orders"))
            and any(
                term in q
                for term in ("how many", "count", "number of", "total")
            )
        )

        if daily_orders and asks_order_count and "last week" in q:
            return (
                "SELECT COALESCE(SUM(CASE WHEN _gold_date >= DATE_SUB("
                "CAST(DATE_TRUNC('WEEK', CURRENT_DATE()) AS DATE), 7) "
                "AND _gold_date < CAST(DATE_TRUNC('WEEK', CURRENT_DATE()) AS DATE) "
                "THEN record_count ELSE 0 END), 0) AS order_count, "
                "MAX(_gold_date) AS latest_data_date, "
                "DATE_SUB(CAST(DATE_TRUNC('WEEK', CURRENT_DATE()) AS DATE), 7) "
                "AS requested_period_start "
                f"FROM `{daily_orders}` "
            )

        # Existing fallback for revenue and regional sales questions.
        orders = table_with(
            "order_amount",
            "region",
            preferred=("retail_orders", "fact_retail_orders", "gold_retail_orders"),
        )
        if orders and any(
            term in q for term in ("revenue", "sales", "order", "region")
        ):
            return (
                f"SELECT region, SUM(order_amount) AS revenue, "
                f"COUNT(*) AS order_count "
                f"FROM `{orders}` "
                f"GROUP BY region ORDER BY revenue DESC LIMIT 200"
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
        col_s = ", ".join(
            f"{c['name']} {c['type']}" + (f" -- {c['description']}" if c.get("description") else "")
            for c in cols
        )
        lines.append(f"{table}({col_s})")
    rag_context = schema_ctx.get("rag_context", [])
    if rag_context:
        lines.extend(["Retrieved business context:", *rag_context])
    return "\n".join(lines)


def _generate_healthcare_fallback(
    question: str,
    schema_ctx: dict[str, Any],
    is_prediction: bool,
) -> str:
    if is_prediction:
        raise ValueError(
            "Healthcare prediction queries require an explicitly validated model and cohort policy."
        )
    tables = {
        str(table).lower(): {str(column.get("name", "")).lower() for column in columns}
        for table, columns in schema_ctx.get("tables", {}).items()
    }
    q = question.lower()

    def available(table: str, *columns: str) -> bool:
        return table in tables and set(columns).issubset(tables[table])

    if any(term in q for term in ("diagnos", "condition")) and available(
        "healthcare_diagnoses", "tenant_id", "diagnosis_category"
    ):
        return (
            "SELECT diagnosis_category, COUNT(*) AS diagnosis_count "
            "FROM healthcare_diagnoses WHERE tenant_id IS NOT NULL "
            "GROUP BY diagnosis_category ORDER BY diagnosis_count DESC LIMIT 200"
        )
    if any(term in q for term in ("quality", "screening", "follow-up")) and available(
        "healthcare_quality_metrics", "tenant_id", "metric_name", "metric_value", "reporting_period"
    ):
        return (
            "SELECT reporting_period, metric_name, AVG(metric_value) AS average_metric_value, "
            "COUNT(*) AS metric_count FROM healthcare_quality_metrics "
            "WHERE tenant_id IS NOT NULL GROUP BY reporting_period, metric_name "
            "ORDER BY reporting_period LIMIT 200"
        )
    if any(term in q for term in ("claim", "approved amount", "approval")) and available(
        "healthcare_claims", "tenant_id", "claim_status", "claim_amount", "approved_amount"
    ):
        return (
            "SELECT claim_status, SUM(claim_amount) AS claim_amount, "
            "SUM(approved_amount) AS approved_amount, COUNT(*) AS claim_count "
            "FROM healthcare_claims WHERE tenant_id IS NOT NULL "
            "GROUP BY claim_status ORDER BY claim_amount DESC LIMIT 200"
        )
    if any(term in q for term in ("encounter", "visit", "appointment")) and available(
        "healthcare_encounters", "tenant_id", "encounter_type", "encounter_status"
    ):
        return (
            "SELECT encounter_type, encounter_status, COUNT(*) AS encounter_count "
            "FROM healthcare_encounters WHERE tenant_id IS NOT NULL "
            "GROUP BY encounter_type, encounter_status ORDER BY encounter_count DESC LIMIT 200"
        )
    if any(term in q for term in ("patient", "enrollment", "region")) and available(
        "healthcare_patients", "tenant_id", "postal_region", "patient_status"
    ):
        return (
            "SELECT postal_region, patient_status, COUNT(*) AS patient_count "
            "FROM healthcare_patients WHERE tenant_id IS NOT NULL "
            "GROUP BY postal_region, patient_status ORDER BY patient_count DESC LIMIT 200"
        )
    raise ValueError(
        "No supported aggregate Healthcare query is available for that question without a configured language model."
    )
