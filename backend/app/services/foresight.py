"""Foresight: the governed business context of one domain, assembled from existing sources only.

Nothing here defines a metric or a formula. Metrics are read from the domain metric registries
(banking_registry.py, domain_registries.py) and from services/metrics.py; entities come from the
registry's approved tables and the metadata snapshot built at warm-up; Banking's glossary, rules,
capabilities and dictionary come from banking_context.py. Relationships and data checks for the
other four domains were verified against Databricks gold with read-only queries on VERIFIED_ON and
are recorded here with their counts. No Databricks query runs when Foresight is opened.
"""

import re
from typing import Any

from ..config import settings
from ..domains import DOMAIN_LABELS
from ..sensitive_data import is_sensitive_column
from . import banking_context
from .banking_registry import BANKING, DomainRegistry
from .domain_registries import REGISTRIES
from .metrics import METRICS as SECONDARY_METRICS

VERIFIED_ON = "2026-11-12"
_TECHNICAL = (
    (re.compile(r"^gold_(?:metadata|load_summary)$"), "pipeline"),
    (re.compile(r"^gold_(?:column_profile|table_profile|data_quality)$"), "profiling"),
    (re.compile(r"^snapshot_"), "point-in-time copy"),
    (re.compile(r"^summary_"), "pre-aggregated summary"),
    (re.compile(r"^agg_"), "pre-aggregated"),
    (re.compile(r"^(?:dim|fact)_"), "modelled copy"),
)


def registry_for(domain: str) -> DomainRegistry | None:
    return BANKING if domain == "banking" else REGISTRIES.get(domain)


# Verified 2026-11-12: parent key unique among non-null values; "unmatched" counts child rows whose
# key has no parent. "configured" joins are used by the domain's metric registry today.
_RELATIONSHIPS: dict[str, tuple[tuple[str, str, str, str, str, int, bool, str], ...]] = {
    "finance": (
        ("gold_accounts", "client_id", "gold_clients", "client_id", "many-to-one", 20, True, "Each account belongs to one client."),
        ("gold_trades", "account_id", "gold_accounts", "account_id", "many-to-one", 20, False, "Each trade is booked to one account."),
        ("gold_trades", "security_id", "gold_securities", "security_id", "many-to-one", 30, True, "Each trade is in one security."),
    ),
    "insurance": (
        ("gold_policies", "holder_id", "gold_policyholders", "holder_id", "many-to-one", 20, True, "Each policy has one policyholder."),
        ("gold_policies", "agent_id", "gold_agents", "agent_id", "many-to-one", 0, True, "Each policy was sold by one agent."),
        ("gold_claims", "policy_id", "gold_policies", "policy_id", "many-to-one", 21, True, "Each claim is made on one policy."),
        ("gold_premium_payments", "policy_id", "gold_policies", "policy_id", "many-to-one", 0, True, "Each premium payment is for one policy."),
    ),
    "retail": (
        ("gold_order_items", "order_id", "gold_orders", "order_id", "many-to-one", 0, True, "Each order line belongs to one order."),
        ("gold_order_items", "product_id", "gold_products", "product_id", "many-to-one", 30, True, "Each order line is for one product."),
        ("gold_orders", "store_id", "gold_stores", "store_id", "many-to-one", 0, True, "Each order was placed at one store."),
        ("gold_orders", "customer_id", "gold_customers", "customer_id", "many-to-one", 56, True, "Each order was placed by one customer; 8 customer records have no ID."),
        ("gold_returns", "order_item_id", "gold_order_items", "order_item_id", "many-to-one", 0, True, "Each return is for one order line; no order line has more than one return."),
    ),
    "healthcare": (
        ("gold_prescriptions", "encounter_id", "gold_encounters", "encounter_id", "many-to-one", 0, True, "Each prescription was written in one encounter."),
        ("gold_claims", "encounter_id", "gold_encounters", "encounter_id", "one-to-zero-or-one", 0, True, "Each claim is for one encounter; no encounter has more than one claim."),
        ("gold_encounters", "facility_id", "gold_facilities", "facility_id", "many-to-one", 0, True, "Each encounter took place at one facility."),
        ("gold_encounters", "patient_id", "gold_patients", "patient_id", "many-to-one", 56, False, "Each encounter is for one patient; 10 patient records have no ID."),
        ("gold_claims", "patient_id", "gold_patients", "patient_id", "many-to-one", 19, False, "Each claim names one patient; 14 claims name a different patient from their encounter."),
    ),
}

