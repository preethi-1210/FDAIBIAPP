"""What an Ask AI answer shows, and to whom.

Every response gets a `presentation` block for the business-friendly answer card: the metric's
display name, applied filters, period, freshness and whether the answer's own validation steps
passed. It is computed from the response itself (its trace and evidence), so it never claims more
than the workflow recorded.

Business users then receive a view without the technical record: SQL, physical catalog, schema and
table names, execution trace, routing, business-rule identifiers, lineage and provider details are
removed on the server, not hidden in the browser. Administrators receive the full response. The
stored history keeps everything for audit.
"""

import re
from datetime import date
from typing import Any

from ..domains import DOMAIN_LABELS
from ..schemas import QueryResponse

STALE_AFTER_DAYS = 31
_VALIDATION_STAGES = ("sql_validation", "result_validation", "grounding_check")
_TABLE_PREFIX = re.compile(r"^(?:gold|silver|bronze|agg|fact|dim|vw|v)_", re.IGNORECASE)
# catalog.schema.table, written plainly or with backticks or quotes.
_THREE_PART = re.compile(r"[`\"]?\b[A-Za-z_][\w-]*[`\"]?\.[`\"]?[A-Za-z_]\w*[`\"]?\.[`\"]?[A-Za-z_]\w*\b[`\"]?")
_CATALOG_SCHEMA = re.compile(r"\b[A-Za-z_][\w-]*\.(?:gold|silver|bronze)\b", re.IGNORECASE)


def dataset_label(source: str, domain: str) -> str:
    """'banking.gold.gold_accounts' -> 'Banking accounts data'. Never returns a physical name."""
    table = source.replace("`", "").replace('"', "").split(".")[-1]
    words = _TABLE_PREFIX.sub("", table).replace("_", " ").strip().lower() or "business"
    return f"{DOMAIN_LABELS.get(domain, domain.title())} {words} data"


def _source_names(response: QueryResponse) -> list[str]:
    names = [source for source in (response.evidence or {}).get("sources", []) if isinstance(source, str)]
    return names + [table for table in response.schema_used or [] if table not in names]


def scrub_text(text: str | None, domain: str, sources: list[str] | None = None) -> str | None:
    """Replace physical catalog, schema and table names in user-facing text with business names."""
    if not text:
        return text
    for source in sorted(sources or [], key=len, reverse=True):
        if source:
            text = re.sub(rf"(?<![\w.]){re.escape(source)}(?![\w])", dataset_label(source, domain), text)
    text = _THREE_PART.sub(lambda match: dataset_label(match.group(0), domain), text)
    text = _CATALOG_SCHEMA.sub(f"approved {DOMAIN_LABELS.get(domain, domain)} data", text)
    return text


def _last(trace: list[dict[str, Any]], stage: str) -> dict[str, Any] | None:
    return next((step for step in reversed(trace) if step.get("stage") == stage), None)


def verification(response: QueryResponse, metric_label: str | None, domain_label: str, filters: list[dict[str, Any]]) -> dict[str, Any]:
    """'passed' only when data was read and every validation stage that ran finally succeeded."""
    trace = response.trace or []
    ran = {stage: _last(trace, stage) for stage in _VALIDATION_STAGES if _last(trace, stage)}
    executed = _last(trace, "sql_execution")
    if response.status == "error" or any(not step.get("ok") for step in ran.values()) or (executed and not executed.get("ok")):
        return {"status": "failed", "summary": "This answer did not pass its validation checks.", "checks": []}
    if response.status not in {"complete", "no_data"} or not executed or not ran:
        reason = "No business data was queried for this answer." if not executed else "The checks for this answer did not run."
        return {"status": "not_verified", "summary": reason, "checks": []}
    checks = []
    if metric_label:
        checks.append(f"Used the approved definition of {metric_label.lower()}")
    for item in filters:
        checks.append(f"Applied filter: {item['label']} = {', '.join(item['values'])}")
    if "sql_validation" in ran:
        checks.append(f"Read-only query limited to approved {domain_label} data and your permissions")
    if "result_validation" in ran:
        checks.append("Results checked for completeness, valid ranges and reconciliation")
    if "grounding_check" in ran:
        checks.append("Every figure in the answer matches the data that was read")
    return {
        "status": "passed",
        "summary": "This answer was checked against approved business definitions and the available data.",
        "checks": checks,
    }


