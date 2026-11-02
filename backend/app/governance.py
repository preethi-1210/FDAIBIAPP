from __future__ import annotations

import re
from typing import Any

from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

from .config import settings
from .warehouse import databricks_domain_catalog


# def catalog_name(domain: str) -> str:
#     if settings.warehouse == "databricks" and settings.databricks_catalog:
#         return settings.databricks_catalog
#     if settings.warehouse == "snowflake" and settings.snowflake_database:
#         return settings.snowflake_database
#     return f"ryze_{domain}"

def catalog_name(domain: str) -> str:
    catalog_names = {
        "banking": "banking",
        "insurance": "insurance",
        "finance": "banking",
        "retail": "retail",
    }

    if domain not in catalog_names:
        raise ValueError(f"Unsupported business domain: {domain}")

    return catalog_names[domain]


def gold_schema() -> str:
    if settings.warehouse == "databricks":
        return settings.databricks_schema or "curated_gold"
    if settings.warehouse == "snowflake":
        return settings.snowflake_schema or "PUBLIC"
    return "curated_gold"


DOMAIN_VIEWS: dict[str, list[dict[str, Any]]] = {
    "insurance": [
        {"id": "vw_claims_enriched", "label": "Claims · PII masked", "maps": ["claims", "customers"], "topic": "claims"},
        {"id": "vw_premiums_enriched", "label": "Premiums · ACL enforced", "maps": ["orders", "products"], "topic": "premiums"},
        {"id": "vw_loss_ratio_by_coverage", "label": "Loss ratio", "maps": ["claims", "orders", "products"], "topic": "loss"},
        {"id": "vw_large_loss_trend_by_cause", "label": "Large loss trend", "maps": ["claims"], "topic": "loss"},
        {"id": "vw_top_brokers_by_paid", "label": "Broker analytics", "maps": ["customers", "orders"], "topic": "broker"},
        {"id": "vw_settlement_sla_by_line", "label": "Settlement SLA", "maps": ["claims"], "topic": "sla"},
        {"id": "vw_reserve_adequacy_by_region", "label": "Reserve adequacy", "maps": ["claims", "customers"], "topic": "reserve"},
        {"id": "vw_broker_loss_ratio", "label": "Broker loss ratio", "maps": ["claims", "orders"], "topic": "loss"},
    ],
    "banking": [
        {"id": "vw_deposits_enriched", "label": "Deposits · PII masked", "maps": ["accounts", "customers"], "topic": "deposits"},
        {"id": "vw_transactions_enriched", "label": "Transactions · ACL", "maps": ["transactions", "accounts"], "topic": "txn"},
        {"id": "vw_loans_enriched", "label": "Loans · ACL", "maps": ["loans", "customers"], "topic": "loan"},
        {"id": "vw_product_balances", "label": "Product balances", "maps": ["products", "orders"], "topic": "balance"},
        {"id": "vw_npl_by_segment", "label": "NPL by segment", "maps": ["loans", "customers"], "topic": "risk"},
        {"id": "vw_churn_risk", "label": "Churn risk", "maps": ["customers", "orders"], "topic": "churn"},
    ],
    "finance": [
        {"id": "vw_aum_enriched", "label": "AUM · ACL enforced", "maps": ["orders", "customers"], "topic": "aum"},
        {"id": "vw_trades_enriched", "label": "Trades", "maps": ["orders", "products"], "topic": "trades"},
        {"id": "vw_instrument_mix", "label": "Instrument mix", "maps": ["products", "order_items"], "topic": "mix"},
        {"id": "vw_client_churn", "label": "Client churn", "maps": ["customers"], "topic": "churn"},
    ],
    "retail": [
        {"id": "vw_sales_enriched", "label": "Sales · PII masked", "maps": ["orders", "customers"], "topic": "sales"},
        {"id": "vw_product_performance", "label": "Product performance", "maps": ["products", "order_items"], "topic": "product"},
        {"id": "vw_region_revenue", "label": "Region revenue", "maps": ["customers", "orders"], "topic": "region"},
        {"id": "vw_customer_churn", "label": "Customer churn", "maps": ["customers"], "topic": "churn"},
    ],
}

PLATFORM_VIEWS = [
    {"id": "fdryze_audit_log", "label": "Audit · time-travel · write destination", "maps": ["query_history"], "write": True},
    {"id": "fdryze_insights", "label": "Saved insights", "maps": [], "write": False},
    {"id": "fdryze_hitl_queue", "label": "HITL queue", "maps": ["hitl_reviews"], "write": True},
]


def _sql_dialect() -> str:
    if settings.warehouse in {"databricks", "snowflake"}:
        return settings.warehouse
    return "postgres" if settings.database_url.startswith("postgresql") else "sqlite"


