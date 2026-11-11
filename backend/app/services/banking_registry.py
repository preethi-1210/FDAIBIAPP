"""Versioned registry of approved banking metrics, verified against Unity Catalog banking.gold.

The registry, not a language model, decides what can be measured, from which table and how. A metric
cannot be asked for by a dimension, filter value or period the registry does not list, so a request
that cannot be represented is reported instead of being approximated. Bump a metric's version when
its meaning changes; REGISTRY_VERSION is recorded with every answer.
"""

import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator

REGISTRY_VERSION = "1.0.0"
CATALOG_SCHEMA = "banking.gold"
ROLES = ("admin", "analyst")

# Business tables generated SQL may read. summary_*, snapshot_*, dim_*, fact_* and pipeline tables
# duplicate these or hold per-record figures that look like totals, and agg_customers_* aggregates
# phone numbers and postcodes.
APPROVED_TABLES = frozenset({
    "gold_transactions", "gold_accounts", "gold_branches", "gold_cards", "gold_customers", "gold_loans",
    "gold_loan_repayments", "agg_transactions_daily", "agg_transactions_monthly", "agg_accounts_daily",
    "agg_accounts_monthly", "agg_cards_daily", "agg_cards_monthly", "agg_loans_daily", "agg_loans_monthly",
    "agg_loan_repayments_daily", "agg_loan_repayments_monthly",
})

_ACCOUNT_JOIN = "LEFT JOIN gold_accounts a ON a.account_id = t.account_id"
_BRANCH_FROM_TXN = "LEFT JOIN gold_branches b ON b.branch_id = a.branch_id"
_CUSTOMER_FROM_TXN = "LEFT JOIN gold_customers cu ON cu.customer_id = a.customer_id"
_CITIES = ("Ahmedabad", "Bengaluru", "Chennai", "Delhi", "Hyderabad", "Kolkata", "Mumbai", "Pune")
_STATES = ("Delhi", "Gujarat", "Karnataka", "Maharashtra", "Tamil Nadu", "Telangana", "West Bengal")
_REGIONS = ("NORTH", "EAST", "WEST", "SOUTH")
_SEGMENTS = ("MASS", "AFFLUENT", "PREMIUM")
_ACCOUNT_TYPES = ("CURRENT", "SALARY", "SAVINGS")


@dataclass(frozen=True)
class Dimension:
    id: str
    label: str
    expression: str
    aliases: tuple[str, ...]
    values: tuple[str, ...] = ()
    # Spoken forms of a value, such as "successful" for SUCCESS.
    value_aliases: tuple[tuple[str, str], ...] = ()
    joins: tuple[str, ...] = ()
    # False when a value is also an everyday word ("store", "monthly"): only value_aliases are read.
    match_values: bool = True


@dataclass(frozen=True)
class Source:
    id: str
    from_clause: str
    tables: tuple[str, ...]
    date_expression: str
    date_meaning: str
    dimensions: tuple[Dimension, ...]

    def dimension(self, dimension_id: str) -> Dimension | None:
        return next((item for item in self.dimensions if item.id == dimension_id), None)


@dataclass(frozen=True)
class Measure:
    alias: str
    label: str
    expression: str
    unit: str  # count | inr | percent


@dataclass(frozen=True)
class Metric:
    id: str
    label: str
    definition: str
    source: Source
    measures: tuple[Measure, ...]
    # Regexes; a group named "gap" is left for filters, as in "how many UPI transactions".
    aliases: tuple[str, ...]
    required_columns: tuple[str, ...]
    aggregation: str
    time_semantics: str
    # False for current-state figures: the source holds no history of them, so a period cannot apply.
    supports_time: bool = True
    forecastable: bool = False
    required_filter: str | None = None
    data_quality: tuple[str, ...] = ()
    example_question: str = ""
    version: str = "1"
    roles: tuple[str, ...] = ROLES
    # Added to every dimension list so a breakdown can be checked against the total.
    reconcile: bool = True
    extra: dict = field(default_factory=dict)

    @property
    def primary(self) -> Measure:
        return self.measures[0]


