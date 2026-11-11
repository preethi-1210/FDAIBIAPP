"""Governed business metrics: approved formulas computed deterministically from verified tables.

A metric here is answered without an LLM writing its SQL. Definitions use only columns verified
against Unity Catalog. A metric whose business definition is not approved is refused with an
explanation rather than approximated.
"""

import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable

from ..warehouse import execute_readonly
from .reports import RETAIL_NET_SALES

_MONTHS = {
    name: index
    for index, name in enumerate(
        ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
         "november", "december"),
        start=1,
    )
}
# A breakdown or comparison is not a single metric value; those go to SQL generation.
_NOT_A_SINGLE_VALUE = re.compile(
    r"\b(?:by|per|each|compare|compared|versus|vs|trend|monthly|weekly|daily|top|highest|lowest|why|average|forecast|predict)\b",
    re.IGNORECASE,
)
# The period forms parse_period understands. Anything else about time is left for SQL generation.
_PERIOD_TEXT = re.compile(
    r"\b(?:(?:in|for|during)\s+)?(?:(?:" + "|".join(_MONTHS) + r")\s+\d{4}|\d{4}-\d{2}|q[1-4]\s+\d{4}|last\s+(?:week|month))\b"
    r"|\b(?:in|for|during)\s+\d{4}\b",
    re.IGNORECASE,
)
_NO_FILTER_WORDS = frozenset(
    "what whats is was were are the our my me us show give tell get find see display report calculate compute "
    "please can could would you i we a an total overall all available data value amount figure sum how much "
    "have has did do does of".split()
)


@dataclass(frozen=True)
class Period:
    start: date | None
    end_exclusive: date | None
    label: str


@dataclass(frozen=True)
class MetricDefinition:
    key: str
    label: str
    domain: str
    aliases: re.Pattern[str]
    formula: str
    sources: tuple[str, ...]
    unit: str
    filters: str
    date_expression: str
    from_clause: str
    where_clause: str
    value_expression: str
    count_expression: str
    denominator: "MetricDefinition | None" = None
    # False for a current figure: its date column says when an account opened, so a period
    # would change what is measured instead of when it was measured.
    dated: bool = True


def _metric(key: str, label: str, domain: str, aliases: str, **fields: Any) -> MetricDefinition:
    return MetricDefinition(key, label, domain, re.compile(aliases, re.IGNORECASE), **fields)


