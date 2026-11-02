from datetime import date, datetime, timezone
from functools import partial
from random import Random
import re
import time
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from sqlglot import exp, parse_one
from sqlglot.errors import ParseError
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..auth import AuthUser
from ..config import settings
from ..domains import DOMAIN_LABELS
from ..llm import complete_text, record_step, trace_steps
from ..governance import build_lineage, chart_spec, extract_tables, guardrail_report, model_gov, source_tables
from ..models import Claim, Customer, HitlReview, LiveEvent, Order, OrderItem, Product, QueryHistory
from ..schemas import PredictionPayload, QueryResponse, UncertaintyPayload
from ..sensitive_data import (
    require_sensitive_request_access,
    require_sql_sensitive_access,
    requested_sensitive_type,
    sanitize_sensitive_rows,
)
from ..warehouse import (
    databricks_domain_catalog,
    databricks_healthcare_tenant_catalog,
    execute_databricks_domain_batch,
    execute_readonly,
    execute_readonly_batch,
)
from .context_graph import ContextGraphService
from .feature_builder import build_features, build_forecast
from .guardrail import GuardrailError
from .assistant_graph import BusinessAssistantGraph
from .schema_rag import SchemaRagError
from .sql_generator import generate_sql
from .synthesis import synthesize
from .tabpfn_service import ModelUnavailableError, TabPFNService
from .tenant_scope import scope_healthcare_sql
from .reports import RETAIL_NET_SALES

RISK_RATIO_DEFINITIONS = {
    "banking": {
        "label": "Dormant account ratio",
        "description": "Dormant account balances divided by open banking account balances.",
        "numerator_label": "Dormant account balances",
        "denominator_label": "Open account balances",
    },
    "finance": {
        "label": "Cancelled trade ratio (demo proxy)",
        "description": "Cancelled trade notional divided by gross trade notional; this demo proxy is not an investment-loss measure.",
        "numerator_label": "Cancelled trade notional",
        "denominator_label": "Gross trade notional",
    },
    "insurance": {
        "label": "Insurance loss ratio",
        "description": "Insurance claims divided by earned premium.",
        "numerator_label": "Claims",
        "denominator_label": "Premium",
    },
    "healthcare": {
        "label": "Approved claim amount rate",
        "description": "Approved claim amounts divided by submitted claim amounts for the authorized tenant.",
        "numerator_label": "Approved claim amount",
        "denominator_label": "Submitted claim amount",
    },
    "retail": {
        "label": "Cancelled sales ratio (demo proxy)",
        "description": "Cancelled order value divided by gross order value; this demo proxy is not an accounting loss measure.",
        "numerator_label": "Cancelled sales",
        "denominator_label": "Gross sales",
    },
}

_QUERY_ENTITY_PATTERNS = {
    "order": re.compile(r"\b(?:orders?|purchases?|sales?)\b", re.IGNORECASE),
    "claim": re.compile(r"\bclaims?\b", re.IGNORECASE),
    "transaction": re.compile(r"\btransactions?\b", re.IGNORECASE),
    "account": re.compile(r"\baccounts?\b", re.IGNORECASE),
    "loan": re.compile(r"\b(?:loans?|lending|npl)\b", re.IGNORECASE),
    "customer": re.compile(r"\b(?:customers?|clients?)\b", re.IGNORECASE),
    "policy": re.compile(r"\bpolicies?\b", re.IGNORECASE),
    "product": re.compile(r"\bproducts?\b", re.IGNORECASE),
    "beneficiary": re.compile(r"\bbeneficiaries\b", re.IGNORECASE),
    "patient": re.compile(r"\bpatients?\b", re.IGNORECASE),
}
_QUERY_MEASURE_PATTERNS = {
    "fee": re.compile(r"\bfees?\b", re.IGNORECASE),
    "interest": re.compile(r"\binterest\b", re.IGNORECASE),
    "refund": re.compile(r"\brefunds?\b", re.IGNORECASE),
    "discount": re.compile(r"\bdiscounts?\b", re.IGNORECASE),
    "premium": re.compile(r"\bpremiums?\b", re.IGNORECASE),
    "payment": re.compile(r"\bpayments?\b", re.IGNORECASE),
    "card": re.compile(r"\bcards?\b", re.IGNORECASE),
    "inventory": re.compile(r"\b(?:inventory|stock)\b", re.IGNORECASE),
    "store": re.compile(r"\bstores?\b", re.IGNORECASE),
    "branch": re.compile(r"\bbranch(?:es)?\b", re.IGNORECASE),
}


_LLM_PROVIDERS = {"google", "openai", "grok", "anthropic", "ollama"}
_ABOUT_ASSISTANT = (
    "This is the RYZE AI/BI governed business assistant. Ask questions in plain language about your "
    "authorized {domain} data and it will plan the analysis, generate read-only SQL against the configured "
    "Databricks SQL Warehouse, validate it against Unity Catalog metadata and your access rights, run it, and "
    "answer from the actual results, showing the SQL, table, chart and how each step was handled. "
    "It can also project future values with a self-hosted TabPFN model (business users' predictions are "
    "released after administrator review) and follow up on earlier questions in the same conversation. "
    "Try for example: “Total transaction volume for March 2026” or “Compare it with the month before”."
)
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _source_label(domain: str) -> str:
    if settings.warehouse == "databricks":
        try:
            return (
                f"Databricks SQL Warehouse {settings.databricks_warehouse_id or ''} "
                f"{databricks_domain_catalog(domain)}.{settings.databricks_domain_schema}"
            ).replace("  ", " ")
        except GuardrailError:
            return "Databricks SQL Warehouse"
    return settings.warehouse


def _is_empty_result(table: list[dict[str, Any]]) -> bool:
    """True when nothing was found: no rows, or one aggregate row of only zeros/NULLs."""
    if not table:
        return True
    if len(table) != 1:
        return False
    values = list(table[0].values())
    return bool(values) and all(
        value is None or (isinstance(value, (int, float)) and not isinstance(value, bool) and value == 0)
        for value in values
    )


def _forecast_answer(
    forecast: dict[str, Any],
    prediction: PredictionPayload,
    uncertainty: UncertaintyPayload,
) -> str:
    low, high = uncertainty.confidence_range
    target = str(forecast["target"]).replace("_", " ")
    return (
        f"Prediction (self-hosted TabPFN model output, not actual data): {target} for "
        f"{forecast['next_period']} is projected at {prediction.values[0]:,.2f}, with an 80% range of "
        f"{low:,.2f} to {high:,.2f}. Actual history used: {forecast['periods']} {forecast['frequency']} "
        f"periods from {forecast['history_start']} to {forecast['history_end']}; the latest actual value was "
        f"{forecast['last_value']:,.2f} ({forecast['history_end']}). The projection assumes recent patterns "
        "continue and does not account for events outside this data. If the latest period is still in "
        "progress, the projection may be understated."
    )


def _validate_query_relevance(question: str, sql: str, domain: str) -> None:
    normalized_question = re.sub(r"[_$]+", " ", question)
    source_names = [".".join(parts) for parts in source_tables(sql)]
    normalized_sources = re.sub(r"[_$]+", " ", " ".join(source_names))
    requested_entities = [
        entity
        for entity, pattern in _QUERY_ENTITY_PATTERNS.items()
        if pattern.search(normalized_question)
    ]
    missing_entities = [
        entity
        for entity in requested_entities
        if not _QUERY_ENTITY_PATTERNS[entity].search(normalized_sources)
    ]
    if missing_entities:
        requested = ", ".join(missing_entities)
        raise HTTPException(
            status_code=422,
            detail=(
                f"Generated SQL does not address the requested {requested} data for the "
                f"{domain} domain. No query was run. Check that the selected domain contains "
                "that data or rephrase the question using an available metric."
            ),
        )
    # Specific measures must appear somewhere in the statement (column, expression or alias);
    # otherwise the model substituted a different metric, e.g. total amount for "fee revenue".
    normalized_sql = re.sub(r"[_$]+", " ", sql)
    missing_measures = [
        measure
        for measure, pattern in _QUERY_MEASURE_PATTERNS.items()
        if pattern.search(normalized_question) and not pattern.search(normalized_sql)
    ]
    if missing_measures:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Generated SQL does not reference the requested {', '.join(missing_measures)} data for the "
                f"{domain} domain, so it cannot answer the question. No query was run. The authorized source "
                "may not contain that measure."
            ),
        )