_REGION = Dimension("region", "branch region", "b.region", (r"regions?", r"zones?"), _REGIONS, joins=(_ACCOUNT_JOIN, _BRANCH_FROM_TXN))
_STATE = Dimension("state", "branch state", "b.state", (r"states?",), _STATES, joins=(_ACCOUNT_JOIN, _BRANCH_FROM_TXN))
_TXN_SEGMENT = Dimension("customer_segment", "customer segment", "cu.customer_segment", (r"customer\s+segments?", r"segments?"), _SEGMENTS, joins=(_ACCOUNT_JOIN, _CUSTOMER_FROM_TXN))
_TXN_ACCOUNT_TYPE = Dimension("account_type", "account type", "a.account_type", (r"account\s+types?",), _ACCOUNT_TYPES, joins=(_ACCOUNT_JOIN,))

TRANSACTIONS = Source(
    "transactions",
    "gold_transactions t",
    ("gold_transactions", "gold_accounts", "gold_branches", "gold_customers"),
    "t.txn_timestamp",
    "transaction date",
    (
        Dimension("txn_type", "transaction type", "t.txn_type", (r"(?:transaction|txn)\s+types?", r"types?(?:\s+of\s+transactions?)?"), ("DEBIT", "CREDIT", "TRANSFER"), (("debits", "DEBIT"), ("credits", "CREDIT"), ("transfers", "TRANSFER"))),
        Dimension("channel", "channel", "t.channel", (r"channels?",), ("ATM", "NETBANKING", "IMPS", "POS", "UPI", "NEFT"), (("net banking", "NETBANKING"), ("internet banking", "NETBANKING"))),
        Dimension("merchant_category", "merchant category", "t.merchant_category", (r"merchant\s+categor(?:y|ies)", r"merchants?", r"categor(?:y|ies)"), ("CASH", "FOOD", "ELECTRONICS", "TRAVEL", "UTILITIES", "GROCERY", "SHOPPING", "FUEL", "JEWELLERY"), (("jewelry", "JEWELLERY"), ("groceries", "GROCERY"), ("utility", "UTILITIES"), ("petrol", "FUEL"), ("diesel", "FUEL"), ("flights", "TRAVEL"), ("restaurants", "FOOD"))),
        Dimension("status", "transaction status", "t.status", (r"(?:transaction\s+)?status(?:es)?",), ("SUCCESS", "FAILED", "PENDING"), (("successful", "SUCCESS"), ("succeeded", "SUCCESS"), ("completed", "SUCCESS"), ("failures", "FAILED"), ("failure", "FAILED"), ("fail", "FAILED"))),
        Dimension("city", "transaction city", "t.city", (r"cit(?:y|ies)", r"locations?"), _CITIES, (("bangalore", "Bengaluru"),)),
        _REGION,
        _STATE,
        _TXN_ACCOUNT_TYPE,
        _TXN_SEGMENT,
    ),
)
ACCOUNTS = Source(
    "accounts",
    "gold_accounts a",
    ("gold_accounts", "gold_branches", "gold_customers"),
    "a.open_date",
    "account opening date",
    (
        Dimension("account_type", "account type", "a.account_type", (r"account\s+types?", r"types?(?:\s+of\s+accounts?)?"), _ACCOUNT_TYPES),
        Dimension("account_status", "account status", "a.status", (r"(?:account\s+)?status(?:es)?",), ("ACTIVE", "DORMANT", "CLOSED")),
        Dimension("region", "branch region", "b.region", (r"regions?", r"zones?"), _REGIONS, joins=("LEFT JOIN gold_branches b ON b.branch_id = a.branch_id",)),
        Dimension("state", "branch state", "b.state", (r"states?",), _STATES, joins=("LEFT JOIN gold_branches b ON b.branch_id = a.branch_id",)),
        Dimension("city", "branch city", "b.city", (r"cit(?:y|ies)", r"locations?"), _CITIES, (("bangalore", "Bengaluru"),), joins=("LEFT JOIN gold_branches b ON b.branch_id = a.branch_id",)),
        Dimension("customer_segment", "customer segment", "cu.customer_segment", (r"customer\s+segments?", r"segments?"), _SEGMENTS, joins=("LEFT JOIN gold_customers cu ON cu.customer_id = a.customer_id",)),
    ),
)
LOANS = Source(
    "loans",
    "gold_loans l",
    ("gold_loans",),
    "l.disbursement_date",
    "disbursement date",
    (Dimension("loan_type", "loan type", "l.loan_type", (r"loan\s+types?", r"types?(?:\s+of\s+loans?)?", r"products?"), ("AUTO", "PERSONAL", "EDUCATION", "HOME")),),
)
REPAYMENTS = Source(
    "repayments",
    "gold_loan_repayments r",
    ("gold_loan_repayments", "gold_loans"),
    "r.due_date",
    "instalment due date",
    (Dimension("loan_type", "loan type", "l.loan_type", (r"loan\s+types?", r"types?(?:\s+of\s+loans?)?", r"products?"), ("AUTO", "PERSONAL", "EDUCATION", "HOME"), joins=("LEFT JOIN gold_loans l ON l.loan_id = r.loan_id",)),),
)
CARDS = Source(
    "cards",
    "gold_cards c",
    ("gold_cards",),
    "c.issue_date",
    "card issue date",
    (
        Dimension("card_type", "card type", "c.card_type", (r"card\s+types?", r"types?(?:\s+of\s+cards?)?"), ("CREDIT", "DEBIT")),
        Dimension("card_status", "card status", "c.status", (r"(?:card\s+)?status(?:es)?",), ("ACTIVE", "EXPIRED", "BLOCKED")),
    ),
)
CUSTOMERS = Source(
    "customers",
    "gold_customers cu",
    ("gold_customers",),
    "cu.onboarding_date",
    "customer onboarding date",
    (
        Dimension("customer_segment", "customer segment", "cu.customer_segment", (r"customer\s+segments?", r"segments?"), _SEGMENTS),
        Dimension("city", "customer city", "cu.city", (r"cit(?:y|ies)", r"locations?"), _CITIES, (("bangalore", "Bengaluru"),)),
        Dimension("state", "customer state", "cu.state", (r"states?",), _STATES),
    ),
)