# Read-only violation counts measured on VERIFIED_ON. Checks are reported, never enforced, and have
# no approved owner, severity or threshold yet.
_CHECKS: dict[str, tuple[tuple[str, str, str, tuple[str, ...], int], ...]] = {
    "finance": (
        ("finance_trade_price_quantity_positive", "Trade price and quantity are positive", "price > 0 AND quantity > 0", ("gold_trades.price", "gold_trades.quantity"), 70),
        ("finance_cash_balance_not_negative", "Account cash balance is not negative", "cash_balance >= 0", ("gold_accounts.cash_balance",), 0),
    ),
    "insurance": (
        ("insurance_approved_within_claimed", "Approved amount does not exceed claimed amount", "approved_amount <= claimed_amount", ("gold_claims.approved_amount", "gold_claims.claimed_amount"), 25),
        ("insurance_rejected_claims_unpaid", "Rejected claims have no approved amount", "claim_status = 'REJECTED' implies approved_amount = 0", ("gold_claims.claim_status", "gold_claims.approved_amount"), 0),
        ("insurance_claim_after_policy_start", "Claims are dated on or after the policy start", "claim_date >= policy start_date", ("gold_claims.claim_date", "gold_policies.start_date"), 0),
        ("insurance_paid_within_due", "Premium paid does not exceed premium due", "amount_paid <= amount_due", ("gold_premium_payments.amount_paid", "gold_premium_payments.amount_due"), 0),
    ),
    "retail": (
        ("retail_refund_within_item_value", "Refund does not exceed the order line value", "refund_amount <= quantity * unit_price", ("gold_returns.refund_amount", "gold_order_items.quantity", "gold_order_items.unit_price"), 46),
        ("retail_return_after_order", "Returns are dated on or after the order", "return_date >= order_date", ("gold_returns.return_date", "gold_orders.order_date"), 13),
    ),
    "healthcare": (
        ("healthcare_approved_within_billed", "Approved amount does not exceed billed amount", "approved_amount <= billed_amount", ("gold_claims.approved_amount", "gold_claims.billed_amount"), 45),
        ("healthcare_claim_patient_matches_encounter", "A claim's patient matches its encounter's patient", "claims.patient_id = encounters.patient_id", ("gold_claims.patient_id", "gold_encounters.patient_id"), 14),
        ("healthcare_one_claim_per_encounter", "At most one claim per encounter", "COUNT(claims) per encounter_id <= 1", ("gold_claims.encounter_id",), 0),
        ("healthcare_rejected_claims_unpaid", "Rejected claims have no approved amount", "claim_status = 'REJECTED' implies approved_amount = 0", ("gold_claims.claim_status", "gold_claims.approved_amount"), 0),
    ),
}

# Controls in code that apply to every domain, so the Rules view can say exactly what is enforced.
_PLATFORM_RULES = (
    ("platform_domain_authorization", "Only authorized domains are visible", "enforced",
     "Every request re-reads the user's account and domain grants; other domains are refused.", "app/auth.py get_current_user; app/services/access.py domains_for"),
    ("platform_read_only_sql", "Analytics SQL is read-only and bounded", "enforced",
     "Every statement is parsed: one SELECT, no writes or dangerous functions, a row limit.", "app/services/guardrail.py validate_sql"),
    ("platform_sensitive_fields", "Sensitive fields are protected", "enforced",
     "Personal and restricted identifiers are refused or redacted for business users; card security codes are never returned.", "app/sensitive_data.py"),
    ("platform_registry_first", "Approved metrics answer first", "enforced",
     "Questions an approved metric can represent are answered from the registry's compiled SQL.", "app/services/orchestrator.py _registry_for"),
    ("platform_model_sql_allowlist", "Model-written SQL table and column allowlist", "shadow",
     "Parsed allowlists exist for all five domains but run in shadow mode: they log, and refuse nothing, until reviewed.", "app/services/domain_sql_policy.py"),
)


def _label(table: str) -> str:
    return re.sub(r"^(?:gold|agg)_", "", table).replace("_", " ").capitalize()


def _technical_kind(table: str) -> str | None:
    return next((kind for pattern, kind in _TECHNICAL if pattern.search(table)), None)


def relationships(domain: str) -> list[dict[str, Any]]:
    if domain == "banking":
        rows = [
            (r.child, r.child_column, r.parent, r.parent_column, r.cardinality, r.unmatched_children, r.used_in_registry, r.note)
            for r in banking_context.RELATIONSHIPS
        ]
        verified_on = banking_context.VERIFIED_ON
    else:
        rows, verified_on = list(_RELATIONSHIPS.get(domain, ())), VERIFIED_ON
    return [
        {
            "child": child, "child_column": child_column, "parent": parent, "parent_column": parent_column,
            "child_label": _label(child), "parent_label": _label(parent),
            "join": f"{child}.{child_column} = {parent}.{parent_column}",
            "cardinality": cardinality, "unmatched_children": unmatched,
            "status": "configured" if configured else "verified",
            "evidence": "Verified against the data (unique parent key, unmatched rows counted); Unity Catalog declares no keys.",
            "explanation": note, "verified_on": verified_on,
        }
        for child, child_column, parent, parent_column, cardinality, unmatched, configured, note in rows
    ]