def _stale_weekly_aggregate_error(
    question: str,
    sql: str,
    rows: list[dict[str, Any]],
    domain: str,
) -> str | None:
    if (
        domain != "retail"
        or "last week" not in question.lower()
        or "agg_retail_orders_daily" not in sql.lower()
        or not rows
    ):
        return None

    latest_value = rows[0].get("latest_data_date")
    period_start_value = rows[0].get("requested_period_start")
    if latest_value is None:
        return "The Retail order aggregate has no dated source rows, so last week's order count cannot be verified."
    if period_start_value is None:
        return "The Retail order aggregate did not return the requested week boundary, so its freshness cannot be verified."
    try:
        latest_date = date.fromisoformat(str(latest_value)[:10])
        period_start = date.fromisoformat(str(period_start_value)[:10])
    except ValueError:
        return "The Retail order aggregate returned an invalid date, so last week's order count cannot be verified."

    if latest_date < period_start:
        return (
            f"The Retail source is stale: its latest order data is {latest_date.isoformat()}, "
            f"before the requested week starting {period_start.isoformat()}. "
            "I can't report last week's order count until the source is refreshed."
        )
    return None


def _validate_query_columns(sql: str, schema_ctx: dict[str, Any], domain: str) -> None:
    """Reject column references that do not exist in the referenced tables' metadata.

    Conservative by design: statements with CTEs or derived tables are left to the warehouse's
    own validation rather than risk rejecting valid SQL.
    """
    if settings.warehouse != "databricks":
        return
    try:
        tree = parse_one(sql, read="databricks")
    except ParseError:
        return
    if tree.find(exp.CTE) or any(isinstance(source.this, exp.Subquery) for source in tree.find_all(exp.From, exp.Join)):
        return
    columns_by_table = {
        str(table).lower(): {str(column.get("name", "")).lower() for column in columns}
        for table, columns in schema_ctx.get("tables", {}).items()
    }
    aliases: dict[str, str] = {}
    ambiguous: set[str] = set()
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if name not in columns_by_table:
            return
        for key in {name, table.alias_or_name.lower()}:
            # The same alias bound to different tables in different scopes cannot be resolved
            # without scope analysis; leave those references to the warehouse.
            if aliases.get(key, name) != name:
                ambiguous.add(key)
            aliases[key] = name
    output_aliases = {alias.alias.lower() for alias in tree.find_all(exp.Alias)}
    referenced = sorted(set(aliases.values()))
    unknown: dict[str, str] = {}
    for column in tree.find_all(exp.Column):
        name = column.name.lower()
        if not name or isinstance(column.this, exp.Star):
            continue
        qualifier = column.table.lower()
        if qualifier:
            table = aliases.get(qualifier)
            if table is not None and qualifier not in ambiguous and name not in columns_by_table[table]:
                unknown[f"{qualifier}.{name}"] = f"not a column of {table}"
        elif name not in output_aliases and not any(name in columns_by_table[table] for table in referenced):
            unknown[name] = f"not a column of {', '.join(referenced)}"
    if unknown:
        # Name the table each column was checked against, so a column that exists elsewhere in
        # the domain (for example txn_timestamp on gold_transactions) is not mistaken for missing.
        details = "; ".join(f"{column} ({reason})" for column, reason in sorted(unknown.items()))
        raise HTTPException(
            status_code=422,
            detail=(
                f"Generated SQL references column(s) that do not exist in the referenced {domain} "
                f"table(s): {details}. No query was run."
            ),
        )


def _validate_query_sources(sql: str, schema_ctx: dict[str, Any], domain: str) -> None:
    available_tables = {str(table).lower() for table in schema_ctx.get("tables", {})}
    # Parsed sources (CTEs excluded): FROM inside EXTRACT/TRIM/SUBSTRING is not a table.
    for source in source_tables(sql):
        parts = [part.strip().strip("`\"").lower() for part in source]
        table = parts[-1]
        if table not in available_tables:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Generated SQL references '{table}', which is not present in the "
                    f"authorized {domain} source schema. No query was run."
                ),
            )
        if settings.warehouse == "databricks" and len(parts) > 1:
            expected_schema = settings.databricks_domain_schema.lower()
            expected_catalog = databricks_domain_catalog(domain).lower()
            if parts[-2] != expected_schema or (
                len(parts) == 3 and parts[0] != expected_catalog
            ):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Generated SQL references a catalog/schema outside the "
                        f"authorized {domain} source ({expected_catalog}.{expected_schema}). "
                        "No query was run."
                    ),
                )
        if settings.warehouse == "snowflake" and len(parts) > 1:
            expected_schema = settings.snowflake_schema.lower()
            expected_database = settings.snowflake_database.lower()
            in_configured_schema = (
                len(parts) == 2 and parts[0] == expected_schema
            ) or (
                len(parts) == 3
                and parts[0] == expected_database
                and parts[1] == expected_schema
            )
            if not in_configured_schema:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Generated SQL references a schema outside the configured "
                        f"{domain} source ({settings.snowflake_database}.{settings.snowflake_schema}). "
                        "No query was run."
                    ),
                )


def _risk_ratio_query(domain: str, domain_sql: str, parameterized: bool = False) -> str:
    predicate = "=:d" if parameterized else f"= {domain_sql}"
    if domain == "healthcare":
        return f"""
            SELECT f.facility_region AS region,
                   COALESCE(SUM(c.approved_amount), 0) AS numerator,
                   COALESCE(SUM(c.claim_amount), 0) AS denominator
            FROM healthcare_facilities f
            LEFT JOIN healthcare_encounters e
                ON e.tenant_id = f.tenant_id
                AND e.facility_key = f.facility_key
                AND e.tenant_id {predicate}
            LEFT JOIN healthcare_claims c
                ON c.tenant_id = e.tenant_id
                AND c.encounter_key = e.encounter_key
                AND c.tenant_id {predicate}
            WHERE f.tenant_id {predicate}
            GROUP BY f.facility_region
            ORDER BY f.facility_region
        """
    if domain == "insurance":
        return f"""
            SELECT regions.region AS region,
                   COALESCE(claims_by_region.numerator, 0) AS numerator,
                   COALESCE(premium_by_region.denominator, 0) AS denominator
            FROM (
                SELECT region FROM customers WHERE domain {predicate} GROUP BY region
            ) regions
            LEFT JOIN (
                SELECT cu.region AS region, SUM(o.total_amount) AS denominator
                FROM orders o
                JOIN customers cu ON cu.id = o.customer_id
                WHERE o.domain {predicate} AND cu.domain {predicate} AND o.status != 'cancelled'
                GROUP BY cu.region
            ) premium_by_region ON premium_by_region.region = regions.region
            LEFT JOIN (
                SELECT cu.region AS region, SUM(cl.amount) AS numerator
                FROM claims cl
                JOIN customers cu ON cu.id = cl.customer_id
                WHERE cl.domain {predicate} AND cu.domain {predicate}
                GROUP BY cu.region
            ) claims_by_region ON claims_by_region.region = regions.region
            ORDER BY region
        """

    exposure_filter = "o.status = 'delinquent'" if domain == "banking" else "o.status = 'cancelled'"
    denominator_filter = "o.status != 'cancelled'" if domain == "banking" else "1 = 1"
    return f"""
        SELECT cu.region AS region,
               COALESCE(SUM(CASE WHEN {exposure_filter} THEN o.total_amount ELSE 0 END), 0) AS numerator,
               COALESCE(SUM(CASE WHEN {denominator_filter} THEN o.total_amount ELSE 0 END), 0) AS denominator
        FROM orders o
        JOIN customers cu ON cu.id = o.customer_id
        WHERE o.domain {predicate} AND cu.domain {predicate}
        GROUP BY cu.region
        ORDER BY cu.region
    """


def _build_risk_ratio_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    risk_rows = []
    for row in rows:
        numerator = float(row.get("numerator") or 0)
        denominator = float(row.get("denominator") or 0)
        risk_rows.append(
            {
                "region": str(row["region"]),
                "numerator": numerator,
                "denominator": denominator,
                "loss_ratio": numerator / denominator if denominator else 0.0,
            }
        )
    return risk_rows