_ALL_STATUSES = "Counts every transaction status, including FAILED and PENDING; add \"successful\" to the question to restrict it."
_TXN_QUALITY = (
    "The source holds one business day, 2026-03-15, so it has no trend or history.",
    "60 transactions have negative amounts and are included in totals.",
    "30 transactions have no matching account; they appear as unmatched in region, state, account type and segment breakdowns.",
)
_ONE_DAY = "Dated by txn_timestamp, a DATE column. All current rows fall on 2026-03-15."
_SNAPSHOT = "Current state at the last pipeline load; the source holds no history of this figure."
_REPAYMENT_QUALITY = (
    "Instalments are dated by due date, and the latest month (2026-03) is only populated to the 15th.",
    "From 2025-07 some instalments are recorded with amount_paid 0 and no paid_date, so collected amounts fall below amounts due.",
)

METRICS: tuple[Metric, ...] = (
    Metric(
        "transaction_failure_rate", "Transaction failure rate",
        "Share of transactions whose status is FAILED.",
        TRANSACTIONS,
        (Measure("failure_rate_pct", "failure rate", "ROUND(100.0 * AVG(CASE WHEN t.status = 'FAILED' THEN 1.0 ELSE 0.0 END), 2)", "percent"),),
        (r"\b(?:transaction\s+)?failure\s+rate\b", r"\bfailed\s+(?:transaction\s+)?(?:rate|percentage|share)\b", r"\b(?:percentage|share)\s+of\s+failed\s+transactions?\b"),
        ("status",), "AVG over transactions", _ONE_DAY, data_quality=_TXN_QUALITY, example_question="What is the failure rate by channel?",
    ),
    Metric(
        "average_transaction_amount", "Average transaction amount",
        f"Mean amount per transaction. {_ALL_STATUSES}",
        TRANSACTIONS,
        (Measure("average_amount", "average amount", "ROUND(AVG(t.amount), 2)", "inr"),),
        (r"\b(?:average|avg|mean)\s+(?:transaction\s+)?(?:amount|value|size)(?:\s+(?:of|per)\s+transactions?)?\b", r"\b(?:average|avg|mean)\s+(?P<gap>.*?)\btransactions?(?:\s+(?:amount|value|size))?\b"),
        ("amount",), "AVG(amount)", _ONE_DAY, data_quality=_TXN_QUALITY, example_question="What is the average transaction amount for UPI?",
    ),
    Metric(
        "transaction_amount", "Total transaction amount",
        f"Sum of transaction amounts, debit and credit added together rather than netted. {_ALL_STATUSES}",
        TRANSACTIONS,
        (Measure("total_amount", "total amount", "ROUND(SUM(t.amount), 2)", "inr"), Measure("transaction_count", "transactions", "COUNT(*)", "count")),
        (r"\b(?:total\s+)?transaction\s+(?:amount|value)s?\b", r"\b(?:total\s+)?(?:amount|value)\s+of\s+(?P<gap>.*?)\btransactions?\b", r"\b(?:total|sum\s+of)\s+(?P<gap>.*?)\btransactions?\s+(?:amount|value)s?\b", r"\b(?:total\s+)?(?:spend|spending)\b"),
        ("amount",), "SUM(amount)", _ONE_DAY, data_quality=_TXN_QUALITY, example_question="What is the total transaction amount for March 2026?",
    ),
    Metric(
        "transaction_volume", "Transaction volume",
        f"Number of transactions, reported with their total amount because \"volume\" is used for both. {_ALL_STATUSES}",
        TRANSACTIONS,
        (Measure("transaction_count", "transactions", "COUNT(*)", "count"), Measure("total_amount", "total amount", "ROUND(SUM(t.amount), 2)", "inr")),
        (r"\b(?:total\s+)?(?:transaction|txn)\s+(?:volumes?|counts?)\b", r"\bvolumes?\s+of\s+(?P<gap>.*?)\btransactions?\b", r"\b(?:how\s+many|number\s+of|count\s+of|total)\s+(?P<gap>.*?)\btransactions?\b", r"\btransactions?\b"),
        ("txn_id", "amount"), "COUNT(*), SUM(amount)", _ONE_DAY, data_quality=_TXN_QUALITY, example_question="Show the total transaction volume for March 2026",
    ),
    Metric(
        "new_accounts_opened", "New accounts opened",
        "Number of accounts by opening date, whatever their status today.",
        ACCOUNTS,
        (Measure("accounts_opened", "accounts opened", "COUNT(*)", "count"),),
        (r"\b(?:new\s+)?accounts?\s+(?:opened|openings?)\b", r"\bnew\s+(?P<gap>.*?)\baccounts?\b", r"\baccount\s+openings?\b", r"\b(?:opened|opening)\s+(?P<gap>.*?)\baccounts?\b"),
        ("account_id", "open_date"), "COUNT(*)", "Dated by open_date, from 2010-01-02 to 2026-06-06.",
        forecastable=True, data_quality=("The latest month, 2026-06, is only populated to the 6th.",), example_question="Show the monthly trend of new accounts opened",
    ),
    Metric(
        "average_account_balance", "Average account balance",
        "Mean current balance per account, in INR. Accounts with no recorded balance are ignored.",
        ACCOUNTS,
        (Measure("average_balance", "average balance", "ROUND(AVG(a.balance), 2)", "inr"),),
        (r"\b(?:average|avg|mean)\s+(?P<gap>.*?)\b(?:account\s+)?balances?\b",),
        ("balance",), "AVG(balance)", _SNAPSHOT, supports_time=False, data_quality=("15 accounts have no balance.",), example_question="What is the average balance by account type?",
    ),
    Metric(
        "total_account_balance", "Total account balance",
        "Sum of current balances, in INR. Accounts with no recorded balance are ignored.",
        ACCOUNTS,
        (Measure("total_balance", "total balance", "ROUND(SUM(a.balance), 2)", "inr"),),
        (r"\b(?:total\s+)?(?P<gap>.*?)\b(?:account\s+)?balances?\b",),
        ("balance",), "SUM(balance)", _SNAPSHOT, supports_time=False, data_quality=("15 accounts have no balance.",), example_question="What is the total balance by region?",
    ),
    Metric(
        "account_count", "Number of accounts",
        "Number of accounts held today, in any status unless a status is asked for.",
        ACCOUNTS,
        (Measure("account_count", "accounts", "COUNT(*)", "count"),),
        (r"\b(?:how\s+many|number\s+of|count\s+of|total)\s+(?P<gap>.*?)\baccounts?\b", r"\baccount\s+counts?\b", r"\baccounts?\b"),
        ("account_id",), "COUNT(*)", _SNAPSHOT, supports_time=False, example_question="How many dormant accounts are there?",
    ),
    Metric(
        "collection_rate", "Repayment collection rate",
        "Amount paid as a share of the amount due, for instalments due in the period.",
        REPAYMENTS,
        (Measure("collection_rate_pct", "collection rate", "ROUND(100.0 * SUM(r.amount_paid) / NULLIF(SUM(r.emi_due), 0), 2)", "percent"),),
        (r"\b(?:repayment\s+|loan\s+|emi\s+)?collection\s+rate\b", r"\bcollection\s+efficiency\b"),
        ("amount_paid", "emi_due"), "SUM(amount_paid) / SUM(emi_due)", "Dated by due_date, from 2024-01-31 to 2026-03-15.",
        data_quality=_REPAYMENT_QUALITY, example_question="What is the collection rate by loan type?",
    ),
    Metric(
        "missed_instalments", "Missed instalments",
        "Number of instalments recorded with amount_paid 0.",
        REPAYMENTS,
        (Measure("missed_instalments", "missed instalments", "SUM(CASE WHEN r.amount_paid = 0 THEN 1 ELSE 0 END)", "count"),),
        (r"\b(?:missed|unpaid)\s+(?:loan\s+)?(?:instal?lments?|emis?|repayments?|payments?)\b", r"\b(?:instal?lments?|emis?|repayments?)\s+(?:were\s+|are\s+|was\s+)?(?:missed|unpaid)\b"),
        ("amount_paid",), "COUNT where amount_paid = 0", "Dated by due_date, from 2024-01-31 to 2026-03-15.",
        data_quality=_REPAYMENT_QUALITY, example_question="How many instalments were missed by month?",
    ),
    Metric(
        "repayment_due", "Repayment amount due",
        "Sum of instalment amounts due, in INR, by due date.",
        REPAYMENTS,
        (Measure("amount_due", "amount due", "SUM(r.emi_due)", "inr"),),
        (r"\b(?:emi|repayments?|instal?lments?)\s+(?:amount\s+)?due\b", r"\bamount\s+due\b"),
        ("emi_due",), "SUM(emi_due)", "Dated by due_date, from 2024-01-31 to 2026-03-15.",
        forecastable=True, data_quality=_REPAYMENT_QUALITY, example_question="Forecast the EMI due for next month",
    ),
    Metric(
        "repayment_collected", "Repayment amount collected",
        "Sum of instalment amounts paid, in INR, by due date.",
        REPAYMENTS,
        (Measure("amount_collected", "amount collected", "SUM(r.amount_paid)", "inr"), Measure("instalments", "instalments", "COUNT(*)", "count")),
        (r"\b(?:(?:loan|emi)\s+)?repayments?(?:\s+(?:amount|collections?|collected))?\b", r"\b(?:amount\s+)?collected\b", r"\b(?:(?:loan|emi)\s+)?collections?\b", r"\bamount\s+paid\b"),
        ("amount_paid",), "SUM(amount_paid)", "Dated by due_date, from 2024-01-31 to 2026-03-15.",
        forecastable=True, data_quality=_REPAYMENT_QUALITY, example_question="Show the monthly trend of loan repayments",
    ),
    Metric(
        "average_interest_rate", "Average interest rate",
        "Mean annual interest rate across loans, unweighted by principal.",
        LOANS,
        (Measure("average_interest_rate", "average interest rate", "ROUND(AVG(l.interest_rate), 2)", "percent"),),
        (r"\b(?:average|avg|mean)\s+(?P<gap>.*?)\binterest\s+rates?\b", r"\binterest\s+rates?\b"),
        ("interest_rate",), "AVG(interest_rate)", "Dated by disbursement_date, from 2024-01-01 to 2025-02-04.", example_question="What is the average interest rate by loan type?",
    ),
    Metric(
        "loans_disbursed", "Loans disbursed",
        "Number and principal of loans by disbursement date, in INR.",
        LOANS,
        (Measure("loans_disbursed", "loans", "COUNT(*)", "count"), Measure("principal_disbursed", "principal", "SUM(l.principal_amount)", "inr")),
        (r"\bloans?\s+disburse(?:d|ments?)\b", r"\bdisburse(?:d|ments?)\b", r"\b(?:how\s+many|number\s+of|count\s+of|total)\s+(?P<gap>.*?)\bloans?\b", r"\bloans?\b", r"\bprincipal\b"),
        ("loan_id", "principal_amount"), "COUNT(*), SUM(principal_amount)", "Dated by disbursement_date, from 2024-01-01 to 2025-02-04.",
        forecastable=True, data_quality=("All 2000 loans have status ACTIVE, so loan status has no analytical value.", "Disbursements stop at 2025-02-04, 14 months of history."), example_question="How many loans were disbursed by loan type?",
    ),
    Metric(
        "total_credit_limit", "Total credit limit",
        "Sum of card credit limits, in INR, across all card statuses unless one is asked for.",
        CARDS,
        (Measure("total_credit_limit", "total credit limit", "SUM(c.credit_limit)", "inr"),),
        (r"\b(?:total\s+)?credit\s+limits?\b",),
        ("credit_limit",), "SUM(credit_limit)", _SNAPSHOT, supports_time=False, example_question="What is the total credit limit by card type?",
    ),
    Metric(
        "cards_issued", "Cards issued",
        "Number of cards by issue date, whatever their status today.",
        CARDS,
        (Measure("cards_issued", "cards issued", "COUNT(*)", "count"),),
        (r"\b(?P<gap>.*?)\bcards?\s+issued\b", r"\bnew\s+(?P<gap>.*?)\bcards?\b", r"\bcard\s+issuance\b"),
        ("card_id", "issue_date"), "COUNT(*)", "Dated by issue_date, from 2018-01-01 to 2023-06-24.",
        forecastable=True, data_quality=("Issues stop at 2023-06-24, so the series is out of date.",), example_question="How many cards were issued in 2022?",
    ),
    Metric(
        "card_count", "Number of cards",
        "Number of cards held today, in any status unless a status is asked for.",
        CARDS,
        (Measure("card_count", "cards", "COUNT(*)", "count"),),
        (r"\b(?:how\s+many|number\s+of|count\s+of|total)\s+(?P<gap>.*?)\bcards?\b", r"\bcard\s+counts?\b", r"\bcards?\b"),
        ("card_id",), "COUNT(*)", _SNAPSHOT, supports_time=False, example_question="How many blocked cards are there?",
    ),
    Metric(
        "new_customers", "New customers onboarded",
        "Number of customers by onboarding date.",
        CUSTOMERS,
        (Measure("customers_onboarded", "customers onboarded", "COUNT(*)", "count"),),
        (r"\bnew\s+(?P<gap>.*?)\bcustomers?\b", r"\bcustomers?\s+onboard(?:ed|ing)\b", r"\bonboard(?:ed|ing)\b"),
        ("customer_id", "onboarding_date"), "COUNT(*)", "Dated by onboarding_date, from 2010-01-01 to 2025-11-18.",
        forecastable=True, data_quality=("15 customers have no customer_id and are still counted.",), example_question="Show the monthly trend of new customers onboarded",
    ),
    Metric(
        "customer_count", "Number of customers",
        "Number of customer records today.",
        CUSTOMERS,
        (Measure("customer_count", "customers", "COUNT(*)", "count"),),
        (r"\b(?:how\s+many|number\s+of|count\s+of|total)\s+(?P<gap>.*?)\bcustomers?\b", r"\bcustomer\s+counts?\b", r"\bcustomers?\b"),
        ("customer_id",), "COUNT(*)", _SNAPSHOT, supports_time=False, data_quality=("15 customers have no customer_id and are still counted.",), example_question="How many customers are in each segment?",
    ),
)
_BY_ID = {metric.id: metric for metric in METRICS}