def _metric_views(domain: str, registry: DomainRegistry) -> list[dict[str, Any]]:
    views = []
    for metric in registry.metrics:
        if domain == "banking":
            view = banking_context.metric_view(metric)
        else:
            catalog, schema = registry.catalog_schema.split(".")
            view = {
                "id": metric.id, "name": metric.label, "description": metric.definition,
                "formula": "; ".join(f"{m.label} = {m.expression}" for m in metric.measures),
                "aggregation": metric.aggregation, "source_catalog": catalog, "source_schema": schema,
                "source_tables": [t for t in metric.source.tables if t in registry.approved_tables],
                "source_columns": list(metric.required_columns), "date_semantics": metric.time_semantics,
                "supports_time": metric.supports_time, "business_owner": None, "pending_rules": [],
                "data_quality": list(metric.data_quality), "version": metric.version,
                "validation_note": f"Approved in the {domain} registry {registry.version}; no business owner sign-off is recorded.",
            }
        views.append({
            **view,
            "status": "registry",
            "units": sorted({m.unit for m in metric.measures}),
            "dimensions": [{"id": d.id, "label": d.label} for d in metric.source.dimensions],
            "used_by": ["Ask AI", "MCP"],
            "approval": "Owner sign-off not recorded",
        })
    registry_ids = {metric.id for metric in registry.metrics}
    for item in SECONDARY_METRICS:
        if item.domain != domain:
            continue
        views.append({
            "id": item.key, "name": item.label, "description": item.formula, "formula": f"{item.value_expression} FROM {item.from_clause}"
            + (f" WHERE {item.where_clause}" if item.where_clause else ""),
            "aggregation": item.value_expression, "source_tables": list(item.sources), "source_columns": [],
            "date_semantics": item.date_expression, "supports_time": item.dated, "units": [item.unit], "dimensions": [],
            "status": "secondary", "used_by": ["Ask AI metric route", "MCP business_metric"], "business_owner": None,
            "approval": "Owner sign-off not recorded", "pending_rules": [], "data_quality": [], "version": None,
            "validation_note": (
                "Defined in services/metrics.py, used when the registry does not answer. Not yet reconciled with the registry"
                + (f": the registry also defines '{item.key}' with its own formula." if item.key in registry_ids else ".")
            ),
        })
    return views


def _rules(domain: str, registry: DomainRegistry) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = [
        {"id": rule_id, "name": name, "kind": kind, "status": kind, "description": text, "implementation": where, "domain": "all"}
        for rule_id, name, kind, text, where in _PLATFORM_RULES
    ]
    if domain == "healthcare":
        rules.append({
            "id": "healthcare_tenant_isolation", "name": "Healthcare tenant isolation", "kind": "enforced", "status": "enforced",
            "description": "Healthcare queries run only in the catalog an administrator mapped to the user's tenant; users without a tenant are refused.",
            "implementation": "app/warehouse.py execute_healthcare_tenant_readonly; app/main.py /context", "domain": "healthcare",
        })
    if domain == "banking":
        for rule in banking_context.RULES:
            rules.append({
                "id": rule.id, "name": rule.statement, "kind": "definition" if rule.status == banking_context.VERIFIED else "pending",
                "status": rule.status, "description": rule.evidence, "while_pending": rule.while_pending or None,
                "applies_to": list(rule.applies_to), "domain": "banking",
            })
    for pattern, reason in registry.unsupported_topics:
        rules.append({
            "id": f"{domain}_refusal_{len(rules)}", "name": reason.split(":")[0].rstrip("."), "kind": "enforced", "status": "enforced",
            "description": reason, "implementation": f"{'banking_registry' if domain == 'banking' else 'domain_registries'}.py unsupported topics",
            "domain": domain,
        })
    seen: set[str] = set()
    for metric in registry.metrics:
        for note in metric.data_quality:
            if note not in seen:
                seen.add(note)
                rules.append({
                    "id": f"{domain}_data_note_{len(seen)}", "name": note, "kind": "data note", "status": "documented",
                    "description": "A known limitation of the source data, attached to the evidence of affected answers.",
                    "applies_to": [m.id for m in registry.metrics if note in m.data_quality], "domain": domain,
                })
    for rule_id, name, expression, columns, violations in _CHECKS.get(domain, ()):
        rules.append({
            "id": rule_id, "name": name, "kind": "check", "status": "PENDING_BUSINESS_APPROVAL", "expression": expression,
            "columns": list(columns), "violations": violations, "measured_on": VERIFIED_ON, "domain": domain,
            "description": "Data-quality check measured with a read-only query. Reported only; not enforced. Owner, severity and threshold need approval.",
        })
    return rules