_WRITTEN_PREMIUM = _metric(
    "written_premium",
    "Written premium",
    "insurance",
    r"\b(?:total\s+|gross\s+)?written\s+premiums?\b|\btotal\s+premiums?\b",
    formula="SUM(premium_amount) of non-cancelled policies, dated by policy start_date",
    sources=("gold_policies",),
    unit="currency",
    filters="policy_status is not CANCELLED",
    date_expression="start_date",
    from_clause="gold_policies",
    where_clause="upper(policy_status) != 'CANCELLED'",
    value_expression="SUM(premium_amount)",
    count_expression="COUNT(*)",
)
_APPROVED_CLAIMS = _metric(
    "approved_claims",
    "Approved claim amount",
    "insurance",
    r"\b(?:total\s+)?(?:approved|paid)\s+claims?(?:\s+amounts?)?\b",
    formula="SUM(approved_amount) of claims with status APPROVED or PAID, dated by claim_date",
    sources=("gold_claims",),
    unit="currency",
    filters="claim_status is APPROVED or PAID",
    date_expression="claim_date",
    from_clause="gold_claims",
    where_clause="upper(claim_status) IN ('APPROVED', 'PAID')",
    value_expression="SUM(approved_amount)",
    count_expression="COUNT(*)",
)
METRICS: tuple[MetricDefinition, ...] = (
    _metric(
        "transaction_volume",
        "Transaction volume",
        "banking",
        r"\b(?:total\s+)?transaction\s+(?:volume|amount|value)s?\b",
        formula="SUM(amount) of all transactions, dated by txn_timestamp",
        sources=("gold_transactions",),
        unit="currency",
        filters="none",
        date_expression="txn_timestamp",
        from_clause="gold_transactions",
        where_clause="",
        value_expression="SUM(amount)",
        count_expression="COUNT(*)",
    ),
    _metric(
        "opened_account_balance",
        "Balance of accounts opened",
        "finance",
        r"\b(?:total\s+)?balances?\s+of\s+accounts?\s+opened\b|\bopened\s+account\s+balances?\b",
        formula="SUM(total_cash_balance) of accounts grouped by open date (agg_accounts_daily)",
        sources=("agg_accounts_daily",),
        unit="currency",
        filters="none",
        date_expression="_gold_date",
        from_clause="agg_accounts_daily",
        where_clause="",
        value_expression="SUM(total_cash_balance)",
        count_expression="SUM(record_count)",
    ),
    _metric(
        "total_cash_balance",
        "Total cash balance",
        "finance",
        r"\btotal\s+cash\s+balances?(?:\s+across\s+(?:all\s+)?accounts)?\b",
        formula="SUM(cash_balance) of all accounts, every status included (gold_accounts)",
        sources=("gold_accounts",),
        unit="currency",
        filters="none; ACTIVE, DORMANT and CLOSED accounts are all included",
        date_expression="open_date",
        from_clause="gold_accounts",
        where_clause="",
        value_expression="SUM(cash_balance)",
        count_expression="COUNT(*)",
        dated=False,
    ),
    _metric(
        "active_account_cash_balance",
        "Active-account cash balance",
        "finance",
        r"\bactive[-\s]accounts?\s+cash\s+balances?\b|\bcash\s+balances?\s+of\s+active\s+accounts?\b",
        formula="SUM(cash_balance) of accounts with status ACTIVE (gold_accounts)",
        sources=("gold_accounts",),
        unit="currency",
        filters="status = 'ACTIVE'",
        date_expression="open_date",
        from_clause="gold_accounts",
        where_clause="status = 'ACTIVE'",
        value_expression="SUM(cash_balance)",
        count_expression="COUNT(*)",
        dated=False,
    ),
    _metric(
        "executed_trades",
        "Executed trades",
        "finance",
        r"\bhow\s+many\s+trades\s+(?:were|are)\s+executed\b|\bexecuted\s+trades?\b",
        formula="COUNT(*) of trades with status EXECUTED, dated by trade_timestamp (gold_trades)",
        sources=("gold_trades",),
        unit="count",
        filters="status = 'EXECUTED'",
        date_expression="trade_timestamp",
        from_clause="gold_trades",
        where_clause="status = 'EXECUTED'",
        value_expression="COUNT(*)",
        count_expression="COUNT(*)",
    ),
    _metric(
        "gross_trade_value",
        "Gross trade value",
        "finance",
        r"\bgross\s+trade\s+value\b",
        formula="SUM(price * quantity) of all trades, every status included, dated by trade_timestamp (gold_trades)",
        sources=("gold_trades",),
        unit="currency",
        filters="none; EXECUTED, CANCELLED and REJECTED trades are all included",
        date_expression="trade_timestamp",
        from_clause="gold_trades",
        where_clause="",
        value_expression="SUM(price * quantity)",
        count_expression="COUNT(*)",
    ),
    # Checked before its two measures: their aliases also match the ratio's name.
    _metric(
        "claims_to_written_premium",
        "Approved claims to written premium",
        "insurance",
        r"\b(?:approved\s+)?claims?\s+to\s+(?:written\s+)?premium(?:\s+ratio)?\b",
        formula="Approved claim amount / written premium * 100, each over the same period. Not an earned-premium loss ratio.",
        sources=("gold_claims", "gold_policies"),
        unit="percent",
        filters="claims APPROVED or PAID; policies not CANCELLED",
        date_expression="claim_date",
        from_clause="gold_claims",
        where_clause="upper(claim_status) IN ('APPROVED', 'PAID')",
        value_expression="SUM(approved_amount)",
        count_expression="COUNT(*)",
        denominator=_WRITTEN_PREMIUM,
    ),
    _WRITTEN_PREMIUM,
    _APPROVED_CLAIMS,
    _metric(
        "net_sales",
        "Net sales",
        "retail",
        r"\b(?:total\s+)?(?:net\s+sales|revenue)\b",
        formula="SUM(quantity * unit_price * (1 - discount capped at 100%)) of non-cancelled orders, dated by order_date",
        sources=("gold_order_items", "gold_orders"),
        unit="currency",
        filters="order_status is not CANCELLED; orders dated in the future are excluded",
        date_expression="o.order_date",
        from_clause="gold_order_items i JOIN gold_orders o ON o.order_id = i.order_id",
        where_clause="upper(o.order_status) != 'CANCELLED' AND o.order_date <= current_timestamp()",
        value_expression=f"SUM({RETAIL_NET_SALES})",
        count_expression="COUNT(DISTINCT o.order_id)",
    ),
)
_LOSS_RATIO = re.compile(r"\bloss\s+ratios?\b", re.IGNORECASE)
LOSS_RATIO_LIMITATION = (
    "An insurance loss ratio (incurred claims / earned premium x 100) cannot be reported yet. The source has "
    "no earned-premium or reserve figures, and the organization has not confirmed whether expenses, reserves "
    "and recoveries are included, so calculating one would be misleading. What is available is “approved claims "
    "to written premium”, which is a different measure; ask for it by name."
)