def _databricks_dashboard_queries(domain: str) -> list[str]:
    if domain == "banking":
        accounts = "banking.gold.gold_accounts"
        branches = "banking.gold.gold_branches"
        transactions = "banking.gold.gold_transactions"
        month = "date_format(txn_timestamp, 'yyyy-MM')"

        return [
            f"""
                SELECT COALESCE(SUM(balance), 0) AS volume,
                       COUNT_IF(lower(status) != 'closed') AS events
                FROM {accounts}
            """,
            f"""
                SELECT COUNT(DISTINCT customer_id) AS customers,
                       COALESCE(
                           SUM(CASE WHEN lower(status) = 'closed'
                                    THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0),
                           0
                       ) AS churn_rate
                FROM {accounts}
            """,
            f"""
                SELECT b.state AS label,
                       COUNT(a.account_id) AS events,
                       COALESCE(SUM(a.balance), 0) AS value
                FROM {accounts} a
                JOIN {branches} b ON b.branch_id = a.branch_id
                WHERE lower(a.status) != 'closed'
                GROUP BY b.state
                ORDER BY value DESC
            """,
            f"""
                SELECT account_type AS label,
                       COALESCE(SUM(balance), 0) AS value
                FROM {accounts}
                WHERE lower(status) != 'closed'
                GROUP BY account_type
                ORDER BY value DESC
            """,
            f"""
                SELECT {month} AS label,
                       COALESCE(SUM(amount), 0) AS value,
                       COUNT(*) AS events
                FROM {transactions}
                GROUP BY {month}
                ORDER BY label
            """,
            f"""
                SELECT txn_type AS label,
                       amount AS value,
                       txn_timestamp AS created_at
                FROM {transactions}
                ORDER BY txn_timestamp DESC
                LIMIT 12
            """,
            f"""
                SELECT b.state AS region,
                       t.merchant_category AS category,
                       COALESCE(SUM(t.amount), 0) AS value
                FROM {transactions} t
                JOIN {accounts} a ON a.account_id = t.account_id
                JOIN {branches} b ON b.branch_id = a.branch_id
                GROUP BY b.state, t.merchant_category
            """,
            "SELECT CAST(0 AS DOUBLE) AS claims",
            f"""
                SELECT b.state AS region,
                       COALESCE(
                           SUM(CASE WHEN lower(a.status) = 'dormant'
                                    THEN a.balance ELSE 0 END), 0
                       ) AS numerator,
                       COALESCE(SUM(a.balance), 0) AS denominator
                FROM {accounts} a
                JOIN {branches} b ON b.branch_id = a.branch_id
                GROUP BY b.state
                ORDER BY b.state
            """,
            f"""
                SELECT date_format(t.txn_timestamp, 'yyyy-MM') AS label,
                       a.account_type AS category,
                       COALESCE(SUM(t.amount), 0) AS value
                FROM {transactions} t
                JOIN {accounts} a ON a.account_id = t.account_id
                WHERE lower(a.status) != 'closed'
                GROUP BY date_format(t.txn_timestamp, 'yyyy-MM'), a.account_type
                ORDER BY label, category
                LIMIT 200
            """,
        ]

    if domain == "insurance":
        # Verified insurance.gold tables: gold_policies, gold_policyholders, gold_claims,
        # gold_premium_payments.
        policies = "gold_policies p JOIN gold_policyholders h ON h.holder_id = p.holder_id"
        active = "upper(p.policy_status) != 'CANCELLED'"
        month = "date_format(p.start_date, 'yyyy-MM')"
        paid_on = "COALESCE(paid_date, due_date)"
        return [
            """
                SELECT COALESCE(SUM(premium_amount), 0) AS volume,
                       COUNT_IF(upper(policy_status) != 'CANCELLED') AS events
                FROM gold_policies
            """,
            """
                SELECT COUNT(DISTINCT holder_id) AS customers,
                       COALESCE(
                           SUM(CASE WHEN upper(policy_status) = 'CANCELLED' THEN 1 ELSE 0 END)
                           / NULLIF(COUNT(*), 0),
                           0
                       ) AS churn_rate
                FROM gold_policies
            """,
            f"""
                SELECT h.state AS label, COUNT(*) AS events,
                       COALESCE(SUM(p.premium_amount), 0) AS value
                FROM {policies}
                WHERE {active}
                GROUP BY h.state ORDER BY value DESC
            """,
            f"""
                SELECT p.policy_type AS label, COALESCE(SUM(p.premium_amount), 0) AS value
                FROM gold_policies p
                WHERE {active}
                GROUP BY p.policy_type ORDER BY value DESC
            """,
            f"""
                SELECT {month} AS label, COALESCE(SUM(p.premium_amount), 0) AS value,
                       COUNT(*) AS events
                FROM gold_policies p
                WHERE {active}
                GROUP BY {month} ORDER BY label
            """,
            f"""
                SELECT mode AS label,
                       COALESCE(CASE WHEN paid_date IS NULL THEN amount_due ELSE amount_paid END, 0) AS value,
                       CAST({paid_on} AS TIMESTAMP) AS created_at,
                       CASE WHEN paid_date IS NULL THEN 'Pending' ELSE 'Paid' END AS status
                FROM gold_premium_payments
                WHERE {paid_on} <= current_date()
                ORDER BY {paid_on} DESC LIMIT 12
            """,
            f"""
                SELECT h.state AS region, p.policy_type AS category,
                       COALESCE(SUM(p.premium_amount), 0) AS value
                FROM {policies}
                WHERE {active}
                GROUP BY h.state, p.policy_type
            """,
            "SELECT COALESCE(SUM(claimed_amount), 0) AS claims FROM gold_claims",
            f"""
                SELECT premiums.region, COALESCE(losses.numerator, 0) AS numerator,
                       premiums.denominator
                FROM (
                    SELECT h.state AS region, SUM(p.premium_amount) AS denominator
                    FROM {policies}
                    WHERE {active}
                    GROUP BY h.state
                ) premiums
                LEFT JOIN (
                    SELECT h.state AS region, SUM(c.claimed_amount) AS numerator
                    FROM gold_claims c
                    JOIN gold_policies p ON p.policy_id = c.policy_id
                    JOIN gold_policyholders h ON h.holder_id = p.holder_id
                    GROUP BY h.state
                ) losses ON losses.region = premiums.region
                ORDER BY premiums.region
            """,
            f"""
                SELECT {month} AS label, p.policy_type AS category,
                       COALESCE(SUM(p.premium_amount), 0) AS value
                FROM gold_policies p
                WHERE {active}
                GROUP BY {month}, p.policy_type
                ORDER BY label DESC, category
                LIMIT 200
            """,
        ]

    if domain == "retail":
        # Verified retail.gold tables. Order items carry no line total, so revenue is net
        # item sales; future-dated source orders are excluded from every metric.
        items = (
            "gold_order_items i "
            "JOIN gold_orders o ON o.order_id = i.order_id "
            "JOIN gold_products p ON p.product_id = i.product_id "
            "JOIN gold_stores s ON s.store_id = o.store_id"
        )
        dated = "o.order_date <= current_timestamp()"
        active = f"upper(o.order_status) != 'CANCELLED' AND {dated}"
        net = RETAIL_NET_SALES
        month = "date_format(o.order_date, 'yyyy-MM')"
        return [
            f"""
                SELECT COALESCE(SUM({net}), 0) AS volume,
                       COUNT(DISTINCT o.order_id) AS events
                FROM {items}
                WHERE {active}
            """,
            """
                SELECT COUNT(DISTINCT customer_id) AS customers,
                       COALESCE(
                           SUM(CASE WHEN upper(order_status) = 'CANCELLED' THEN 1 ELSE 0 END)
                           / NULLIF(COUNT(*), 0),
                           0
                       ) AS churn_rate
                FROM gold_orders
                WHERE order_date <= current_timestamp()
            """,
            f"""
                SELECT s.region AS label, COUNT(DISTINCT o.order_id) AS events,
                       COALESCE(SUM({net}), 0) AS value
                FROM {items}
                WHERE {active}
                GROUP BY s.region ORDER BY value DESC
            """,
            f"""
                SELECT p.category AS label, COALESCE(SUM({net}), 0) AS value
                FROM {items}
                WHERE {active}
                GROUP BY p.category ORDER BY value DESC
            """,
            f"""
                SELECT {month} AS label, COALESCE(SUM({net}), 0) AS value,
                       COUNT(DISTINCT o.order_id) AS events
                FROM {items}
                WHERE {active}
                GROUP BY {month} ORDER BY label
            """,
            f"""
                SELECT o.channel AS label, pay.amount AS value,
                       o.order_date AS created_at, o.order_status AS status
                FROM gold_orders o
                JOIN gold_payments pay ON pay.order_id = o.order_id
                WHERE {dated}
                ORDER BY o.order_date DESC LIMIT 12
            """,
            f"""
                SELECT s.region AS region, p.category AS category,
                       COALESCE(SUM({net}), 0) AS value
                FROM {items}
                WHERE {active}
                GROUP BY s.region, p.category
            """,
            "SELECT CAST(0 AS DOUBLE) AS claims",
            f"""
                SELECT s.region AS region,
                       COALESCE(SUM(CASE WHEN upper(o.order_status) = 'CANCELLED'
                                         THEN {net} ELSE 0 END), 0) AS numerator,
                       COALESCE(SUM({net}), 0) AS denominator
                FROM {items}
                WHERE {dated}
                GROUP BY s.region ORDER BY s.region
            """,
            f"""
                SELECT {month} AS label, p.category AS category,
                       COALESCE(SUM({net}), 0) AS value
                FROM {items}
                WHERE {active}
                GROUP BY {month}, p.category
                ORDER BY label DESC, category
                LIMIT 200
            """,
        ]

    if domain == "finance":
        catalog = databricks_domain_catalog(domain)
        qualified_catalog = f"`{catalog}`"
        return [
            # 1. Latest verified account balance aggregate
            f"""
                SELECT COALESCE(total_balance, 0) AS volume,
                       COALESCE(record_count, 0) AS events
                FROM {qualified_catalog}.gold.agg_accounts_monthly
                WHERE _gold_month = (
                    SELECT MAX(_gold_month)
                    FROM {qualified_catalog}.gold.agg_accounts_monthly
                )
            """,

            # 2. Customer/churn metrics are not available in the
            # verified account-balance aggregate.
            """
                SELECT CAST(NULL AS BIGINT) AS customers,
                       CAST(NULL AS DOUBLE) AS churn_rate
            """,

            # 3. No verified regional breakdown yet.
            """
                SELECT CAST(NULL AS STRING) AS label,
                       CAST(NULL AS BIGINT) AS events,
                       CAST(NULL AS DOUBLE) AS value
                WHERE 1 = 0
            """,

            # 4. No verified category breakdown yet.
            """
                SELECT CAST(NULL AS STRING) AS label,
                       CAST(NULL AS DOUBLE) AS value
                WHERE 1 = 0
            """,

            # 5. Monthly account balance trend
            f"""
                SELECT _gold_month AS label,
                       COALESCE(total_balance, 0) AS value,
                       COALESCE(record_count, 0) AS events
                FROM {qualified_catalog}.gold.agg_accounts_monthly
                ORDER BY _gold_month
            """,

            # 6. Recent daily account balance aggregates
            f"""
                SELECT 'Daily account balance' AS label,
                       COALESCE(total_balance, 0) AS value,
                       CAST(_gold_date AS TIMESTAMP) AS created_at
                FROM {qualified_catalog}.gold.agg_accounts_daily
                ORDER BY _gold_date DESC
                LIMIT 12
            """,

            # 7. No verified region/category heatmap available.
            """
                SELECT CAST(NULL AS STRING) AS region,
                       CAST(NULL AS STRING) AS category,
                       CAST(NULL AS DOUBLE) AS value
                WHERE 1 = 0
            """,

            # 8. Claims are not represented by these account aggregates.
            """
                SELECT CAST(NULL AS DOUBLE) AS claims
            """,

            # 9. No verified Finance risk-ratio numerator/denominator.
            """
                SELECT CAST(NULL AS STRING) AS region,
                       CAST(NULL AS DOUBLE) AS numerator,
                       CAST(NULL AS DOUBLE) AS denominator
                WHERE 1 = 0
            """,

            # Category history is not available in the verified account aggregates.
            """
                SELECT CAST(NULL AS STRING) AS label,
                       CAST(NULL AS STRING) AS category,
                       CAST(NULL AS DOUBLE) AS value
                WHERE 1 = 0
                LIMIT 200
            """,
        ]

    raise GuardrailError(
        f"No Databricks business catalog is configured for the {domain} dashboard. "
        "Configure its authorized catalog and dashboard source mapping before using this domain."
    )