def _entities(domain: str, registry: DomainRegistry, live: dict[str, list[dict[str, str]]], rels: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    dictionary = {t.name: t for t in banking_context.DICTIONARY} if domain == "banking" else {}
    primary_keys = {(r["parent"], r["parent_column"]) for r in rels}
    foreign_keys = {(r["child"], r["child_column"]): f"{r['parent']}.{r['parent_column']}" for r in rels}
    entities = []
    for table in sorted(registry.approved_tables):
        columns = live.get(table, [])
        documented = dictionary.get(table)
        meanings = {c.name: c for c in documented.columns} if documented else {}
        visible = []
        for column in columns:
            name = column["name"]
            if is_sensitive_column(name):
                continue
            key = "PK" if (table, name) in primary_keys or (meanings.get(name) and meanings[name].key == "PK") else (
                f"FK {foreign_keys[(table, name)]}" if (table, name) in foreign_keys else None
            )
            visible.append({
                "name": name, "type": column.get("type"), "key": key,
                "meaning": meanings[name].meaning if name in meanings else column.get("description"),
                "technical": name.startswith(("_gold", "ingestion", "source_file")),
            })
        entities.append({
            "table": table, "name": _label(table), "kind": "aggregate" if table.startswith("agg_") else "entity",
            "source": f"{registry.catalog_schema}.{table}",
            "description": documented.grain if documented else None,
            "notes": list(documented.notes) if documented else [],
            "rows": documented.rows if documented else None,
            "coverage": documented.coverage if documented else None,
            "columns": visible, "hidden_sensitive_columns": len(columns) - len(visible),
            "metadata_available": bool(columns),
            "metrics": [m.id for m in registry.metrics if table in m.source.tables],
            "relationships": [r["join"] for r in rels if table in (r["child"], r["parent"])],
            "validation": "Dictionary reviewed against Unity Catalog" if documented else "Columns from Unity Catalog; business meanings not yet documented",
        })
    technical = [
        {"table": table, "kind": _technical_kind(table) or "other gold table"}
        for table in sorted(live) if table not in registry.approved_tables
    ]
    return entities, technical


def build(domain: str, graph: Any, catalog: str | None, synchronized_at: str | None) -> dict[str, Any]:
    """Everything the Foresight views need for one authorized domain."""
    registry = registry_for(domain)
    if registry is None:
        return {"domain": domain, "available": False}
    live = graph.domain_tables.get(domain, {}) if settings.warehouse == "databricks" else {}
    rels = relationships(domain)
    entities, technical = _entities(domain, registry, live, rels)
    metrics = _metric_views(domain, registry)
    rules = _rules(domain, registry)
    glossary = [
        {"term": t.term, "definition": t.definition, "status": t.status,
         "maps_to": f"{t.maps_to[0]} {t.maps_to[1]}" if t.maps_to else None,
         "source": f"{t.table}.{t.column}" if t.table and t.column else None}
        for t in banking_context.GLOSSARY
    ] if domain == "banking" else None
    capabilities = [
        {"id": c.id, "name": c.name, "status": c.status, "metrics": list(c.metrics), "pending_rules": list(c.pending_rules),
         "unavailable": [{"topic": topic, "reason": reason} for topic, reason in c.unavailable], "note": c.note}
        for c in banking_context.CAPABILITIES
    ] if domain == "banking" else None

    def count(items: list[dict[str, Any]], key: str) -> dict[str, int]:
        result: dict[str, int] = {}
        for item in items:
            result[item[key]] = result.get(item[key], 0) + 1
        return result

    return {
        "domain": domain,
        "domain_label": DOMAIN_LABELS.get(domain, domain),
        "available": True,
        "registry_version": registry.version,
        "overview": {
            "analytical_source": f"{catalog}.{settings.databricks_domain_schema}" if catalog else registry.catalog_schema,
            "registry_source": registry.catalog_schema,
            "application_source": "PostgreSQL (users, permissions, history, audit)",
            "synchronized_at": synchronized_at,
            "metadata_available": bool(live),
            "entities": sum(1 for e in entities if e["kind"] == "entity"),
            "aggregates": sum(1 for e in entities if e["kind"] == "aggregate"),
            "technical_tables": len(technical),
            "relationships": count(rels, "status"),
            "metrics": count(metrics, "status"),
            "rules": count(rules, "kind"),
            "documented_entities": sum(1 for e in entities if e["description"]),
            "verified_on": VERIFIED_ON if domain != "banking" else banking_context.VERIFIED_ON,
        },
        "entities": entities,
        "technical_tables": technical,
        "relationships": rels,
        "metrics": metrics,
        "rules": rules,
        "glossary": glossary,
        "capabilities": capabilities,
    }
