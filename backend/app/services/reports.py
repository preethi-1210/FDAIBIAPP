import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from ..config import settings
from ..domains import DOMAIN_LABELS
from ..services.guardrail import GuardrailError
from ..services.tenant_scope import scope_healthcare_sql
from ..warehouse import databricks_domain_catalog, execute_readonly_many

MAX_REPORT_DAYS = 366
# Retail gold order items carry no line total; discount_pct contains values above 100.
RETAIL_NET_SALES = "i.quantity * i.unit_price * (1 - LEAST(GREATEST(i.discount_pct, 0), 100) / 100.0)"


@dataclass(frozen=True)
class ReportSource:
    date_expression: str
    amount_expression: str
    count_expression: str
    from_clause: str
    where_clause: str
    category_expression: str | None
    region_expression: str | None
    amount_key: str
    amount_label: str
    amount_unit: str
    amount_definition: str
    count_key: str
    count_label: str
    count_definition: str
    snapshot: bool = False


def comparison_dates(
    start: date,
    end: date,
    comparison: str,
    custom_start: date | None = None,
    custom_end: date | None = None,
) -> tuple[date, date]:
    _validate_range(start, end)
    if comparison == "custom":
        if custom_start is None or custom_end is None:
            raise ValueError("Custom comparison requires comparison_start and comparison_end.")
        _validate_range(custom_start, custom_end)
        if custom_end >= start:
            raise ValueError("Custom comparison dates must end before the selected period starts.")
        return custom_start, custom_end
    if custom_start is not None or custom_end is not None:
        raise ValueError("comparison_start and comparison_end are only valid with comparison=custom.")

    if comparison == "previous_period":
        duration = (end - start).days + 1
        comparison_end = start - timedelta(days=1)
        return comparison_end - timedelta(days=duration - 1), comparison_end
    if comparison == "previous_month":
        return _shift_month(start, -1), _shift_month(end, -1)
    if comparison == "previous_quarter":
        return _shift_month(start, -3), _shift_month(end, -3)
    if comparison == "previous_year":
        return _shift_year(start, -1), _shift_year(end, -1)
    raise ValueError("comparison must be previous_period, previous_month, previous_quarter, previous_year, or custom.")


def _validate_range(start: date, end: date) -> None:
    if end < start:
        raise ValueError("end_date must be on or after start_date.")
    if (end - start).days + 1 > MAX_REPORT_DAYS:
        raise ValueError(f"Report date ranges may not exceed {MAX_REPORT_DAYS} days.")