def parse_period(question: str, today: date | None = None) -> Period:
    """Read an explicit period from the question; no period means all available data."""
    today = today or date.today()
    q = question.lower()
    match = re.search(r"\b(" + "|".join(_MONTHS) + r")\s+(\d{4})\b", q)
    if match:
        year, month = int(match.group(2)), _MONTHS[match.group(1)]
        return _month_period(year, month, f"{match.group(1).title()} {year}")
    match = re.search(r"\b(\d{4})-(\d{2})\b", q)
    if match and 1 <= int(match.group(2)) <= 12:
        return _month_period(int(match.group(1)), int(match.group(2)), match.group(0))
    match = re.search(r"\bq([1-4])\s+(\d{4})\b", q)
    if match:
        quarter, year = int(match.group(1)), int(match.group(2))
        start = date(year, 3 * quarter - 2, 1)
        end = date(year + 1, 1, 1) if quarter == 4 else date(year, 3 * quarter + 1, 1)
        return Period(start, end, f"Q{quarter} {year}")
    match = re.search(r"\b(?:in|for|during)\s+(\d{4})\b", q)
    if match:
        year = int(match.group(1))
        return Period(date(year, 1, 1), date(year + 1, 1, 1), str(year))
    if "last week" in q:
        this_week = today - timedelta(days=today.weekday())
        return Period(this_week - timedelta(days=7), this_week, "last week")
    if "last month" in q:
        first = today.replace(day=1)
        previous = (first - timedelta(days=1)).replace(day=1)
        return Period(previous, first, "last month")
    return Period(None, None, "all available data")


def _month_period(year: int, month: int, label: str) -> Period:
    start = date(year, month, 1)
    return Period(start, start + timedelta(days=monthrange(year, month)[1]), label)


def match_metric(question: str, domain: str) -> MetricDefinition | str | None:
    """The governed metric for this question, a refusal explanation, or None to use SQL generation."""
    if domain == "insurance" and _LOSS_RATIO.search(question):
        return LOSS_RATIO_LIMITATION
    if _NOT_A_SINGLE_VALUE.search(question):
        return None
    metric = next((m for m in METRICS if m.domain == domain and m.aliases.search(question)), None)
    # An approved metric has no filters of its own, so "net sales for electronics" must not be
    # answered with total net sales. Any word left over may be a filter; SQL generation handles it.
    if metric is None:
        return None
    if not metric.dated and _PERIOD_TEXT.search(question):
        return None
    remainder = _PERIOD_TEXT.sub(" ", metric.aliases.sub(" ", question))
    return None if set(re.findall(r"[a-z0-9]+", remainder.lower())) - _NO_FILTER_WORDS else metric


def _measure(
    metric: MetricDefinition,
    period: Period,
    execute: Callable[..., list[dict[str, Any]]],
) -> dict[str, Any]:
    date_expression = f"CAST({metric.date_expression} AS DATE)"
    clauses = [metric.where_clause] if metric.where_clause else []
    parameters: dict[str, Any] = {}
    if period.start is not None:
        clauses.extend([f"{date_expression} >= :period_start", f"{date_expression} < :period_end"])
        parameters = {"period_start": period.start, "period_end": period.end_exclusive}
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = (
        f"SELECT {metric.value_expression} AS metric_value, {metric.count_expression} AS record_count "
        f"FROM {metric.from_clause}{where} LIMIT 1"
    )
    rows = execute(sql, metric.domain, parameters or None, 1)
    row = rows[0] if rows else {}
    return {
        "sql": sql,
        "value": float(row["metric_value"]) if row.get("metric_value") is not None else None,
        "records": int(float(row["record_count"])) if row.get("record_count") is not None else 0,
    }


def calculate_metric(
    metric: MetricDefinition,
    question: str,
    today: date | None = None,
    execute: Callable[..., list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Compute a metric in code from actual query results. value None means no matching records."""
    run = execute or (lambda sql, domain, parameters, limit: execute_readonly(sql, domain, parameters, limit))
    period = parse_period(question, today)
    measured = _measure(metric, period, run)
    value = measured["value"]
    sql = measured["sql"]
    records = measured["records"]
    if metric.denominator is not None:
        denominator = _measure(metric.denominator, period, run)
        sql = f"{sql}\n\n{denominator['sql']}"
        value = (
            None
            if value is None or not denominator["value"]
            else round(value / denominator["value"] * 100, 2)
        )
    if records == 0:
        value = None
    return {
        "metric": metric.key,
        "label": metric.label,
        "value": value,
        "unit": metric.unit,
        "records": records,
        "period": period.label,
        "formula": metric.formula,
        "filters": metric.filters,
        "sources": list(metric.sources),
        "sql": sql,
    }


def format_metric_answer(result: dict[str, Any]) -> str:
    formats = {"percent": ",.2f", "count": ",.0f"}
    value = format(result["value"], formats.get(result["unit"], ",.2f")) + ("%" if result["unit"] == "percent" else "")
    return (
        f"{result['label']} for {result['period']}: {value} across {result['records']:,} records. "
        f"Source: {', '.join(result['sources'])}. Definition: {result['formula']} Filters: {result['filters']}."
    )
