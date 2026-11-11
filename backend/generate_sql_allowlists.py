"""Generate draft SQL allowlists from Unity Catalog names, for review before they are enforced.

Reads table and column names through information_schema (read-only) for each domain's configured
catalog and schema, and writes app/sql_allowlists/<domain>.json. Changes nothing in Databricks.

    python generate_sql_allowlists.py            # all five domains
    python generate_sql_allowlists.py retail     # one domain

Rules, matching the existing sensitive-data policy in app/sensitive_data.py:
- metadata and profiling tables are excluded;
- Banking keeps to the tables its metric registry already approves;
- CVV and credential columns are excluded for everyone;
- restricted identifiers and personal fields may be referenced by administrators only;
- record-level linking keys stay allowed, because the sensitive-data check already limits them to
  counts, totals and joins for business users.
Review each file, remove what questions should not use, and record the reviewer in "reviewed_by".
"""

import json
import sys
from datetime import date

from app.services.banking_registry import APPROVED_TABLES as BANKING_APPROVED_TABLES
from app.services.domain_sql_policy import ALLOWLIST_DIR, DOMAINS
from app.sensitive_data import _ALWAYS_REDACTED_COLUMN, _CREDENTIAL_COLUMN, _CVV_COLUMN, _RESTRICTED_COLUMN
from app.warehouse import databricks_domain_catalog, warehouse_schema_columns

METADATA_TABLES = {
    "gold_metadata": "pipeline metadata, not business data",
    "gold_column_profile": "data profiling output",
    "gold_table_profile": "data profiling output",
    "gold_data_quality": "data-quality results",
    "gold_load_summary": "load bookkeeping",
}


def build(domain: str) -> dict:
    live = warehouse_schema_columns(domain)
    tables: dict[str, dict[str, list[str]]] = {}
    excluded: dict[str, str] = {}
    for table, columns in sorted(live.items()):
        name = table.lower()
        if name in METADATA_TABLES:
            excluded[name] = METADATA_TABLES[name]
            continue
        if domain == "banking" and name not in BANKING_APPROVED_TABLES:
            excluded[name] = "not among the banking metric registry's approved tables"
            continue
        allowed, restricted, never = [], [], []
        for column in columns:
            column_name = str(column["name"]).lower()
            if _CVV_COLUMN.search(column_name) or _CREDENTIAL_COLUMN.search(column_name):
                never.append(column_name)
            elif _RESTRICTED_COLUMN.search(column_name) or _ALWAYS_REDACTED_COLUMN.search(column_name):
                restricted.append(column_name)
            else:
                allowed.append(column_name)
        tables[name] = {"columns": allowed, "restricted": restricted}
        if never:
            tables[name]["never"] = never
    return {
        "domain": domain,
        "source": f"{databricks_domain_catalog(domain)} catalog, information_schema, {date.today().isoformat()}",
        "status": "draft: generated, awaiting review",
        "reviewed_by": None,
        "tables": tables,
        "excluded_tables": excluded,
    }


def main(domains: list[str]) -> None:
    ALLOWLIST_DIR.mkdir(exist_ok=True)
    for domain in domains:
        allowlist = build(domain)
        path = ALLOWLIST_DIR / f"{domain}.json"
        path.write_text(json.dumps(allowlist, indent=2) + "\n", encoding="utf-8")
        restricted = sum(len(entry["restricted"]) for entry in allowlist["tables"].values())
        print(f"{domain}: {len(allowlist['tables'])} tables, {restricted} admin-only columns, {len(allowlist['excluded_tables'])} tables excluded -> {path.name}")


if __name__ == "__main__":
    requested = sys.argv[1:] or list(DOMAINS)
    unknown = [domain for domain in requested if domain not in DOMAINS]
    if unknown:
        raise SystemExit(f"Unknown domain(s): {', '.join(unknown)}")
    main(requested)