def _shift_month(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    return value.replace(year=year, month=month, day=min(value.day, calendar.monthrange(year, month)[1]))


def _shift_year(value: date, years: int) -> date:
    year = value.year + years
    return value.replace(year=year, day=min(value.day, calendar.monthrange(year, value.month)[1]))


def _source(domain: str, tenant_id: str | None) -> ReportSource | None:
    if domain not in DOMAIN_LABELS:
        raise ValueError("Authenticated business domain is not supported.")

    if domain == "healthcare":
        if not tenant_id or not tenant_id.strip():
            raise GuardrailError("Healthcare reports require an administrator-provisioned tenant assignment.")
        return ReportSource(
            date_expression="c.service_date",
            amount_expression="c.claim_amount",
            count_expression="COUNT(*)",
            from_clause=(
                "healthcare_claims c "
                "JOIN healthcare_encounters e "
                "ON e.tenant_id = c.tenant_id AND e.encounter_key = c.encounter_key "
                "JOIN healthcare_facilities f "
                "ON f.tenant_id = e.tenant_id AND f.facility_key = e.facility_key"
            ),
            where_clause="",
            category_expression="c.claim_status",
            region_expression="f.facility_region",
            amount_key="claim_amount",
            amount_label="Claim amount",
            amount_unit="currency",
            amount_definition="Sum of claim_amount for tenant-scoped claims grouped by service_date.",
            count_key="claims",
            count_label="Claims",
            count_definition="Count of tenant-scoped healthcare claims grouped by service_date.",
        )

    if settings.warehouse == "databricks":
        catalog = f"`{databricks_domain_catalog(domain)}`"
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", settings.databricks_domain_schema):
            raise GuardrailError("DATABRICKS_DOMAIN_SCHEMA must be a simple SQL identifier.")
        schema = f"`{settings.databricks_domain_schema}`"
        if domain == "banking":
            transactions = f"{catalog}.{schema}.`gold_transactions`"
            accounts = f"{catalog}.{schema}.`gold_accounts`"
            branches = f"{catalog}.{schema}.`gold_branches`"
            return ReportSource(
                "t.txn_timestamp", "t.amount", "COUNT(*)",
                f"{transactions} t JOIN {accounts} a ON a.account_id = t.account_id "
                f"JOIN {branches} b ON b.branch_id = a.branch_id",
                "", "t.merchant_category", "b.state",
                "transaction_amount", "Transaction amount", "currency",
                "Sum of transaction amount grouped by transaction_date.",
                "transaction_count", "Transactions", "Count of transactions grouped by transaction_date.",
            )
        if domain == "insurance":
            policies = f"{catalog}.{schema}.`gold_policies`"
            holders = f"{catalog}.{schema}.`gold_policyholders`"
            return ReportSource(
                "p.start_date", "p.premium_amount", "COUNT(*)",
                f"{policies} p JOIN {holders} h ON h.holder_id = p.holder_id",
                "upper(p.policy_status) != 'CANCELLED'", "p.policy_type", "h.state",
                "premium_amount", "Premium amount", "currency",
                "Sum of non-cancelled policy premium_amount grouped by policy start_date.",
                "policy_count", "Policies", "Count of non-cancelled policies grouped by policy start_date.",
            )
        if domain == "retail":
            items = f"{catalog}.{schema}.`gold_order_items`"
            orders = f"{catalog}.{schema}.`gold_orders`"
            products = f"{catalog}.{schema}.`gold_products`"
            stores = f"{catalog}.{schema}.`gold_stores`"
            return ReportSource(
                "o.order_date", RETAIL_NET_SALES, "COUNT(DISTINCT o.order_id)",
                f"{items} i JOIN {orders} o ON o.order_id = i.order_id "
                f"JOIN {products} p ON p.product_id = i.product_id "
                f"JOIN {stores} s ON s.store_id = o.store_id",
                # Future-dated source rows (some orders are dated years ahead) are excluded.
                "upper(o.order_status) != 'CANCELLED' AND o.order_date <= current_timestamp()",
                "p.category", "s.region",
                "revenue", "Revenue", "currency",
                "Net item sales (quantity × unit price, less discount capped at 100%) of non-cancelled orders, grouped by order_date.",
                "orders", "Orders", "Count of distinct non-cancelled retail orders grouped by order_date.",
            )
        if domain == "finance":
            # agg_accounts_daily groups accounts by the date they were opened (its rows sum
            # exactly to agg_accounts_monthly), so values are additive and support daily,
            # weekly, monthly and quarterly intervals. It is not a balance snapshot.
            return ReportSource(
                "a._gold_date", "a.total_cash_balance", "SUM(a.record_count)",
                f"{catalog}.{schema}.`agg_accounts_daily` a",
                "", None, None,
                "opened_account_balance", "Balance of accounts opened", "currency",
                "Current balance of the accounts opened in each period, grouped by account open date.",
                "accounts_opened", "Accounts opened", "Number of accounts opened in each period.",
            )
        return None

    if settings.warehouse == "snowflake" and domain != "retail":
        return None

    if domain == "banking":
        return ReportSource(
            "t.transaction_date", "t.amount", "COUNT(*)",
            "transactions t JOIN accounts a ON a.id = t.account_id "
            "JOIN customers c ON c.id = a.customer_id",
            "t.domain = :domain", "t.transaction_type", "c.region",
            "transaction_amount", "Transaction amount", "currency",
            "Sum of transaction amount grouped by transaction_date.",
            "transaction_count", "Transactions", "Count of transactions grouped by transaction_date.",
        )
    if domain == "retail":
        return ReportSource(
            "o.order_date", "oi.amount", "COUNT(DISTINCT o.id)",
            "orders o JOIN order_items oi ON oi.order_id = o.id "
            "JOIN products p ON p.id = oi.product_id JOIN customers c ON c.id = o.customer_id",
            "o.domain = :domain AND oi.domain = :domain AND p.domain = :domain "
            "AND c.domain = :domain AND o.status != 'cancelled'",
            "p.category", "c.region",
            "revenue", "Revenue", "currency",
            "Sum of retail order-item amount grouped by order_date.",
            "orders", "Orders", "Distinct non-cancelled retail orders grouped by order_date.",
        )
    if domain == "insurance":
        return ReportSource(
            "cl.claim_date", "cl.amount", "COUNT(*)",
            "claims cl JOIN customers c ON c.id = cl.customer_id",
            "cl.domain = :domain AND c.domain = :domain",
            "cl.status", "c.region",
            "claim_amount", "Claim amount", "currency",
            "Sum of insurance claim amount grouped by the verified claim_date.",
            "claims", "Claims", "Count of insurance claims grouped by the verified claim_date.",
        )
    return None


def _build_query(
    source: ReportSource,
    start: date,
    end: date,
    interval: str,
    category: str | None,
    region: str | None,
    detail: bool,
) -> tuple[str, dict[str, Any]]:
    params: dict[str, Any] = {
        "start_date": start,
        "end_exclusive": end + timedelta(days=1),
    }
    if ":domain" in source.where_clause:
        params["domain"] = _domain_parameter(source)

    date_expression = f"CAST({source.date_expression} AS DATE)"
    selected = [f"{date_expression} AS event_date"]
    group_by = [date_expression]
    if detail and source.category_expression:
        selected.append(f"{source.category_expression} AS category")
        group_by.append(source.category_expression)
    if detail and source.region_expression:
        selected.append(f"{source.region_expression} AS region")
        group_by.append(source.region_expression)
    selected.append(f"SUM({source.amount_expression}) AS amount_value")
    if source.snapshot:
        selected.append(f"SUM({source.count_expression}) AS count_value")
    else:
        selected.append(f"{source.count_expression} AS count_value")

    clauses = [f"{date_expression} >= :start_date", f"{date_expression} < :end_exclusive"]
    if source.where_clause:
        clauses.append(f"({source.where_clause})")
    if category is not None:
        if not source.category_expression:
            raise ValueError("Category filters are not supported for this domain and configured source.")
        params["category"] = category
        clauses.append(f"{source.category_expression} = :category")
    if region is not None:
        if not source.region_expression:
            raise ValueError("Region filters are not supported for this domain and configured source.")
        params["region"] = region
        clauses.append(f"{source.region_expression} = :region")

    query = (
        f"SELECT {', '.join(selected)} FROM {source.from_clause} "
        f"WHERE {' AND '.join(clauses)} GROUP BY {', '.join(group_by)} "
        "ORDER BY event_date LIMIT 2000"
    )
    return query, params


def _healthcare_schema() -> dict[str, Any]:
    columns = {
        "healthcare_claims": (
            "tenant_id", "encounter_key", "service_date", "claim_amount", "claim_status",
        ),
        "healthcare_encounters": (
            "tenant_id", "encounter_key", "facility_key",
        ),
        "healthcare_facilities": (
            "tenant_id", "facility_key", "facility_region",
        ),
    }
    return {
        "tables": {
            table: [{"name": column} for column in names]
            for table, names in columns.items()
        }
    }


def _run_queries(
    statements: list[tuple[str, dict[str, Any]]],
    domain: str,
    tenant_id: str | None,
) -> list[list[dict[str, Any]]]:
    scoped = []
    for sql, params in statements:
        if domain == "healthcare":
            sql = scope_healthcare_sql(sql, tenant_id, _healthcare_schema())
            sql = re.sub(r"%\(([A-Za-z_][A-Za-z0-9_]*)\)s", r":\1", sql)
        scoped.append((sql, params))
    return execute_readonly_many(scoped, domain=domain, row_limit=2000)


def _domain_parameter(source: ReportSource) -> str:
    if source.amount_key.startswith("transaction_"):
        return "banking"
    if source.amount_key == "revenue":
        return "retail"
    return "insurance"


def _availability_query(source: ReportSource) -> tuple[str, dict[str, Any]]:
    date_expression = f"CAST({source.date_expression} AS DATE)"
    query = (
        f"SELECT MIN({date_expression}) AS first_date, MAX({date_expression}) AS last_date "
        f"FROM {source.from_clause}"
    )
    params: dict[str, Any] = {}
    if source.where_clause:
        query += f" WHERE ({source.where_clause})"
        if ":domain" in source.where_clause:
            params["domain"] = _domain_parameter(source)
    return query, params


def _availability(bounds: list[dict[str, Any]]) -> dict[str, str | None]:
    if not bounds or bounds[0].get("first_date") is None or bounds[0].get("last_date") is None:
        return {"start": None, "end": None}
    return {
        "start": _as_date(bounds[0]["first_date"]).isoformat(),
        "end": _as_date(bounds[0]["last_date"]).isoformat(),
    }


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _bucket_start(value: date, interval: str) -> date:
    if interval == "daily":
        return value
    if interval == "weekly":
        return value - timedelta(days=value.weekday())
    if interval == "monthly":
        return value.replace(day=1)
    month = ((value.month - 1) // 3) * 3 + 1
    return value.replace(month=month, day=1)


def _next_bucket(value: date, interval: str) -> date:
    if interval == "daily":
        return value + timedelta(days=1)
    if interval == "weekly":
        return value + timedelta(days=7)
    if interval == "monthly":
        return _shift_month(value, 1)
    return _shift_month(value, 3)


def _bucket_label(value: date, interval: str) -> str:
    if interval == "monthly":
        return value.strftime("%b %Y")
    if interval == "quarterly":
        return f"Q{((value.month - 1) // 3) + 1} {value.year}"
    if interval == "weekly":
        return f"Week of {value.isoformat()}"
    return value.isoformat()


def _period_label(start: date, end: date, interval: str) -> str:
    first = _bucket_start(start, interval)
    last = _bucket_start(end, interval)
    if first == last:
        return _bucket_label(first, interval)
    return f"{_bucket_label(first, interval)} – {_bucket_label(last, interval)}"


def _value(row: dict[str, Any], key: str) -> float | None:
    raw = row.get(key)
    return None if raw is None else float(raw)


def _metric(
    key: str,
    label: str,
    current: float | None,
    comparison: float | None,
    unit: str,
    definition: str,
) -> dict[str, Any]:
    change = None
    if current is not None and comparison not in (None, 0):
        change = (current - comparison) / abs(comparison) * 100
    return {
        "key": key,
        "label": label,
        "current": current,
        "comparison": comparison,
        "unit": unit,
        "definition": definition,
        "change_percent": change,
        "higher_is_better": None,
    }


def get_report(
    domain: str,
    tenant_id: str | None,
    start: date,
    end: date,
    interval: str,
    comparison: str,
    comparison_start: date | None = None,
    comparison_end: date | None = None,
    category: str | None = None,
    region: str | None = None,
) -> dict[str, Any]:
    category = category.strip() if category is not None else None
    region = region.strip() if region is not None else None
    if category == "" or region == "":
        raise ValueError("Category and region filters must not be blank.")
    comparison_start, comparison_end = comparison_dates(
        start, end, comparison, comparison_start, comparison_end
    )
    source = _source(domain, tenant_id)
    period = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "comparison_start": comparison_start.isoformat(),
        "comparison_end": comparison_end.isoformat(),
        "label": _period_label(start, end, interval),
        "comparison_label": _period_label(comparison_start, comparison_end, interval),
    }
    if source is None:
        if category is not None or region is not None:
            raise ValueError("Category and region filters are not supported for this domain and configured source.")
        return {
            "domain": domain,
            "domain_label": DOMAIN_LABELS[domain],
            "period": period,
            "availability": {"start": None, "end": None},
            "metrics": [],
            "trend": [],
            "categories": [],
            "regions": [],
            "rows": [],
            "filter_options": {"categories": [], "regions": []},
            "updated_at": datetime.now().astimezone().isoformat(),
        }

    if category is not None and not source.category_expression:
        raise ValueError("Category filters are not supported for this domain and configured source.")
    if region is not None and not source.region_expression:
        raise ValueError("Region filters are not supported for this domain and configured source.")
    if source.snapshot and interval != "monthly":
        raise ValueError(
            f"{DOMAIN_LABELS[domain]} data is only available as monthly snapshots. "
            "Choose the Monthly reporting interval."
        )

    current_sql, current_params = _build_query(source, start, end, interval, category, region, detail=True)
    comparison_sql, comparison_params = _build_query(
        source, comparison_start, comparison_end, interval, category, region, detail=True
    )
    current_totals_sql, current_totals_params = _build_query(
        source, start, end, interval, category, region, detail=False
    )
    comparison_totals_sql, comparison_totals_params = _build_query(
        source, comparison_start, comparison_end, interval, category, region, detail=False
    )
    options_sql, options_params = _build_query(
        source, start, end, interval, None, None, detail=True
    )
    # The six queries are independent, so they run as one batch (concurrently on Databricks).
    (
        bounds,
        current_rows,
        comparison_rows,
        current_totals,
        comparison_totals,
        option_rows,
    ) = _run_queries(
        [
            _availability_query(source),
            (current_sql, current_params),
            (comparison_sql, comparison_params),
            (current_totals_sql, current_totals_params),
            (comparison_totals_sql, comparison_totals_params),
            (options_sql, options_params),
        ],
        domain,
        tenant_id,
    )
    availability = _availability(bounds)

    current_by_date: dict[date, dict[str, Any]] = {}
    comparison_by_date: dict[date, dict[str, Any]] = {}
    for rows, destination in (
        (current_totals, current_by_date),
        (comparison_totals, comparison_by_date),
    ):
        for row in rows:
            day = _as_date(row["event_date"])
            bucket = _bucket_start(day, interval)
            values = destination.setdefault(bucket, {"amount": 0.0, "count": 0.0})
            amount_value = _value(row, "amount_value")
            count_value = _value(row, "count_value")
            if amount_value is not None:
                values["amount"] += amount_value
            if count_value is not None:
                values["count"] += count_value

    current_total = sum(value["amount"] for value in current_by_date.values()) if current_by_date else None
    comparison_total = (
        sum(value["amount"] for value in comparison_by_date.values()) if comparison_by_date else None
    )
    current_count = sum(value["count"] for value in current_by_date.values()) if current_by_date else None
    comparison_count = (
        sum(value["count"] for value in comparison_by_date.values()) if comparison_by_date else None
    )
    if source.snapshot:
        current_total = current_by_date[max(current_by_date)]["amount"] if current_by_date else None
        comparison_total = (
            comparison_by_date[max(comparison_by_date)]["amount"] if comparison_by_date else None
        )
        current_count = current_by_date[max(current_by_date)]["count"] if current_by_date else None
        comparison_count = (
            comparison_by_date[max(comparison_by_date)]["count"] if comparison_by_date else None
        )

    metrics = [
        _metric(
            source.amount_key, source.amount_label, current_total, comparison_total,
            source.amount_unit, source.amount_definition,
        ),
        _metric(
            source.count_key, source.count_label, current_count, comparison_count,
            "count", source.count_definition,
        ),
    ]

    current_buckets: list[date] = []
    bucket = _bucket_start(start, interval)
    final_bucket = _bucket_start(end, interval)
    while bucket <= final_bucket:
        current_buckets.append(bucket)
        bucket = _next_bucket(bucket, interval)
    comparison_buckets: list[date] = []
    bucket = _bucket_start(comparison_start, interval)
    final_bucket = _bucket_start(comparison_end, interval)
    while bucket <= final_bucket:
        comparison_buckets.append(bucket)
        bucket = _next_bucket(bucket, interval)
    trend = [
        {
            "label": _bucket_label(bucket, interval),
            "value": current_by_date.get(bucket, {}).get("amount"),
            "comparison_value": (
                comparison_by_date.get(comparison_buckets[index], {}).get("amount")
                if index < len(comparison_buckets) else None
            ),
        }
        for index, bucket in enumerate(current_buckets)
    ]
    count_trend = [
        {
            "label": _bucket_label(bucket, interval),
            "value": current_by_date.get(bucket, {}).get("count"),
            "comparison_value": (
                comparison_by_date.get(comparison_buckets[index], {}).get("count")
                if index < len(comparison_buckets) else None
            ),
        }
        for index, bucket in enumerate(current_buckets)
    ]
    metrics[0]["trend"] = trend
    metrics[1]["trend"] = count_trend

    category_totals: dict[str, float] = {}
    region_totals: dict[str, float] = {}
    available_categories: set[str] = set()
    available_regions: set[str] = set()
    for row in option_rows:
        if row.get("category") is not None:
            available_categories.add(str(row["category"]))
        if row.get("region") is not None:
            available_regions.add(str(row["region"]))
    report_rows = []
    for row in current_rows:
        amount_value = _value(row, "amount_value")
        if amount_value is None:
            continue
        category_value = row.get("category")
        region_value = row.get("region")
        if category_value is not None:
            category_totals[str(category_value)] = category_totals.get(str(category_value), 0.0) + amount_value
        if region_value is not None:
            region_totals[str(region_value)] = region_totals.get(str(region_value), 0.0) + amount_value
        event_bucket = _bucket_start(_as_date(row["event_date"]), interval)
        report_rows.append({
            "period": _bucket_label(event_bucket, interval),
            "category": str(category_value) if category_value is not None else "",
            "region": str(region_value) if region_value is not None else "",
            "metric": source.amount_key,
            "value": amount_value,
        })
        count_value = _value(row, "count_value")
        if count_value is not None:
            report_rows.append({
                "period": _bucket_label(event_bucket, interval),
                "category": str(category_value) if category_value is not None else "",
                "region": str(region_value) if region_value is not None else "",
                "metric": source.count_key,
                "value": count_value,
            })

    return {
        "domain": domain,
        "domain_label": DOMAIN_LABELS[domain],
        "period": period,
        "availability": availability,
        "metrics": metrics,
        "trend": trend,
        "categories": [
            {"label": name, "value": value}
            for name, value in sorted(category_totals.items(), key=lambda item: (-item[1], item[0]))
        ],
        "regions": [
            {"label": name, "value": value}
            for name, value in sorted(region_totals.items(), key=lambda item: (-item[1], item[0]))
        ],
        "rows": report_rows,
        "filter_options": {
            "categories": sorted(available_categories),
            "regions": sorted(available_regions),
        },
        "updated_at": datetime.now().astimezone().isoformat(),
    }