def metric_by_id(metric_id: str) -> Metric | None:
    """A metric of the registry in use; banking unless another domain's workflow set its own."""
    registry = _active.get()
    return _BY_ID.get(metric_id) if registry is BANKING else registry.metric(metric_id)


# Named so the refusal can say what is missing; other unknown questions get the supported list.
UNSUPPORTED_TOPICS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), reason)
    for pattern, reason in (
        (r"\b(?:npa|npl|non[-\s]?performing|delinquen\w+|default\s+rate|defaults?)\b", "Non-performing loan and default rates are not available: every loan is recorded as ACTIVE and no default or days-past-due classification exists. Missed instalments are available."),
        (r"\b(?:profit\w*|net\s+interest\s+(?:margin|income)|nim|revenue|income|roa|roe|cost\s+to\s+income)\b", "Profitability and income figures are not available: banking.gold holds no general ledger, income or cost data."),
        (r"\bchurn\w*\b", "Churn is not available: no account closure date or customer history is recorded, only each account's current status."),
        (r"\bcredit\s+scores?\b|\bcibil\b", "Credit scores are not available: no credit score column exists in banking.gold."),
        (r"\bfraud\w*\b", "Fraud figures are not available: no fraud label or case data exists in banking.gold. Data-quality anomaly checks are available."),
        (r"\b(?:deposit|balance)\s+(?:growth|history|trend)\b|\bbalance\s+(?:over\s+time|by\s+month|last\s+(?:month|year))\b|\bmonthly\s+(?:\w+\s+)?balances?\b", "Balance history is not available: banking.gold holds only each account's current balance, with no dated balances."),
        (r"\bloss\s+ratio\b|\b(?:insurance|written)\s+premiums?\b|\bpremiums\b|\bpolic(?:y|ies)\b|\bclaims?\b", "Insurance measures are not part of the banking domain."),
    )
)