def _freshness(evidence: dict[str, Any], today: date) -> dict[str, Any]:
    coverage = evidence.get("coverage") or {}
    latest = coverage.get("last_date")
    if not latest:
        return {"available": False, "note": evidence.get("time_semantics")}
    try:
        age = (today - date.fromisoformat(str(latest)[:10])).days
    except ValueError:
        return {"available": False, "note": evidence.get("time_semantics")}
    return {
        "available": True,
        "latest_date": str(latest)[:10],
        "first_date": str(coverage.get("first_date") or "")[:10] or None,
        "age_days": age,
        "stale": age > STALE_AFTER_DAYS,
        # Dated after today: a data-quality issue in the source, reported rather than hidden.
        "future": age < 0,
        "note": evidence.get("time_semantics"),
    }


def presentation(response: QueryResponse, technical: bool, today: date | None = None) -> dict[str, Any]:
    domain = response.domain or ""
    domain_label = DOMAIN_LABELS.get(domain, domain.title())
    evidence = response.evidence or {}
    labels = evidence.get("filter_labels") or {}
    filters = [
        {"label": labels.get(key, key.replace("_", " ")), "values": [str(v) for v in (values if isinstance(values, list) else [values])]}
        for key, values in (evidence.get("filters") or {}).items()
    ]
    metric_label = evidence.get("metric_label")
    sources = _source_names(response)
    answer = scrub_text(response.answer, domain, sources) or ""
    paragraphs = [part.strip() for part in answer.split("\n\n") if part.strip()]
    return {
        "domain_label": domain_label,
        "metric_label": metric_label,
        "definition": evidence.get("definition"),
        "filters": filters,
        "period": evidence.get("period"),
        "datasets": sorted({dataset_label(source, domain) for source in sources}),
        "freshness": _freshness(evidence, today or date.today()),
        "verification": verification(response, metric_label, domain_label, filters),
        "summary": paragraphs[0] if paragraphs else "",
        "details": paragraphs[1:],
        "technical_details": technical,
    }


_BUSINESS_EVIDENCE = (
    "kind", "metric", "metric_label", "definition", "time_semantics", "period", "coverage", "filters",
    "filter_labels", "dimensions", "interpreted_as", "data_quality", "reconciliation", "row_count",
    "observed_rows_are_actual_data", "history",
)
_GUARDRAIL_NAMES = {
    "SELECT only": "Read-only access",
    "No multi-statement": "Single, contained request",
    "Row LIMIT applied": "Bounded result size",
    "Forbidden keywords blocked": "Data-changing operations blocked",
    "Read-only executor": "Read-only connection",
}


def for_viewer(response: QueryResponse, role: str, today: date | None = None) -> QueryResponse:
    """The response a viewer may receive. Administrators get everything; others the business view."""
    technical = role == "admin"
    view = response.model_copy(deep=True)
    view.presentation = presentation(response, technical, today)
    if technical:
        return view
    domain = response.domain or ""
    sources = _source_names(response)
    evidence = response.evidence or {}
    view.answer = scrub_text(response.answer, domain, sources) or ""
    view.error = scrub_text(response.error, domain, sources)
    view.sql = None
    view.schema_used = []
    view.lineage = None
    view.trace = []
    view.llm_provider = None
    view.plan = {"subtasks": response.plan.get("subtasks", []), "is_prediction": bool(response.plan.get("is_prediction"))}
    business_evidence = {key: evidence[key] for key in _BUSINESS_EVIDENCE if key in evidence}
    if evidence.get("sources"):
        business_evidence["sources"] = [
            dataset_label(source, domain) if isinstance(source, str) else source for source in evidence["sources"]
        ]
    view.evidence = business_evidence
    if response.guardrails:
        view.guardrails = {
            "ok": response.guardrails.get("ok"),
            "error": None if response.guardrails.get("ok") else "The request did not pass the data-access checks.",
            "checks": [
                {"name": _GUARDRAIL_NAMES[check["name"]], "pass": check["pass"]}
                for check in response.guardrails.get("checks", [])
                if check.get("name") in _GUARDRAIL_NAMES
            ],
        }
    if response.model_gov:
        # The governance facts name stages and counts, never providers or models, so they are kept.
        gov = dict(response.model_gov)
        gov.pop("llm", None)  # Present only in history saved before governance was derived from the trace.
        gov["pii_policy"] = "Sensitive fields are masked before results are shown"
        view.model_gov = gov
    return view