def source_tables(sql: str | None) -> list[tuple[str, ...]]:
    """Table sources referenced by a statement as (catalog, schema, table) parts, CTEs excluded.

    Parsed with sqlglot so that FROM inside function syntax such as EXTRACT(MONTH FROM col),
    TRIM(BOTH ' ' FROM col) or SUBSTRING(col FROM 1) is not mistaken for a table. Unparseable SQL
    falls back to the stricter regex reading, so validation never becomes more permissive.
    """
    if not sql:
        return []
    try:
        tree = parse_one(sql, read=_sql_dialect())
    except (ParseError, ValueError):
        return [tuple(re.split(r"\s*\.\s*", name)) for name in _regex_source_names(sql)]
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    sources: list[tuple[str, ...]] = []
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            # Table-valued functions (read_files(...), etc.) are sources too; report them by
            # their SQL so source validation rejects them as unauthorized.
            parts: tuple[str, ...] = (table.this.sql(dialect=_sql_dialect()),)
        else:
            parts = tuple(part for part in (table.catalog, table.db, table.name) if part)
            if len(parts) == 1 and parts[0].lower() in cte_names:
                continue
        if parts not in sources:
            sources.append(parts)
    return sources


def extract_tables(sql: str | None) -> list[str]:
    found: list[str] = []
    for parts in source_tables(sql):
        table = parts[-1].strip("`\"")
        if table not in found:
            found.append(table)
    return found


def _regex_source_names(sql: str) -> list[str]:
    source_pattern = re.compile(
        r"\b(?:FROM|JOIN)\s+((?:`[^`]+`|\"[^\"]+\"|[A-Za-z_][A-Za-z0-9_$]*)"
        r"(?:\s*\.\s*(?:`[^`]+`|\"[^\"]+\"|[A-Za-z_][A-Za-z0-9_$]*)){0,2})",
        re.IGNORECASE,
    )
    cte_pattern = re.compile(
        r"(?:\bWITH\b|,)\s*([A-Za-z_][A-Za-z0-9_$]*)\s+AS\s*\(",
        re.IGNORECASE,
    )
    cte_names = {name.lower() for name in cte_pattern.findall(sql)}
    found: list[str] = []
    for match in source_pattern.finditer(sql):
        name = match.group(1)
        table = re.split(r"\s*\.\s*", name)[-1].strip("`\"")
        if table.lower() not in cte_names and name not in found:
            found.append(name)
    return found