def values_named_in(question: str) -> list[str]:
    """Known filter values a question names, for checking that generated SQL kept them."""
    lowered = question.lower()
    found: list[str] = []
    for metric in METRICS:
        for dimension in metric.source.dimensions:
            for value in dimension.values:
                if re.search(rf"(?<![a-z0-9-]){re.escape(value.lower())}s?(?![a-z0-9-])", lowered) and value not in found:
                    found.append(value)
    return found


def supported_summary() -> str:
    return ", ".join(metric.label.lower() for metric in METRICS)


@dataclass(frozen=True)
class DomainRegistry:
    """Everything the planner, compiler and agent need to know about one domain's approved data."""

    domain: str
    label: str
    catalog_schema: str
    version: str
    metrics: tuple[Metric, ...]
    approved_tables: frozenset[str]
    unsupported_topics: tuple[tuple[re.Pattern[str], str], ...] = ()
    # Words naming what each source counts, by source id; banking keeps its own list in the planner.
    subject_words: dict | None = None
    # Banking answers or refuses everything it reads. The other registries leave what they cannot
    # represent cleanly to the existing path, so no question answered before stops being answered.
    fallthrough: bool = True

    def metric(self, metric_id: str) -> Metric | None:
        return next((metric for metric in self.metrics if metric.id == metric_id), None)

    def supported_summary(self) -> str:
        return ", ".join(metric.label.lower() for metric in self.metrics)


BANKING = DomainRegistry(
    domain="banking",
    label="banking",
    catalog_schema=CATALOG_SCHEMA,
    version=REGISTRY_VERSION,
    metrics=METRICS,
    approved_tables=APPROVED_TABLES,
    unsupported_topics=UNSUPPORTED_TOPICS,
    fallthrough=False,
)
_active: ContextVar[DomainRegistry] = ContextVar("ryze_metric_registry", default=BANKING)


def active_registry() -> DomainRegistry:
    return _active.get()


@contextmanager
def use_registry(registry: DomainRegistry) -> Iterator[None]:
    token = _active.set(registry)
    try:
        yield
    finally:
        _active.reset(token)