def _databricks_healthcare_dashboard_queries() -> list[str]:
    month = "date_format(claim_date, 'yyyy-MM')"
    return [
        """
            SELECT COALESCE(SUM(total_approved_amount), 0) AS volume,
                   (SELECT COUNT(*) FROM gold_encounters) AS events,
                   COALESCE(SUM(total_billed_amount), 0) AS claims
            FROM agg_claims_monthly
            LIMIT 1
        """,
        """
            SELECT COUNT(*) AS customers,
                   CAST(NULL AS DOUBLE) AS churn_rate
            FROM gold_patients
            LIMIT 1
        """,
        """
            SELECT f.state AS label, COUNT(DISTINCT e.encounter_id) AS events,
                   COALESCE(SUM(c.approved_amount), 0) AS value
            FROM gold_facilities f
            LEFT JOIN gold_encounters e ON e.facility_id = f.facility_id
            LEFT JOIN gold_claims c ON c.encounter_id = e.encounter_id
            GROUP BY f.state ORDER BY value DESC
            LIMIT 200
        """,
        """
            SELECT claim_status AS label, COALESCE(SUM(billed_amount), 0) AS value
            FROM gold_claims
            GROUP BY claim_status ORDER BY value DESC
            LIMIT 200
        """,
        """
            SELECT _gold_month AS label,
                   COALESCE(total_approved_amount, 0) AS value,
                   COALESCE(record_count, 0) AS events
            FROM agg_claims_monthly
            ORDER BY _gold_month
            LIMIT 200
        """,
        """
            SELECT 'Approved claims' AS label,
                   COALESCE(total_approved_amount, 0) AS value,
                   CAST(_gold_date AS TIMESTAMP) AS created_at
            FROM agg_claims_daily
            ORDER BY _gold_date DESC
            LIMIT 12
        """,
        """
            SELECT f.state AS region, e.encounter_type AS category,
                   COUNT(DISTINCT e.encounter_id) AS value
            FROM gold_encounters e
            JOIN gold_facilities f ON f.facility_id = e.facility_id
            GROUP BY f.state, e.encounter_type
            LIMIT 200
        """,
        """
            SELECT f.state AS region,
                   COALESCE(SUM(c.approved_amount), 0) AS numerator,
                   COALESCE(SUM(c.billed_amount), 0) AS denominator
            FROM gold_claims c
            JOIN gold_encounters e ON e.encounter_id = c.encounter_id
            JOIN gold_facilities f ON f.facility_id = e.facility_id
            GROUP BY f.state ORDER BY f.state
            LIMIT 200
        """,
        """
            SELECT CAST(NULL AS STRING) AS label,
                   CAST(NULL AS DOUBLE) AS value
            WHERE 1 = 0
            LIMIT 200
        """,
        f"""
            SELECT claim_status AS category, {month} AS label,
                   COALESCE(SUM(billed_amount), 0) AS value
            FROM gold_claims
            GROUP BY claim_status, {month}
            ORDER BY label, category
            LIMIT 200
        """,
    ]