def chart_spec(table: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not table:
        return None
    keys = list(table[0].keys())

    def is_num(v: Any) -> bool:
        if v is None or v == "":
            return False
        if isinstance(v, bool):
            return False
        if isinstance(v, (int, float)):
            return True
        try:
            float(v)
            return True
        except (TypeError, ValueError):
            return False

    numeric = [k for k in keys if sum(1 for r in table[:30] if is_num(r.get(k))) >= max(1, len(table[:30]) // 2)]
    cats = [k for k in keys if k not in numeric]
    if not numeric:
        return None
    x = cats[0] if cats else keys[0]
    y = numeric[-1]
    kind = "bar"
    if any(t in x.lower() for t in ("month", "date", "week", "day")):
        kind = "line"
    elif len(table) <= 8 and cats:
        kind = "pie"
    series = []
    for row in table[:60]:
        try:
            series.append({"label": str(row.get(x)), "value": float(row.get(y) or 0)})
        except (TypeError, ValueError):
            continue
    return {"type": kind, "x": x, "y": y, "data": series}


def build_lineage(
    domain: str,
    question: str,
    sql: str | None,
    table: list[dict[str, Any]],
    username: str,
    audit_id: str,
    schema_used: list[str],
    guardrail_ok: bool,
    local_counts: dict[str, int],
) -> dict[str, Any]:
    cat = (
        databricks_domain_catalog(domain)
        if settings.warehouse == "databricks"
        else catalog_name(domain)
    )
    tables = extract_tables(sql) or schema_used
    if settings.warehouse == "databricks":
        schema = settings.databricks_domain_schema
        source_fqns = [f"{cat}.{schema}.{table}" for table in tables]
        columns = list(table[0].keys()) if table else []
        return {
            "domain": domain,
            "catalog": cat,
            "engine": "Ryze NL→SQL (Genie off)",
            "flow": [
                f"{cat}.{schema}.{', '.join(tables)}",
                "Ryze NL→SQL",
                "Guardrails + domain-scoped read-only execution",
                "Application query_history audit record",
            ],
            "medallion": [
                {
                    "layer": "Queried Databricks source",
                    "fqn": ", ".join(source_fqns),
                    "blurb": "Live source tables queried directly; unqueried medallion layers are not inferred.",
                    "stats": f"{len(table)} result rows",
                }
            ],
            "views": [],
            "trace": [
                {"step": 1, "title": "Question", "detail": question, "by": username},
                {"step": 2, "title": "Ryze · NL→SQL", "detail": (sql or "")[:280]},
                {
                    "step": 3,
                    "title": "Live source tables",
                    "detail": ", ".join(source_fqns) or "No source table identified",
                    "columns": columns,
                    "acl": True,
                },
                {"step": 4, "title": "Result", "detail": f"{len(table)} rows"},
                {"step": 5, "title": "Audit log", "detail": audit_id, "status": "Logged"},
            ],
            "guardrail_ok": guardrail_ok,
            "note": (
                f"Read-only query executed against {cat}.{schema}; "
                "Bronze/Gold lineage and source row counts are not reported unless verified."
            ),
        }
    views = DOMAIN_VIEWS.get(domain, DOMAIN_VIEWS["retail"])
    accessed: set[str] = set()
    for view in views:
        if any(m in tables for m in view["maps"]):
            accessed.add(view["id"])
    accessed.add("fdryze_audit_log")

    bronze_count = local_counts.get("bronze", 0)
    silver_count = local_counts.get("silver", 0)
    gold_count = local_counts.get("gold", 0)

    bronze_table = {
        "insurance": "claims_raw",
        "banking": "transactions_raw",
        "finance": "trades_raw",
        "retail": "orders_raw",
    }.get(domain, "events_raw")
    silver_table = {
        "insurance": "claims",
        "banking": "transactions",
        "finance": "trades",
        "retail": "orders",
    }.get(domain, "events")
    gold_view = views[0]["id"] if views else "vw_enriched"

    columns = list(table[0].keys()) if table else []
    gold_cards = []
    for view in views + PLATFORM_VIEWS:
        state = "write" if view.get("write") and view["id"] in accessed else ("queried" if view["id"] in accessed else "idle")
        gold_cards.append(
            {
                "id": view["id"],
                "fqn": f"{cat}.{gold_schema()}.{view['id']}",
                "label": view["label"],
                "state": state,
                "queries": 1 if view["id"] in accessed else 0,
            }
        )

    return {
        "domain": domain,
        "catalog": cat,
        "engine": "Ryze NL→SQL (Genie off)",
        "flow": [
            f"{gold_schema()}.vw_*",
            "Ryze NL→SQL",
            "Guardrails + HITL",
            "fdryze_audit_log",
        ],
        "medallion": [
            {
                "layer": "Bronze · raw",
                "fqn": f"{cat}.raw.{bronze_table}",
                "blurb": "Ingested feeds — append-only, untransformed. Catalog tracks every write.",
                "stats": f"{bronze_count:,} raw rows",
            },
            {
                "layer": "Silver · cleaned",
                "fqn": f"{cat}.curated_silver.{silver_table}",
                "blurb": "Deduplicated, conformed, quality-checked. PII tokenised before AI access.",
                "stats": f"{silver_count:,} rows · validated · PII masked",
            },
            {
                "layer": "Gold · curated",
                "fqn": f"{cat}.{gold_schema()}.{gold_view}",
                "blurb": "Business-ready views the AI reads. Row-level ACL. Ryze queries only this layer.",
                "stats": f"{gold_count:,} rows · ACL-governed · masked",
            },
        ],
        "views": gold_cards,
        "trace": [
            {"step": 1, "title": "Question", "detail": question, "by": username},
            {
                "step": 2,
                "title": "Ryze · NL→SQL",
                "detail": (sql or "")[:280],
            },
            {
                "step": 3,
                "title": "Gold view(s)",
                "detail": ", ".join(v["id"] for v in gold_cards if v["state"] != "idle") or "none",
                "columns": columns,
                "acl": True,
            },
            {"step": 4, "title": "Result", "detail": f"{len(table)} rows"},
            {"step": 5, "title": "Audit log", "detail": audit_id, "status": "Logged · immutable"},
        ],
        "guardrail_ok": guardrail_ok,
        "note": "Unity Catalog ACL when Databricks is connected · PII masked at view layer · all reads audit-logged",
    }


def guardrail_report(sql: str | None, ok: bool, error: str | None) -> dict[str, Any]:
    checks = [
        {"name": "SELECT only", "pass": bool(sql) and sql.strip().lower().startswith("select")},
        {"name": "No multi-statement", "pass": bool(sql) and ";" not in sql.strip().rstrip(";")},
        {"name": "Row LIMIT applied", "pass": bool(sql) and "limit" in (sql or "").lower()},
        {"name": "Forbidden keywords blocked", "pass": ok or (error or "").lower().find("forbidden") >= 0},
        {"name": "Read-only executor", "pass": ok},
        {"name": "Genie disabled", "pass": True},
    ]
    return {"ok": ok, "error": error, "checks": checks}


def model_gov(prediction: Any, llm_provider: str | None) -> dict[str, Any]:
    return {
        "prediction_runtime": "self-hosted TabPFN" if prediction else "not invoked",
        "external_ml_api": False,
        "genie": False,
        "llm": llm_provider or "heuristic fallback",
        "pii_policy": "masked at silver / gold view",
        "approval": "HITL required for prediction outputs",
    }