class Orchestrator:
    def __init__(self) -> None:
        self.graph = ContextGraphService()
        self.assistant = BusinessAssistantGraph(self.graph)
        self.tabpfn = TabPFNService()
        self._ready = False

    def warmup(self, db: Session) -> None:
        self.graph.rebuild(db)
        self._ready = True

    def run(
        self,
        db: Session,
        question: str,
        user: AuthUser,
        llm_override: str | None = None,
        persist_history: bool = True,
        conversation_context: list[dict[str, str]] | None = None,
        conversation_id: UUID | None = None,
        approved_review: HitlReview | None = None,
    ) -> QueryResponse:
        with trace_steps() as steps:
            return self._run(
                db,
                question,
                user,
                llm_override,
                persist_history,
                conversation_context,
                conversation_id,
                approved_review,
                steps,
            )

    def _run(
        self,
        db: Session,
        question: str,
        user: AuthUser,
        llm_override: str | None,
        persist_history: bool,
        conversation_context: list[dict[str, str]] | None,
        conversation_id: UUID | None,
        approved_review: HitlReview | None,
        steps: list[dict[str, Any]],
    ) -> QueryResponse:
        if not self._ready:
            try:
                self.warmup(db)
            except GuardrailError as exc:
                if settings.warehouse not in {"databricks", "snowflake"}:
                    raise
                raise HTTPException(
                    status_code=503,
                    detail=(
                        f"The configured {settings.warehouse} warehouse is unavailable. "
                        f"Check the catalog/schema settings and warehouse permissions: {exc}"
                    ),
                ) from exc

        require_sensitive_request_access(question, user.role)
        admin_sensitive_request = (
            user.role == "admin" and requested_sensitive_type(question) == "admin_only"
        )
        if admin_sensitive_request:
            try:
                self.graph.refresh_warehouse_schema()
            except GuardrailError as exc:
                raise HTTPException(
                    status_code=503,
                    detail=f"Could not inspect the connected schema for approved sensitive fields: {exc}",
                ) from exc
        try:
            assistant_state = self.assistant.invoke(
                question,
                user.domain,
                llm_override,
                include_all_tables=admin_sensitive_request,
                conversation_context=conversation_context,
            )
        except SchemaRagError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Business schema retrieval is unavailable: {exc}",
            ) from exc
        plan = assistant_state["plan"]
        schema_ctx = assistant_state["schema_context"]
        audit_id = f"AU-{int(datetime.utcnow().timestamp()) % 100000:04d}"
        # A follow-up rewritten by the planner names its metric and period explicitly.
        resolved_question = plan.get("standalone_question") or question
        respond = partial(
            self._respond,
            db,
            user=user,
            question=question,
            plan=plan,
            schema_ctx=schema_ctx,
            steps=steps,
            audit_id=audit_id,
            persist_history=persist_history,
            conversation_id=conversation_id,
        )

        if plan.get("needs_clarification"):
            return respond(
                answer=plan.get("clarification_question") or "Could you clarify your question?",
                status="needs_clarification",
                sql=None,
                table=[],
                guardrail_ok=True,
                error=None,
            )
        if plan.get("intent") == "help":
            record_step("synthesis", "built-in description of the assistant (no data queried)")
            return respond(
                answer=_ABOUT_ASSISTANT.format(domain=DOMAIN_LABELS.get(user.domain, user.domain)),
                status="complete",
                sql=None,
                table=[],
                guardrail_ok=True,
                error=None,
            )
        if plan.get("intent") == "general":
            return respond(
                answer=self._general_answer(resolved_question, llm_override),
                status="complete",
                sql=None,
                table=[],
                guardrail_ok=True,
                error=None,
            )
        if user.domain == "healthcare" and plan.get("is_prediction"):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Healthcare prediction is disabled until a clinically validated model, "
                    "cohort policy, and governance review are configured."
                ),
            )
        try:
            sql = generate_sql(
                resolved_question,
                schema_ctx,
                bool(plan.get("is_prediction")),
                user.domain,
                llm_override,
                role=user.role,
                conversation_context=conversation_context,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _validate_query_sources(sql, schema_ctx, user.domain)
        _validate_query_columns(sql, schema_ctx, user.domain)
        _validate_query_relevance(resolved_question, sql, user.domain)
        if user.domain == "healthcare":
            try:
                sql = scope_healthcare_sql(sql, user.tenant_id, schema_ctx)
            except GuardrailError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        require_sql_sensitive_access(sql, user.role)
        record_step("sql_validation", "read-only, authorized sources and columns, sensitive fields")

        started = time.perf_counter()
        source_label = _source_label(user.domain)
        try:
            table = sanitize_sensitive_rows(execute_readonly(sql, user.domain), user.role)
            guardrail_ok = True
            error = None
            record_step(
                "sql_execution",
                source_label,
                detail=f"{len(table)} row(s) returned",
                latency_ms=(time.perf_counter() - started) * 1000,
            )
        except GuardrailError as exc:
            table = []
            guardrail_ok = False
            error = str(exc)
            record_step("sql_execution", source_label, ok=False, detail=error[:300])
        if not guardrail_ok:
            return respond(answer=error, status="error", sql=sql, table=table, guardrail_ok=False, error=error)

        stale = _stale_weekly_aggregate_error(question, sql, table, user.domain)
        if stale:
            return respond(answer=stale, status="no_data", sql=sql, table=table, guardrail_ok=True, error=stale)
        if _is_empty_result(table):
            # An aggregate over no matching rows (e.g. COALESCE(SUM(...), 0)) is not a measured
            # zero; report the source's real coverage instead of "0".
            answer = (
                "The query matched no records for this question, so there is no value to report for the "
                "requested period." + self._availability_note(sql, schema_ctx, user)
            )
            return respond(answer=answer, status="no_data", sql=sql, table=table, guardrail_ok=True, error=None)

        prediction = None
        uncertainty = None
        hitl = None
        if plan.get("is_prediction"):
            if approved_review is None and user.role != "admin" and settings.hitl_prediction_review:
                review = HitlReview(
                    audit_id=audit_id,
                    username=user.username,
                    domain=user.domain,
                    tenant_id=user.tenant_id,
                    question=question,
                    status="pending",
                    reason="prediction",
                    conversation_id=str(conversation_id) if conversation_id else None,
                )
                if persist_history:
                    db.add(review)
                    db.flush()
                record_step("hitl", "administrator approval required before the model runs")
                return respond(
                    answer=(
                        "This question asks for a model prediction. Predictions for business users are released "
                        "only after an administrator approves them, so the TabPFN model has not been run. "
                        f"Review {audit_id} is pending; the historical data retrieved for it is shown below. "
                        "After approval, choose “Run approved prediction” to continue."
                    ),
                    status="awaiting_review",
                    sql=sql,
                    table=table,
                    guardrail_ok=True,
                    error=None,
                    hitl={"status": "pending", "audit_id": audit_id, "review_id": review.id, "reason": "prediction"},
                )
            try:
                forecast = build_forecast(resolved_question, table)
                if forecast is not None:
                    prediction, uncertainty = self.tabpfn.forecast(forecast)
                    record_step("prediction", "self-hosted TabPFNRegressor (next-period forecast)")
                    answer = _forecast_answer(forecast, prediction, uncertainty)
                else:
                    prepared = build_features(resolved_question, table)
                    prediction, uncertainty = self.tabpfn.predict(prepared)
                    record_step("prediction", f"self-hosted TabPFN ({prediction.type}, cross-validated)")
                    answer = None
            except ValueError as exc:
                record_step("prediction", "self-hosted TabPFN", ok=False, detail=str(exc)[:300])
                return respond(
                    answer=f"{exc} The historical data retrieved for the question is shown below.",
                    status="insufficient_data",
                    sql=sql,
                    table=table,
                    guardrail_ok=True,
                    error=str(exc),
                )
            except Exception as exc:
                message = str(exc) if isinstance(exc, ModelUnavailableError) else (
                    f"The prediction model could not run ({type(exc).__name__}). No prediction was produced."
                )
                record_step("prediction", "self-hosted TabPFN", ok=False, detail=message[:300])
                return respond(
                    answer=f"{message} The historical data retrieved for the question is shown below.",
                    status="error",
                    sql=sql,
                    table=table,
                    guardrail_ok=True,
                    error=message,
                )
            if approved_review is not None:
                hitl = {
                    "status": "approved",
                    "audit_id": approved_review.audit_id,
                    "review_id": approved_review.id,
                    "reason": "prediction",
                    "reviewed_by": approved_review.reviewed_by,
                }
            else:
                hitl = {"status": "not_required", "audit_id": audit_id, "reason": "administrator request"}
            if answer is not None:
                return respond(
                    answer=answer,
                    status="complete",
                    sql=sql,
                    table=table,
                    guardrail_ok=True,
                    error=None,
                    prediction=prediction,
                    uncertainty=uncertainty,
                    hitl=hitl,
                )

        answer = synthesize(
            question,
            table,
            sql,
            prediction,
            uncertainty,
            user.domain,
            llm_override,
            conversation_context,
            intent=plan.get("intent"),
        )
        return respond(
            answer=answer,
            status="complete",
            sql=sql,
            table=table,
            guardrail_ok=True,
            error=None,
            prediction=prediction,
            uncertainty=uncertainty,
            hitl=hitl,
        )

    def _respond(
        self,
        db: Session,
        *,
        user: AuthUser,
        question: str,
        plan: dict[str, Any],
        schema_ctx: dict[str, Any],
        steps: list[dict[str, Any]],
        audit_id: str,
        persist_history: bool,
        conversation_id: UUID | None,
        answer: str,
        status: str,
        sql: str | None,
        table: list[dict[str, Any]],
        guardrail_ok: bool,
        error: str | None,
        prediction: PredictionPayload | None = None,
        uncertainty: UncertaintyPayload | None = None,
        hitl: dict[str, Any] | None = None,
    ) -> QueryResponse:
        schema_used = list(schema_ctx.get("tables", {}).keys())
        if settings.warehouse in {"databricks", "snowflake"}:
            counts = {}
        else:
            orders_count = db.query(Order).filter(Order.domain == user.domain).count()
            counts = {
                "bronze": orders_count + db.query(Claim).filter(Claim.domain == user.domain).count(),
                "silver": db.query(Customer).filter(Customer.domain == user.domain).count(),
                "gold": orders_count,
            }
        # Report only providers that actually produced output for this request.
        providers = sorted({
            step["engine"].split(":")[0]
            for step in steps
            if step["ok"] and step["engine"].split(":")[0] in _LLM_PROVIDERS
        })
        llm_provider = ", ".join(providers) or None
        if hitl is None and not guardrail_ok and persist_history:
            db.add(
                HitlReview(
                    audit_id=audit_id,
                    username=user.username,
                    domain=user.domain,
                    tenant_id=user.tenant_id,
                    question=question,
                    status="pending",
                    reason="guardrail",
                    conversation_id=str(conversation_id) if conversation_id else None,
                )
            )
            hitl = {"status": "pending", "audit_id": audit_id, "reason": "guardrail"}
        governance = model_gov(prediction, llm_provider)
        governance["approval"] = (
            "Administrator approval required before business users receive predictions"
            if settings.hitl_prediction_review
            else "Predictions are released without approval"
        )
        response = QueryResponse(
            answer=answer,
            sql=sql,
            table=table,
            plan=plan,
            schema_used=schema_used,
            prediction=prediction,
            uncertainty=uncertainty,
            error=error,
            domain=user.domain,
            llm_provider=llm_provider,
            audit_id=audit_id,
            chart=chart_spec(table),
            lineage=build_lineage(
                user.domain,
                question,
                sql,
                table,
                user.username,
                audit_id,
                schema_used,
                guardrail_ok,
                counts,
            ) if sql else None,
            guardrails=guardrail_report(sql, guardrail_ok, error) if sql else None,
            hitl=hitl or {"status": "not_required", "audit_id": audit_id},
            model_gov=governance,
            conversation_id=conversation_id,
            status=status,
            trace=list(steps),
        )
        if persist_history:
            db.add(
                QueryHistory(
                    username=user.username,
                    domain=user.domain,
                    tenant_id=user.tenant_id,
                    question=question,
                    sql=sql,
                    answer=answer,
                    result_json=response.model_dump_json(),
                    audit_id=audit_id,
                    tables_used=",".join(extract_tables(sql)),
                    row_count=len(table),
                    guardrail_ok=1 if guardrail_ok else 0,
                )
            )
            db.commit()
        return response

    @staticmethod
    def _general_answer(question: str, llm_override: str | None) -> str:
        text = complete_text(
            system=(
                "Give a short, general explanation of a business or analytics concept. You have no access to "
                "the user's data: do not state any figures, results or facts about their business."
            ),
            user=question,
            override=llm_override,
            stage="synthesis",
        )
        if not text or re.search(r"\d", text):
            record_step("synthesis", "general explanation withheld", ok=False, detail="Unavailable or contained figures")
            return (
                "I can only answer questions about your business with data from your authorized domain. "
                "Ask about a specific metric and period, for example “total transaction volume in March 2026”."
            )
        return "General explanation (no business data was queried): " + text.strip()

    def _availability_note(self, sql: str, schema_ctx: dict[str, Any], user: AuthUser) -> str:
        """Report the real date coverage of the queried source, using its date columns."""
        if user.domain == "healthcare":
            return ""
        tables = schema_ctx.get("tables", {})
        lowered = {str(name).lower(): name for name in tables}
        for source in extract_tables(sql):
            name = lowered.get(source.split(".")[-1].strip("`\"").lower())
            if name is None:
                continue
            date_columns = [
                column["name"]
                for column in tables[name]
                if re.search(r"date|timestamp", str(column.get("type", "")), re.IGNORECASE)
                or str(column["name"]).lower() in {"_gold_month", "month"}
            ]
            used = [c for c in date_columns if re.search(rf"\b{re.escape(c)}\b", sql, re.IGNORECASE)]
            column = (used or date_columns or [None])[0]
            if column is None or not _IDENTIFIER.fullmatch(str(column)) or not _IDENTIFIER.fullmatch(str(name)):
                continue
            try:
                bounds = execute_readonly(
                    f"SELECT MIN({column}) AS first_value, MAX({column}) AS last_value FROM {name} LIMIT 1",
                    user.domain,
                )
            except GuardrailError:
                return ""
            if not bounds or bounds[0].get("last_value") is None:
                return f" The {name} source currently contains no dated records."
            record_step("data_availability", f"{name}.{column} coverage check")
            first, last = str(bounds[0]["first_value"])[:10], str(bounds[0]["last_value"])[:10]
            relative = re.search(r"current_date|current_timestamp|now\(\)|getdate", sql, re.IGNORECASE)
            return (
                f" The {name} data available to you covers {first} to {last}"
                + (f", while the requested period is relative to today ({date.today().isoformat()})." if relative else ".")
            )
        return ""

    def dashboard_metrics(
        self,
        db: Session,
        domain: str,
        simulate: bool = False,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        if domain == "healthcare":
            if not tenant_id:
                raise GuardrailError("Healthcare dashboard access requires a tenant assignment.")
            return self._healthcare_dashboard_metrics(db, tenant_id)
        if settings.warehouse in {"databricks", "snowflake"}:
            return self._dashboard_metrics_remote_warehouse(db, domain)
        if simulate and settings.live_simulate:
            _simulate_tick(db, domain)

        metric_name = {
            "banking": "Deposits / flow",
            "finance": "AUM / notional",
            "insurance": "Gross written premium",
            "retail": "Revenue",
        }.get(domain, "Revenue")

        volume = db.execute(
            text(
                "SELECT COALESCE(SUM(total_amount),0) FROM orders WHERE domain=:d AND status!='cancelled'"
            ),
            {"d": domain},
        ).scalar()
        events = db.execute(
            text("SELECT COUNT(*) FROM orders WHERE domain=:d AND status!='cancelled'"),
            {"d": domain},
        ).scalar()
        customers = db.execute(text("SELECT COUNT(*) FROM customers WHERE domain=:d"), {"d": domain}).scalar()
        churn = db.execute(text("SELECT AVG(churned) FROM customers WHERE domain=:d"), {"d": domain}).scalar()
        by_region = [
            dict(r)
            for r in db.execute(
                text(
                    """
                    SELECT c.region AS label, COALESCE(SUM(o.total_amount),0) AS value
                    FROM customers c
                    LEFT JOIN orders o ON o.customer_id=c.id AND o.domain=:d AND o.status!='cancelled'
                    WHERE c.domain=:d
                    GROUP BY c.region ORDER BY value DESC
                    """
                ),
                {"d": domain},
            ).mappings()
        ]
        if settings.database_url.startswith("postgresql") or settings.warehouse == "databricks":
            month_sql = "to_char(order_date,'YYYY-MM')"
        else:
            month_sql = "strftime('%Y-%m', order_date)"
        by_category = [
            dict(r)
            for r in db.execute(
                text(
                    """
                    SELECT p.category AS label, COALESCE(SUM(oi.amount),0) AS value
                    FROM products p
                    LEFT JOIN order_items oi ON oi.product_id=p.id AND oi.domain=:d
                    WHERE p.domain=:d
                    GROUP BY p.category ORDER BY value DESC
                    """
                ),
                {"d": domain},
            ).mappings()
        ]
        category_trends = [
            dict(row)
            for row in db.execute(
                text(
                    f"""
                    SELECT p.category AS category, {month_sql} AS label,
                           COALESCE(SUM(oi.amount), 0) AS value
                    FROM products p
                    JOIN order_items oi ON oi.product_id = p.id AND oi.domain = :d
                    JOIN orders o ON o.id = oi.order_id AND o.domain = :d
                        AND o.status != 'cancelled'
                    WHERE p.domain = :d
                    GROUP BY p.category, {month_sql}
                    ORDER BY label, category
                    """
                ),
                {"d": domain},
            ).mappings()
        ]
        by_month = [
            dict(r)
            for r in db.execute(
                text(
                    f"""
                    SELECT {month_sql} AS label, COALESCE(SUM(total_amount),0) AS value, COUNT(*) AS events
                    FROM orders WHERE domain=:d AND status!='cancelled'
                    GROUP BY {month_sql} ORDER BY label
                    """
                ),
                {"d": domain},
            ).mappings()
        ]
        live = [
            dict(r)
            for r in db.execute(
                text(
                    """
                    SELECT event_type AS label, amount AS value, created_at
                    FROM live_events WHERE domain=:d
                    ORDER BY id DESC LIMIT 12
                    """
                ),
                {"d": domain},
            ).mappings()
        ]
        heatmap = [
            {"region": r["region"], "category": r["category"], "value": float(r["value"] or 0)}
            for r in db.execute(
                text(
                    """
                    SELECT c.region AS region, p.category AS category, COALESCE(SUM(oi.amount),0) AS value
                    FROM customers c
                    JOIN orders o ON o.customer_id = c.id AND o.domain=:d AND o.status!='cancelled'
                    JOIN order_items oi ON oi.order_id = o.id AND oi.domain=:d
                    JOIN products p ON p.id = oi.product_id
                    WHERE c.domain=:d
                    GROUP BY c.region, p.category
                    """
                ),
                {"d": domain},
            ).mappings()
        ]
        risk_definition = RISK_RATIO_DEFINITIONS[domain]
        risk_source_rows = [
            dict(row)
            for row in db.execute(
                text(_risk_ratio_query(domain, "", parameterized=True)),
                {"d": domain},
            ).mappings()
        ]
        loss_by_region = _build_risk_ratio_rows(risk_source_rows)
        risk_numerator = sum(row["numerator"] for row in loss_by_region)
        risk_denominator = sum(row["denominator"] for row in loss_by_region)
        audits = db.query(QueryHistory).filter(QueryHistory.domain == domain).order_by(QueryHistory.id.desc()).limit(12).all()
        audit_rows = [
            {
                "audit_id": a.audit_id or f"AU-{a.id}",
                "question": a.question,
                "sql": a.sql,
                "rows": a.row_count,
                "guardrail_ok": bool(a.guardrail_ok),
                "user": a.username,
                "created_at": str(a.created_at),
            }
            for a in audits
        ]
        passed = db.query(QueryHistory).filter(QueryHistory.domain == domain, QueryHistory.guardrail_ok == 1).count()
        total_q = db.query(QueryHistory).filter(QueryHistory.domain == domain).count()
        claims_total = 0.0
        if domain == "insurance":
            claims_total = float(
                db.execute(text("SELECT COALESCE(SUM(amount),0) FROM claims WHERE domain=:d"), {"d": domain}).scalar()
                or 0
            )
        return {
            "domain": domain,
            "domain_label": DOMAIN_LABELS.get(domain, domain),
            "metric_name": metric_name,
            "volume": float(volume or 0),
            "events": int(events or 0),
            "customers": int(customers or 0),
            "churn_rate": float(churn or 0),
            "claims": claims_total,
            "loss_ratio": risk_numerator / risk_denominator if risk_denominator else 0.0,
            "loss_ratio_label": risk_definition["label"],
            "loss_ratio_description": risk_definition["description"],
            "loss_numerator_label": risk_definition["numerator_label"],
            "loss_denominator_label": risk_definition["denominator_label"],
            "guardrail_pass_rate": (passed / total_q) if total_q else 1,
            "queries": total_q,
            "heatmap": heatmap,
            "loss_by_region": loss_by_region,
            "audits": audit_rows,
            "by_region": by_region,
            "by_category": by_category,
            "category_trends": [
                {**row, "value": float(row["value"] or 0)} for row in category_trends
            ],
            "by_month": [{**r, "value": float(r["value"]), "events": int(r["events"])} for r in by_month],
            "live": [
                {**r, "value": float(r["value"]), "created_at": str(r["created_at"])} for r in live
            ],
            "updated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
        }

    def _healthcare_dashboard_metrics(self, db: Session, tenant_id: str) -> dict[str, Any]:
        if settings.warehouse == "databricks":
            databricks_healthcare_tenant_catalog(tenant_id)
            results = execute_databricks_domain_batch(
                "healthcare",
                _databricks_healthcare_dashboard_queries(),
                tenant_id=tenant_id,
            )
            return Orchestrator._build_healthcare_dashboard_metrics(db, tenant_id, results)

        tenant = "'" + tenant_id.replace("'", "''") + "'"
        if settings.warehouse == "snowflake" or settings.database_url.startswith("postgresql"):
            month_expression = "TO_CHAR(service_date, 'YYYY-MM')"
        else:
            month_expression = "strftime('%Y-%m', service_date)"
        queries = [
            f"""
                SELECT COALESCE(SUM(approved_amount), 0) AS volume,
                       (SELECT COUNT(*) FROM healthcare_encounters WHERE tenant_id = {tenant}) AS events,
                       COALESCE(SUM(claim_amount), 0) AS claims
                FROM healthcare_claims
                WHERE tenant_id = {tenant}
            """,
            f"""
                SELECT
                    (SELECT COUNT(*) FROM healthcare_patients WHERE tenant_id = {tenant}) AS customers,
                    COALESCE(
                        (SELECT SUM(CASE WHEN lower(encounter_status) = 'cancelled' THEN 1 ELSE 0 END) * 1.0
                         / NULLIF(COUNT(*), 0)
                         FROM healthcare_encounters WHERE tenant_id = {tenant}),
                        0
                    ) AS churn_rate
            """,
            f"""
                SELECT f.facility_region AS label, COUNT(DISTINCT e.encounter_key) AS events,
                       COALESCE(SUM(c.approved_amount), 0) AS value
                FROM healthcare_facilities f
                LEFT JOIN healthcare_encounters e
                    ON e.tenant_id = f.tenant_id AND e.facility_key = f.facility_key
                LEFT JOIN healthcare_claims c
                    ON c.tenant_id = e.tenant_id AND c.encounter_key = e.encounter_key
                WHERE f.tenant_id = {tenant}
                GROUP BY f.facility_region ORDER BY value DESC
            """,
            f"""
                SELECT claim_status AS label, COALESCE(SUM(claim_amount), 0) AS value
                FROM healthcare_claims WHERE tenant_id = {tenant}
                GROUP BY claim_status ORDER BY value DESC
            """,
            f"""
                SELECT {month_expression} AS label, COALESCE(SUM(approved_amount), 0) AS value,
                       COUNT(*) AS events
                FROM healthcare_claims WHERE tenant_id = {tenant}
                GROUP BY {month_expression} ORDER BY label
            """,
            f"""
                SELECT claim_status AS label, claim_amount AS value, service_date AS created_at
                FROM healthcare_claims WHERE tenant_id = {tenant}
                ORDER BY service_date DESC LIMIT 12
            """,
            f"""
                SELECT f.facility_region AS region, e.encounter_type AS category,
                       COUNT(*) AS value
                FROM healthcare_encounters e
                JOIN healthcare_facilities f
                    ON f.tenant_id = e.tenant_id AND f.facility_key = e.facility_key
                WHERE e.tenant_id = {tenant}
                GROUP BY f.facility_region, e.encounter_type
            """,
            f"""
                SELECT f.facility_region AS region,
                       COALESCE(SUM(c.approved_amount), 0) AS numerator,
                       COALESCE(SUM(c.claim_amount), 0) AS denominator
                FROM healthcare_facilities f
                LEFT JOIN healthcare_encounters e
                    ON e.tenant_id = f.tenant_id AND e.facility_key = f.facility_key
                LEFT JOIN healthcare_claims c
                    ON c.tenant_id = e.tenant_id AND c.encounter_key = e.encounter_key
                WHERE f.tenant_id = {tenant}
                GROUP BY f.facility_region ORDER BY f.facility_region
            """,
            f"""
                SELECT metric_name AS label, AVG(metric_value) AS value
                FROM healthcare_quality_metrics WHERE tenant_id = {tenant}
                GROUP BY metric_name ORDER BY metric_name
            """,
            f"""
                SELECT claim_status AS category, {month_expression} AS label,
                       COALESCE(SUM(claim_amount), 0) AS value
                FROM healthcare_claims WHERE tenant_id = {tenant}
                GROUP BY claim_status, {month_expression}
                ORDER BY label, category
                LIMIT 200
            """,
        ]
        if settings.warehouse == "snowflake":
            results = execute_readonly_batch(queries)
        else:
            results = [
                [dict(row) for row in db.execute(text(query)).mappings()]
                for query in queries
            ]
        return Orchestrator._build_healthcare_dashboard_metrics(db, tenant_id, results)

    @staticmethod
    def _build_healthcare_dashboard_metrics(
        db: Session,
        tenant_id: str,
        results: list[list[dict[str, Any]]],
    ) -> dict[str, Any]:
        (
            overview,
            patient_stats,
            by_region,
            by_category,
            by_month,
            live,
            heatmap,
            loss_rows,
            quality_metrics,
            category_trends,
        ) = results
        stats = patient_stats[0] if patient_stats else {}
        definition = RISK_RATIO_DEFINITIONS["healthcare"]
        loss_by_region = _build_risk_ratio_rows(loss_rows)
        total_claim_amount = float(overview[0]["claims"] or 0) if overview else 0.0
        approved_amount = float(overview[0]["volume"] or 0) if overview else 0.0
        total_events = int(overview[0]["events"] or 0) if overview else 0
        tenant_history = db.query(QueryHistory).filter(
            QueryHistory.domain == "healthcare",
            QueryHistory.tenant_id == tenant_id,
        )
        audits = tenant_history.order_by(QueryHistory.id.desc()).limit(12).all()
        total_queries = tenant_history.count()
        passed = tenant_history.filter(QueryHistory.guardrail_ok == 1).count()
        return {
            "domain": "healthcare",
            "domain_label": DOMAIN_LABELS["healthcare"],
            "metric_name": "Approved claim amount",
            "volume": approved_amount,
            "events": total_events,
            "event_label": "Encounters",
            "customers": int(stats.get("customers") or 0),
            "customer_label": "Patients",
            "churn_rate": (
                float(stats["churn_rate"])
                if stats.get("churn_rate") is not None
                else None
            ),
            "churn_label": "Encounter cancellation rate",
            "claims": total_claim_amount,
            "loss_ratio": approved_amount / total_claim_amount if total_claim_amount else 0,
            "loss_ratio_label": definition["label"],
            "loss_ratio_description": definition["description"],
            "loss_numerator_label": definition["numerator_label"],
            "loss_denominator_label": definition["denominator_label"],
            "guardrail_pass_rate": (passed / total_queries) if total_queries else 1,
            "queries": total_queries,
            "heatmap": [
                {"region": row["region"], "category": row["category"], "value": float(row["value"] or 0)}
                for row in heatmap
            ],
            "heatmap_metric_name": "Encounters",
            "loss_by_region": loss_by_region,
            "audits": [
                {
                    "audit_id": row.audit_id or f"AU-{row.id}",
                    "question": row.question,
                    "sql": row.sql,
                    "rows": row.row_count,
                    "guardrail_ok": bool(row.guardrail_ok),
                    "user": row.username,
                    "created_at": str(row.created_at),
                }
                for row in audits
            ],
            "by_region": by_region,
            "by_category": by_category,
            "category_trends": [
                {**row, "value": float(row["value"] or 0)} for row in category_trends
            ],
            "quality_metrics": [
                {**row, "value": float(row["value"] or 0)} for row in quality_metrics
            ],
            "by_month": [
                {**row, "value": float(row["value"] or 0), "events": int(row["events"] or 0)}
                for row in by_month
            ],
            "live": [
                {**row, "value": float(row["value"] or 0), "created_at": str(row["created_at"])}
                for row in live
            ],
            "updated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
        }

    def _dashboard_metrics_remote_warehouse(self, db: Session, domain: str) -> dict[str, Any]:
        if domain not in DOMAIN_LABELS:
            raise ValueError(f"Unsupported business domain '{domain}'")
        if settings.warehouse == "databricks":
            (
                overview,
                customer_stats,
                by_region,
                by_category,
                by_month,
                live,
                heatmap,
                claims,
                loss_by_region_rows,
                category_trends,
            ) = execute_databricks_domain_batch(
                domain,
                _databricks_dashboard_queries(domain),
            )
            churn_label = {
                "banking": "Closed account rate",
                "insurance": "Cancelled policy rate",
                "retail": "Cancelled order rate",
            }.get(domain, "Churn")
        else:
            d = "'" + domain + "'"
            month_expression = "TO_CHAR(order_date, 'YYYY-MM')"
            queries = [
                f"""
                    SELECT COALESCE(SUM(total_amount), 0) AS volume, COUNT(*) AS events
                    FROM orders
                    WHERE domain = {d} AND status != 'cancelled'
                """,
                f"""
                    SELECT COUNT(*) AS customers, COALESCE(AVG(churned), 0) AS churn_rate
                    FROM customers WHERE domain = {d}
                """,
                f"""
                    SELECT c.region AS label, COUNT(o.id) AS events,
                           COALESCE(SUM(o.total_amount), 0) AS value
                    FROM customers c
                    LEFT JOIN orders o ON o.customer_id = c.id AND o.domain = {d}
                        AND o.status != 'cancelled'
                    WHERE c.domain = {d}
                    GROUP BY c.region ORDER BY value DESC
                """,
                f"""
                    SELECT p.category AS label, COALESCE(SUM(oi.amount), 0) AS value
                    FROM products p
                    LEFT JOIN order_items oi ON oi.product_id = p.id AND oi.domain = {d}
                    WHERE p.domain = {d}
                    GROUP BY p.category ORDER BY value DESC
                """,
                f"""
                    SELECT {month_expression} AS label,
                           COALESCE(SUM(total_amount), 0) AS value, COUNT(*) AS events
                    FROM orders
                    WHERE domain = {d} AND status != 'cancelled'
                    GROUP BY {month_expression} ORDER BY label
                """,
                f"""
                    SELECT event_type AS label, amount AS value, created_at
                    FROM live_events WHERE domain = {d} ORDER BY id DESC LIMIT 12
                """,
                f"""
                    SELECT c.region AS region, p.category AS category,
                           COALESCE(SUM(oi.amount), 0) AS value
                    FROM customers c
                    JOIN orders o ON o.customer_id = c.id AND o.domain = {d}
                        AND o.status != 'cancelled'
                    JOIN order_items oi ON oi.order_id = o.id AND oi.domain = {d}
                    JOIN products p ON p.id = oi.product_id
                    WHERE c.domain = {d}
                    GROUP BY c.region, p.category
                """,
                f"SELECT COALESCE(SUM(amount), 0) AS claims FROM claims WHERE domain = {d}",
                _risk_ratio_query(domain, d),
                f"""
                    SELECT p.category AS category,
                           TO_CHAR(o.order_date, 'YYYY-MM') AS label,
                           COALESCE(SUM(oi.amount), 0) AS value
                    FROM products p
                    JOIN order_items oi ON oi.product_id = p.id AND oi.domain = {d}
                    JOIN orders o ON o.id = oi.order_id AND o.domain = {d}
                        AND o.status != 'cancelled'
                    WHERE p.domain = {d}
                    GROUP BY p.category, TO_CHAR(o.order_date, 'YYYY-MM')
                    ORDER BY label, category
                    LIMIT 200
                """,
            ]
            (
                overview,
                customer_stats,
                by_region,
                by_category,
                by_month,
                live,
                heatmap,
                claims,
                loss_by_region_rows,
                category_trends,
            ) = execute_readonly_batch(queries)
            churn_label = "Churn"
        audits = (
            db.query(QueryHistory)
            .filter(QueryHistory.domain == domain)
            .order_by(QueryHistory.id.desc())
            .limit(12)
            .all()
        )
        audit_rows = [
            {
                "audit_id": row.audit_id or f"AU-{row.id}",
                "question": row.question,
                "sql": row.sql,
                "rows": row.row_count,
                "guardrail_ok": bool(row.guardrail_ok),
                "user": row.username,
                "created_at": str(row.created_at),
            }
            for row in audits
        ]
        passed = db.query(QueryHistory).filter(
            QueryHistory.domain == domain, QueryHistory.guardrail_ok == 1
        ).count()
        total_queries = db.query(QueryHistory).filter(QueryHistory.domain == domain).count()
        volume = float(overview[0]["volume"] or 0) if overview else 0.0
        claims_total = float(claims[0]["claims"] or 0) if claims else 0.0
        risk_definition = RISK_RATIO_DEFINITIONS[domain]
        loss_by_region = _build_risk_ratio_rows(loss_by_region_rows)
        risk_numerator = sum(row["numerator"] for row in loss_by_region)
        risk_denominator = sum(row["denominator"] for row in loss_by_region)
        customers_row = customer_stats[0] if customer_stats else {}
        return {
            "domain": domain,
            "domain_label": DOMAIN_LABELS[domain],
            "metric_name": {
                "banking": "Deposits / flow",
                "finance": "AUM / notional",
                "insurance": "Gross written premium",
                "retail": "Revenue",
            }.get(domain, "Revenue"),
            "volume": volume,
            "events": int(overview[0]["events"] or 0) if overview else 0,
            # None means the source has no such metric; the UI shows "Not available", never 0.
            "customers": (
                int(customers_row["customers"]) if customers_row.get("customers") is not None else None
            ),
            "churn_rate": (
                float(customers_row["churn_rate"]) if customers_row.get("churn_rate") is not None else None
            ),
            "churn_label": churn_label,
            "claims": claims_total if domain == "insurance" else 0.0,
            "loss_ratio": risk_numerator / risk_denominator if risk_denominator else None,
            "loss_ratio_label": risk_definition["label"],
            "loss_ratio_description": risk_definition["description"],
            "loss_numerator_label": risk_definition["numerator_label"],
            "loss_denominator_label": risk_definition["denominator_label"],
            "guardrail_pass_rate": (passed / total_queries) if total_queries else 1,
            "queries": total_queries,
            "heatmap": heatmap,
            "loss_by_region": loss_by_region,
            "audits": audit_rows,
            "by_region": by_region,
            "by_category": by_category,
            "category_trends": [
                {**row, "value": float(row["value"] or 0)} for row in category_trends
            ],
            "by_month": [
                {**row, "value": float(row["value"] or 0), "events": int(row["events"] or 0)}
                for row in by_month
            ],
            "live": [
                {**row, "value": float(row["value"] or 0), "created_at": str(row["created_at"])}
                for row in live
            ],
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }


def _simulate_tick(db: Session, domain: str) -> None:
    rng = Random()
    customer = db.query(Customer).filter(Customer.domain == domain).first()
    product = db.query(Product).filter(Product.domain == domain).first()
    if not customer or not product:
        return
    amount = round(rng.uniform(40, 900), 2)
    order = Order(
        domain=domain,
        customer_id=customer.id,
        order_date=date.today(),
        status="completed",
        total_amount=amount,
    )
    db.add(order)
    db.flush()
    db.add(
        OrderItem(
            domain=domain,
            order_id=order.id,
            product_id=product.id,
            quantity=1,
            amount=amount,
        )
    )
    db.add(LiveEvent(domain=domain, event_type="live_tick", amount=amount, created_at=datetime.utcnow()))
    if domain == "insurance" and rng.random() < 0.2:
        db.add(
            Claim(
                domain=domain,
                customer_id=customer.id,
                claim_date=date.today(),
                amount=round(amount * 0.6, 2),
                status="open",
            )
        )
    db.commit()
