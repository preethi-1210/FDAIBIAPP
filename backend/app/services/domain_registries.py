"""Approved metric registries for finance, insurance, retail and healthcare, verified against each gold
schema on 2026-11-10 through the application's own Databricks connection.

The banking workflow's planner, compiler and checks run over these unchanged. A question these
registries cannot represent cleanly is left to the existing path, so nothing answered before is lost.
Definitions reuse the ones the dashboards and reports already use; none are new business rules.
"""

import re

from .banking_registry import Dimension, DomainRegistry, Measure, Metric, Source
from .metrics import LOSS_RATIO_LIMITATION
from .reports import RETAIL_NET_SALES

_CITIES = ("Ahmedabad", "Bengaluru", "Chennai", "Delhi", "Hyderabad", "Kolkata", "Mumbai", "Pune")
_STATES = ("Delhi", "Gujarat", "Karnataka", "Maharashtra", "Tamil Nadu", "Telangana", "West Bengal")
_BANGALORE = (("bangalore", "Bengaluru"),)
# "Total" is left out: in "total premium for health policies" it would turn an amount into a count.
_HOW_MANY = r"\b(?:how\s+many|number\s+of|count\s+of|total\s+number\s+of)\s+(?P<gap>.*?)"


def _topics(*pairs: tuple[str, str]) -> tuple[tuple[re.Pattern[str], str], ...]:
    return tuple((re.compile(pattern, re.IGNORECASE), reason) for pattern, reason in pairs)


# Finance -------------------------------------------------------------------------------------------

_FIN_CLIENT = "LEFT JOIN gold_clients cl ON cl.client_id = a.client_id"
_FIN_SECURITY = "LEFT JOIN gold_securities s ON s.security_id = t.security_id"

FIN_ACCOUNTS = Source(
    "accounts", "gold_accounts a", ("gold_accounts", "gold_clients"), "a.open_date", "account opening date",
    (
        Dimension("account_type", "account type", "a.account_type", (r"account\s+types?", r"types?(?:\s+of\s+accounts?)?"), ("DEMAT", "TRADING", "MF"), (("mutual fund", "MF"),)),
        Dimension("account_status", "account status", "a.status", (r"(?:account\s+)?status(?:es)?",), ("ACTIVE", "DORMANT", "CLOSED"), (("active-account", "ACTIVE"),)),
        Dimension("risk_profile", "client risk profile", "cl.risk_profile", (r"risk\s+profiles?",), ("CONSERVATIVE", "MODERATE", "AGGRESSIVE"), joins=(_FIN_CLIENT,)),
        Dimension("kyc_status", "client KYC status", "cl.kyc_status", (r"kyc(?:\s+status(?:es)?)?",), ("VERIFIED", "PENDING", "REJECTED"), joins=(_FIN_CLIENT,)),
        Dimension("city", "client city", "cl.city", (r"cit(?:y|ies)", r"locations?"), _CITIES, _BANGALORE, joins=(_FIN_CLIENT,)),
    ),
)
FIN_TRADES = Source(
    "trades", "gold_trades t", ("gold_trades", "gold_securities"), "t.trade_timestamp", "trade date",
    (
        Dimension("side", "trade side", "t.side", (r"(?:trade\s+)?sides?", r"buy\s+(?:or|and|vs\.?)\s+sell"), ("BUY", "SELL")),
        Dimension("trade_status", "trade status", "t.status", (r"(?:trade\s+)?status(?:es)?",), ("EXECUTED", "CANCELLED", "REJECTED")),
        Dimension("asset_class", "asset class", "s.asset_class", (r"asset\s+class(?:es)?",), ("EQUITY", "ETF", "BOND", "MF"), (("equities", "EQUITY"), ("bonds", "BOND"), ("etfs", "ETF")), joins=(_FIN_SECURITY,)),
        Dimension("sector", "security sector", "s.sector", (r"sectors?",), ("AUTO", "CONSUMER", "ENERGY", "FINANCIALS", "HEALTHCARE", "MATERIALS", "TECHNOLOGY"), joins=(_FIN_SECURITY,)),
        Dimension("exchange", "exchange", "s.exchange", (r"exchanges?",), ("NSE", "BSE"), joins=(_FIN_SECURITY,)),
        # Breakdown only: 60 symbols, so no value is read from wording. Trades without a matching
        # security appear as an empty symbol rather than disappearing.
        Dimension("security", "security symbol", "s.symbol", (r"securit(?:y|ies)", r"symbols?", r"stocks?", r"instruments?"), (), joins=(_FIN_SECURITY,)),
    ),
)
FIN_CLIENTS = Source(
    "clients", "gold_clients cl", ("gold_clients",), "cl.onboarding_date", "client onboarding date",
    (
        Dimension("risk_profile", "risk profile", "cl.risk_profile", (r"risk\s+profiles?",), ("CONSERVATIVE", "MODERATE", "AGGRESSIVE")),
        Dimension("kyc_status", "KYC status", "cl.kyc_status", (r"kyc(?:\s+status(?:es)?)?",), ("VERIFIED", "PENDING", "REJECTED")),
        Dimension("city", "client city", "cl.city", (r"cit(?:y|ies)", r"locations?"), _CITIES, _BANGALORE),
    ),
)
_FIN_SNAPSHOT = "Current state at the last pipeline load; finance.gold holds no balance history."
_FIN_TRADE_QUALITY = (
    "40 trades have a negative quantity and are included as recorded.",
    "30 trades reference a security that is not in gold_securities; they appear without a sector or asset class.",
)

FINANCE = DomainRegistry(
    domain="finance",
    label="finance",
    catalog_schema="finance.gold",
    version="1.0.0",
    approved_tables=frozenset({"gold_accounts", "gold_clients", "gold_trades", "gold_securities"}),
    unsupported_topics=_topics(
        (r"\baum\b|\bassets\s+under\s+management\b|\bmarket\s+value\b|\bholdings?\b", "Assets under management and market value are not available: finance.gold records account cash balances and trades, not holdings valued at market prices."),
        (r"\b(?:portfolio\s+)?returns?\b|\bp&l\b|\bpnl\b|\bprofit\w*\b|\bgains?\b", "Returns and profit are not available: no cost basis, valuation or realised-gain data exists in finance.gold."),
    ),
    subject_words={
        "accounts": {"account", "client"},
        "trades": {"trade", "trading", "traded"},
        "clients": {"client"},
    },
    metrics=(
        Metric(
            "total_cash_balance", "Total cash balance",
            "Sum of cash_balance across accounts, every status included unless a status is named.",
            FIN_ACCOUNTS,
            (Measure("total_cash_balance", "total cash balance", "ROUND(SUM(a.cash_balance), 2)", "inr"),),
            (r"\b(?:total\s+)?cash\s+balances?(?:\s+across\s+(?:all\s+)?accounts)?\b", r"\baccount\s+balances?\b"),
            ("cash_balance",), "SUM(cash_balance)", _FIN_SNAPSHOT, supports_time=False,
            example_question="What is the total cash balance by account type?",
        ),
        Metric(
            "account_count", "Number of accounts",
            "Number of accounts today, every status included unless a status is named.",
            FIN_ACCOUNTS,
            (Measure("account_count", "accounts", "COUNT(*)", "count"),),
            (_HOW_MANY + r"\baccounts?\b(?!\s+opened)", r"\baccount\s+counts?\b"),
            ("account_id",), "COUNT(*)", _FIN_SNAPSHOT, supports_time=False,
            example_question="How many accounts are there by status?",
        ),
        Metric(
            "accounts_opened", "Accounts opened",
            "Number of accounts by the date they opened, whatever their status today.",
            FIN_ACCOUNTS,
            (Measure("accounts_opened", "accounts opened", "COUNT(*)", "count"),),
            (r"\baccounts?\s+opened\b", r"\baccount\s+openings?\b", r"\bnew\s+(?P<gap>.*?)\baccounts?\b"),
            ("account_id", "open_date"), "COUNT(*)", "Dated by open_date, from 2015-01-03 to 2026-01-15.",
            forecastable=True, example_question="Show the monthly trend of accounts opened",
        ),
        Metric(
            "trade_count", "Number of trades",
            "Number of trades, every status included unless a status is named.",
            FIN_TRADES,
            (Measure("trade_count", "trades", "COUNT(*)", "count"),),
            (_HOW_MANY + r"\btrades?\b", r"\btrade\s+counts?\b", r"\btrading\s+activity\b", r"\btrades?\b"),
            ("trade_id",), "COUNT(*)", "Dated by trade_timestamp, from 2026-01-02 to 2026-03-13.",
            data_quality=_FIN_TRADE_QUALITY, example_question="How many trades were executed by sector?",
        ),
        Metric(
            "gross_trade_value", "Gross trade value",
            "Sum of price multiplied by quantity, every trade status included unless a status is named.",
            FIN_TRADES,
            (Measure("gross_trade_value", "gross trade value", "ROUND(SUM(t.price * t.quantity), 2)", "inr"),),
            (r"\bgross\s+trade\s+values?\b", r"\btrade\s+values?\b", r"\btraded\s+values?\b", r"\b(?:total\s+)?values?\s+of\s+(?P<gap>.*?)\btrades?\b"),
            ("price", "quantity"), "SUM(price * quantity)", "Dated by trade_timestamp, from 2026-01-02 to 2026-03-13.",
            data_quality=_FIN_TRADE_QUALITY, example_question="What is the gross trade value by asset class?",
        ),
        Metric(
            "client_count", "Number of clients",
            "Number of client records today.",
            FIN_CLIENTS,
            (Measure("client_count", "clients", "COUNT(*)", "count"),),
            (_HOW_MANY + r"\bclients?\b", r"\bclient\s+counts?\b"),
            ("client_id",), "COUNT(*)", _FIN_SNAPSHOT, supports_time=False,
            example_question="How many clients are there by risk profile?",
        ),
        Metric(
            "new_clients", "New clients onboarded",
            "Number of clients by onboarding date.",
            FIN_CLIENTS,
            (Measure("clients_onboarded", "clients onboarded", "COUNT(*)", "count"),),
            (r"\bnew\s+(?P<gap>.*?)\bclients?\b", r"\bclients?\s+onboard(?:ed|ing)\b"),
            ("client_id", "onboarding_date"), "COUNT(*)", "Dated by onboarding_date, from 2015-01-01 to 2026-01-31.",
            forecastable=True, example_question="Show the monthly trend of new clients",
        ),
    ),
)

# Insurance -----------------------------------------------------------------------------------------

_INS_HOLDER = "LEFT JOIN gold_policyholders h ON h.holder_id = p.holder_id"
_INS_AGENT = "LEFT JOIN gold_agents ag ON ag.agent_id = p.agent_id"
_INS_POLICY_FROM_CLAIM = "LEFT JOIN gold_policies p ON p.policy_id = c.policy_id"
_INS_POLICY_FROM_PAYMENT = "LEFT JOIN gold_policies p ON p.policy_id = pp.policy_id"
_POLICY_TYPES = ("HEALTH", "MOTOR", "LIFE", "HOME")
_POLICY_TYPE_ALIASES = (r"policy\s+types?", r"types?(?:\s+of\s+polic(?:y|ies))?", r"products?", r"lines?(?:\s+of\s+business)?")

INS_POLICIES = Source(
    "policies", "gold_policies p", ("gold_policies", "gold_policyholders", "gold_agents"), "p.start_date", "policy start date",
    (
        Dimension("policy_type", "policy type", "p.policy_type", _POLICY_TYPE_ALIASES, _POLICY_TYPES),
        Dimension("policy_status", "policy status", "p.policy_status", (r"(?:policy\s+)?status(?:es)?",), ("ACTIVE", "EXPIRED", "LAPSED", "CANCELLED")),
        # "Monthly" also asks for a monthly trend, so only the other two are read from wording.
        Dimension("payment_frequency", "payment frequency", "p.payment_frequency", (r"(?:payment|premium)\s+frequenc(?:y|ies)",), ("ANNUAL", "MONTHLY", "QUARTERLY"), (("annual", "ANNUAL"), ("quarterly", "QUARTERLY")), match_values=False),
        Dimension("state", "policyholder state", "h.state", (r"states?",), _STATES, joins=(_INS_HOLDER,)),
        Dimension("city", "policyholder city", "h.city", (r"cit(?:y|ies)", r"locations?"), _CITIES, _BANGALORE, joins=(_INS_HOLDER,)),
        Dimension("occupation", "policyholder occupation", "h.occupation", (r"occupations?",), ("BUSINESS", "RETIRED", "SALARIED", "SELF-EMPLOYED", "STUDENT"), (("self employed", "SELF-EMPLOYED"),), joins=(_INS_HOLDER,)),
        Dimension("gender", "policyholder gender", "h.gender", (r"genders?",), (), joins=(_INS_HOLDER,)),
        Dimension("agent_city", "agent branch city", "ag.branch_city", (r"agent\s+(?:branch\s+)?cit(?:y|ies)", r"branch\s+cit(?:y|ies)"), _CITIES, joins=(_INS_AGENT,)),
    ),
)
INS_CLAIMS = Source(
    "claims", "gold_claims c", ("gold_claims", "gold_policies", "gold_policyholders"), "c.claim_date", "claim date",
    (
        Dimension("claim_type", "claim type", "c.claim_type", (r"claim\s+types?", r"types?(?:\s+of\s+claims?)?"), ("HOSPITALISATION", "ACCIDENT", "FIRE/THEFT"), (("hospitalization", "HOSPITALISATION"), ("fire", "FIRE/THEFT"), ("theft", "FIRE/THEFT"))),
        Dimension("claim_status", "claim status", "c.claim_status", (r"(?:claim\s+)?status(?:es)?",), ("SUBMITTED", "APPROVED", "PAID", "REJECTED")),
        Dimension("policy_type", "policy type", "p.policy_type", _POLICY_TYPE_ALIASES, _POLICY_TYPES, joins=(_INS_POLICY_FROM_CLAIM,)),
        Dimension("state", "policyholder state", "h.state", (r"states?",), _STATES, joins=(_INS_POLICY_FROM_CLAIM, _INS_HOLDER)),
    ),
)
INS_PAYMENTS = Source(
    "premium_payments", "gold_premium_payments pp", ("gold_premium_payments", "gold_policies"), "pp.due_date", "premium due date",
    (
        Dimension("payment_mode", "payment mode", "pp.mode", (r"(?:payment\s+)?modes?", r"payment\s+methods?"), ("NETBANKING", "AUTO-DEBIT", "UPI", "CARD"), (("net banking", "NETBANKING"), ("auto debit", "AUTO-DEBIT"))),
        Dimension("policy_type", "policy type", "p.policy_type", _POLICY_TYPE_ALIASES, _POLICY_TYPES, joins=(_INS_POLICY_FROM_PAYMENT,)),
    ),
)
_POLICY_DATES = "Dated by policy start_date, from 2023-01-01 to 2026-02-01."
_CLAIM_DATES = "Dated by claim_date, from 2023-03-17 to 2026-03-26."
_CLAIM_QUALITY = ("25 claims have a negative claimed amount and are included as recorded.", "21 claims reference a policy that is not in gold_policies.")

INSURANCE = DomainRegistry(
    domain="insurance",
    label="insurance",
    catalog_schema="insurance.gold",
    version="1.0.0",
    approved_tables=frozenset({"gold_policies", "gold_policyholders", "gold_agents", "gold_claims", "gold_premium_payments"}),
    unsupported_topics=_topics(
        (r"\bloss\s+ratios?\b|\bcombined\s+ratios?\b", LOSS_RATIO_LIMITATION),
        (r"\bearned\s+premiums?\b|\breserves?\b|\bibnr\b", "Earned premium and reserves are not available: insurance.gold records written premium, payments and claims only."),
    ),
    subject_words={
        "policies": {"policy", "policies", "premium", "premiums", "policyholder"},
        "claims": {"claim", "claims"},
        "premium_payments": {"premium", "premiums", "payment", "payments", "collection", "collections"},
    },
    metrics=(
        Metric(
            "written_premium", "Written premium",
            "Sum of premium_amount of policies that are not cancelled, by policy start date.",
            INS_POLICIES,
            (Measure("written_premium", "written premium", "ROUND(SUM(p.premium_amount), 2)", "amount"),),
            (r"\b(?:total\s+|gross\s+)?written\s+premiums?\b", r"\b(?:total\s+)?premiums?(?:\s+amounts?)?\b"),
            ("premium_amount", "policy_status", "start_date"), "SUM(premium_amount)", _POLICY_DATES,
            required_filter="upper(p.policy_status) != 'CANCELLED'", forecastable=True,
            data_quality=("20 policies have a negative premium_amount and are included as recorded.",),
            example_question="What is the written premium by policy type?",
        ),
        Metric(
            "policy_count", "Number of policies",
            "Number of policies by start date, every status included unless a status is named.",
            INS_POLICIES,
            (Measure("policy_count", "policies", "COUNT(*)", "count"),),
            (_HOW_MANY + r"\bpolic(?:y|ies)\b", r"\bpolicy\s+counts?\b", r"\bpolicies\s+(?:sold|issued|written)\b", r"\bnew\s+(?P<gap>.*?)\bpolic(?:y|ies)\b"),
            ("policy_id", "start_date"), "COUNT(*)", _POLICY_DATES, forecastable=True,
            example_question="How many policies are there by status?",
        ),
        Metric(
            "claim_count", "Number of claims",
            "Number of claims by claim date, every status included unless a status is named.",
            INS_CLAIMS,
            (Measure("claim_count", "claims", "COUNT(*)", "count"),),
            (_HOW_MANY + r"\bclaims?\b", r"\bclaim\s+counts?\b", r"\bclaims?\s+(?:volume|activity)\b"),
            ("claim_id", "claim_date"), "COUNT(*)", _CLAIM_DATES, forecastable=True, data_quality=_CLAIM_QUALITY,
            example_question="How many claims are there by status?",
        ),
        Metric(
            "claimed_amount", "Claimed amount",
            "Sum of claimed_amount by claim date, every status included unless a status is named.",
            INS_CLAIMS,
            (Measure("claimed_amount", "claimed amount", "ROUND(SUM(c.claimed_amount), 2)", "amount"),),
            (r"\b(?:total\s+)?claimed\s+amounts?\b", r"\bamounts?\s+claimed\b"),
            ("claimed_amount", "claim_date"), "SUM(claimed_amount)", _CLAIM_DATES, data_quality=_CLAIM_QUALITY,
            example_question="What is the claimed amount by claim type?",
        ),
        Metric(
            "approved_claim_amount", "Approved claim amount",
            "Sum of approved_amount of claims with status APPROVED or PAID, by claim date.",
            INS_CLAIMS,
            (Measure("approved_claim_amount", "approved claim amount", "ROUND(SUM(c.approved_amount), 2)", "amount"),),
            (r"\b(?:total\s+)?(?:approved|paid)\s+claims?(?:\s+amounts?)?\b", r"\bclaims?\s+paid\b"),
            ("approved_amount", "claim_status", "claim_date"), "SUM(approved_amount)", _CLAIM_DATES,
            required_filter="upper(c.claim_status) IN ('APPROVED', 'PAID')", data_quality=_CLAIM_QUALITY,
            example_question="What is the approved claim amount by policy type?",
        ),
        Metric(
            "premium_collected", "Premium collected",
            "Sum of amount_paid on premium instalments, by due date.",
            INS_PAYMENTS,
            (Measure("premium_collected", "premium collected", "ROUND(SUM(pp.amount_paid), 2)", "amount"), Measure("premium_due", "premium due", "ROUND(SUM(pp.amount_due), 2)", "amount")),
            (r"\bpremiums?\s+(?:(?:was|were|is|are|has\s+been|have\s+been)\s+)?(?:collected|collections?|received|paid)\b", r"\bcollected\s+premiums?\b"),
            ("amount_paid", "amount_due", "due_date"), "SUM(amount_paid)", "Dated by due_date, from 2023-01-01 to 2026-03-15.",
            example_question="How much premium was collected by payment mode?",
        ),
        Metric(
            "premium_collection_rate", "Premium collection rate",
            "Amount paid as a share of the amount due, for instalments due in the period.",
            INS_PAYMENTS,
            (Measure("collection_rate_pct", "collection rate", "ROUND(100.0 * SUM(pp.amount_paid) / NULLIF(SUM(pp.amount_due), 0), 2)", "percent"),),
            (r"\b(?:premium\s+)?collection\s+rates?\b", r"\bcollection\s+efficiency\b"),
            ("amount_paid", "amount_due"), "SUM(amount_paid) / SUM(amount_due)", "Dated by due_date, from 2023-01-01 to 2026-03-15.",
            example_question="What is the premium collection rate by policy type?",
        ),
    ),
)

# Retail --------------------------------------------------------------------------------------------

_RET_ORDER_FROM_ITEM = "JOIN gold_orders o ON o.order_id = i.order_id"
_RET_PRODUCT = "LEFT JOIN gold_products p ON p.product_id = i.product_id"
_RET_STORE = "LEFT JOIN gold_stores st ON st.store_id = o.store_id"
_RET_CUSTOMER = "LEFT JOIN gold_customers cu ON cu.customer_id = o.customer_id"
_REGIONS = ("NORTH", "EAST", "WEST", "SOUTH")
_LOYALTY = ("SILVER", "GOLD", "PLATINUM")
# "Store" is also a word for a shop, so the STORE channel is only read from explicit wording.
_CHANNEL = Dimension("channel", "sales channel", "o.channel", (r"(?:sales\s+)?channels?",), ("ONLINE", "STORE", "APP"), (("online", "ONLINE"), ("app", "APP"), ("in-store", "STORE"), ("store channel", "STORE")), match_values=False)
_ORDER_STATUS = Dimension("order_status", "order status", "o.order_status", (r"(?:order\s+)?status(?:es)?",), ("COMPLETED", "RETURNED", "CANCELLED"))


def _store_dimensions(*joins: str) -> tuple[Dimension, ...]:
    return (
        Dimension("region", "store region", "st.region", (r"(?:store\s+)?regions?", r"zones?"), _REGIONS, joins=joins),
        Dimension("state", "store state", "st.state", (r"(?:store\s+)?states?",), _STATES, joins=joins),
        Dimension("city", "store city", "st.city", (r"(?:store\s+)?cit(?:y|ies)", r"locations?"), _CITIES, _BANGALORE, joins=joins),
    )


RET_SALES = Source(
    "sales", f"gold_order_items i {_RET_ORDER_FROM_ITEM}", ("gold_order_items", "gold_orders", "gold_products", "gold_stores", "gold_customers"), "o.order_date", "order date",
    (
        _CHANNEL,
        _ORDER_STATUS,
        Dimension("category", "product category", "p.category", (r"(?:product\s+)?categor(?:y|ies)",), ("APPAREL", "BEAUTY", "ELECTRONICS", "GROCERY", "HOME", "TOYS"), joins=(_RET_PRODUCT,)),
        *_store_dimensions(_RET_STORE),
        Dimension("loyalty_tier", "customer loyalty tier", "cu.loyalty_tier", (r"loyalty\s+tiers?", r"tiers?"), _LOYALTY, joins=(_RET_CUSTOMER,)),
    ),
)
RET_ORDERS = Source(
    "orders", "gold_orders o", ("gold_orders", "gold_stores", "gold_customers"), "o.order_date", "order date",
    (_CHANNEL, _ORDER_STATUS, *_store_dimensions(_RET_STORE), Dimension("loyalty_tier", "customer loyalty tier", "cu.loyalty_tier", (r"loyalty\s+tiers?", r"tiers?"), _LOYALTY, joins=(_RET_CUSTOMER,))),
)
RET_RETURNS = Source(
    "returns", "gold_returns r", ("gold_returns", "gold_order_items", "gold_products"), "r.return_date", "return date",
    (
        Dimension("reason", "return reason", "r.reason", (r"(?:return\s+)?reasons?",), ("WRONG ITEM", "DEFECTIVE", "SIZE ISSUE", "CHANGED MIND", "NOT AS DESCRIBED")),
        Dimension("category", "product category", "p.category", (r"(?:product\s+)?categor(?:y|ies)",), ("APPAREL", "BEAUTY", "ELECTRONICS", "GROCERY", "HOME", "TOYS"), joins=("LEFT JOIN gold_order_items i ON i.order_item_id = r.order_item_id", _RET_PRODUCT)),
    ),
)
RET_CUSTOMERS = Source(
    "customers", "gold_customers cu", ("gold_customers",), "cu.signup_date", "customer signup date",
    (
        Dimension("loyalty_tier", "loyalty tier", "cu.loyalty_tier", (r"loyalty\s+tiers?", r"tiers?"), _LOYALTY),
        Dimension("city", "customer city", "cu.city", (r"cit(?:y|ies)", r"locations?"), _CITIES, _BANGALORE),
        Dimension("state", "customer state", "cu.state", (r"states?",), _STATES),
        Dimension("gender", "customer gender", "cu.gender", (r"genders?",), ()),
    ),
)
# The same exclusions as the Retail report, where this definition comes from.
_NET_SALES_FILTER = "upper(o.order_status) != 'CANCELLED' AND o.order_date <= current_timestamp()"
_ORDER_DATES = "Dated by order_date, from 2025-10-01; orders dated after today are excluded (some rows are dated up to 2030)."
_FUTURE = "o.order_date <= current_timestamp()"

RETAIL = DomainRegistry(
    domain="retail",
    label="retail",
    catalog_schema="retail.gold",
    version="1.0.0",
    approved_tables=frozenset({"gold_order_items", "gold_orders", "gold_products", "gold_stores", "gold_customers", "gold_returns"}),
    unsupported_topics=_topics(
        (r"\b(?:gross\s+)?(?:profit|margins?)\b", "Profit and margin are not reported: gold_products has a cost_price but no agreed cost or margin definition has been approved."),
    ),
    subject_words={
        # "Store" names where a sale happened; the STORE channel is only read from explicit wording.
        "sales": {"sale", "sales", "order", "orders", "product", "products", "item", "items", "store", "stores", "shop", "shops"},
        "orders": {"order", "orders", "store", "stores", "shop", "shops"},
        "returns": {"return", "returns", "refund", "refunds"},
        "customers": {"customer", "customers"},
    },
    metrics=(
        Metric(
            "net_sales", "Net sales",
            "Quantity times unit price, less the discount (capped at 100%), for orders that are not cancelled. Orders dated after today are excluded.",
            RET_SALES,
            (Measure("net_sales", "net sales", f"ROUND(SUM({RETAIL_NET_SALES}), 2)", "amount"),),
            (r"\b(?:total\s+|net\s+)?(?:sales|revenue)(?:\s+amounts?)?\b", r"\bturnover\b"),
            ("quantity", "unit_price", "discount_pct", "order_status", "order_date"), f"SUM({RETAIL_NET_SALES})", _ORDER_DATES,
            required_filter=_NET_SALES_FILTER,
            data_quality=("60 order items have a negative quantity and are included as recorded.", "Discounts above 100% are capped at 100%."),
            example_question="What are net sales by product category?",
        ),
        Metric(
            "units_sold", "Units sold",
            "Sum of item quantity for orders that are not cancelled. Orders dated after today are excluded.",
            RET_SALES,
            (Measure("units_sold", "units sold", "SUM(i.quantity)", "count"),),
            (r"\bunits?\s+sold\b", r"\b(?:quantity|units)\s+(?:sold|ordered)\b", r"\bitems\s+sold\b"),
            ("quantity", "order_status", "order_date"), "SUM(quantity)", _ORDER_DATES,
            required_filter=_NET_SALES_FILTER,
            data_quality=("60 order items have a negative quantity and are included as recorded.",),
            example_question="How many units were sold by category?",
        ),
        Metric(
            "order_count", "Number of orders",
            "Number of orders, every status included unless a status is named. Orders dated after today are excluded.",
            RET_ORDERS,
            (Measure("order_count", "orders", "COUNT(*)", "count"),),
            (_HOW_MANY + r"\borders?\b", r"\border\s+(?:counts?|volume)\b"),
            ("order_id", "order_date"), "COUNT(*)", _ORDER_DATES, required_filter=_FUTURE,
            example_question="How many orders are there by channel?",
        ),
        Metric(
            "refund_amount", "Refund amount",
            "Sum of refund_amount on returns, by return date.",
            RET_RETURNS,
            (Measure("refund_amount", "refund amount", "ROUND(SUM(r.refund_amount), 2)", "amount"),),
            (r"\b(?:total\s+)?refunds?(?:\s+amounts?)?\b", r"\bamounts?\s+refunded\b"),
            ("refund_amount", "return_date"), "SUM(refund_amount)", "Dated by return_date, from 2020-01-01 to 2026-03-28.",
            example_question="What is the refund amount by return reason?",
        ),
        Metric(
            "return_count", "Number of returns",
            "Number of returned items, by return date.",
            RET_RETURNS,
            (Measure("return_count", "returns", "COUNT(*)", "count"),),
            (_HOW_MANY + r"\breturns?\b", r"\breturn\s+counts?\b", r"\breturns?\b"),
            ("return_id", "return_date"), "COUNT(*)", "Dated by return_date, from 2020-01-01 to 2026-03-28.",
            example_question="How many returns are there by reason?",
        ),
        Metric(
            "customer_count", "Number of customers",
            "Number of customer records today.",
            RET_CUSTOMERS,
            (Measure("customer_count", "customers", "COUNT(*)", "count"),),
            (_HOW_MANY + r"\bcustomers?\b", r"\bcustomer\s+counts?\b"),
            ("customer_id",), "COUNT(*)", "Current state at the last pipeline load.", supports_time=False,
            example_question="How many customers are there by loyalty tier?",
        ),
        Metric(
            "new_customers", "New customers",
            "Number of customers by signup date.",
            RET_CUSTOMERS,
            (Measure("customers_signed_up", "customers signed up", "COUNT(*)", "count"),),
            (r"\bnew\s+(?P<gap>.*?)\bcustomers?\b", r"\bcustomers?\s+sign(?:ed)?[-\s]?ups?\b", r"\bsign[-\s]?ups?\b"),
            ("customer_id", "signup_date"), "COUNT(*)", "Dated by signup_date, from 2019-01-01 to 2026-01-30.",
            forecastable=True, example_question="Show the monthly trend of new customers",
        ),
    ),
)

# Healthcare ----------------------------------------------------------------------------------------
# Aggregates only. Queries run in the tenant's own catalog, chosen from the administrator's mapping.
# Diagnoses are not included: existing redaction masks diagnosis columns for analysts, and whether
# aggregate diagnosis figures may be shown is a compliance decision.

_HC_FACILITY = "LEFT JOIN gold_facilities f ON f.facility_id = e.facility_id"
_HC_ENCOUNTER_FROM_CLAIM = "LEFT JOIN gold_encounters e ON e.encounter_id = hc.encounter_id"
_DEPARTMENTS = ("CARDIOLOGY", "GENERAL MEDICINE", "GYNECOLOGY", "NEUROLOGY", "ONCOLOGY", "ORTHOPEDICS", "PEDIATRICS", "PULMONOLOGY")
_DEPARTMENT = Dimension("department", "department", "e.department", (r"departments?", r"specialt(?:y|ies)"), _DEPARTMENTS)
_ENCOUNTER_TYPE = Dimension("encounter_type", "encounter type", "e.encounter_type", (r"encounter\s+types?", r"types?(?:\s+of\s+(?:encounters?|visits?))?", r"visit\s+types?"), ("OPD", "IPD", "EMERGENCY"), (("outpatient", "OPD"), ("inpatient", "IPD")))


def _facility_dimensions(*joins: str) -> tuple[Dimension, ...]:
    return (
        Dimension("facility_state", "facility state", "f.state", (r"(?:facility\s+)?states?",), _STATES, joins=joins),
        Dimension("facility_city", "facility city", "f.city", (r"(?:facility\s+)?cit(?:y|ies)", r"locations?"), _CITIES, _BANGALORE, joins=joins),
        Dimension("facility_type", "facility type", "f.facility_type", (r"facility\s+types?",), ("HOSPITAL", "CLINIC"), joins=joins),
    )


HC_ENCOUNTERS = Source(
    "encounters", "gold_encounters e", ("gold_encounters", "gold_facilities"), "e.admit_date", "admission date",
    (_ENCOUNTER_TYPE, _DEPARTMENT, Dimension("discharge_status", "discharge status", "e.discharge_status", (r"discharge\s+status(?:es)?", r"outcomes?"), ("DISCHARGED", "TRANSFERRED", "DECEASED")), *_facility_dimensions(_HC_FACILITY)),
)
HC_CLAIMS = Source(
    "claims", "gold_claims hc", ("gold_claims", "gold_encounters", "gold_facilities"), "hc.claim_date", "claim date",
    (
        Dimension("insurer", "insurer", "hc.insurer", (r"insurers?", r"payers?"), ("HealthFirst", "LifeShield", "MediSure", "StarCare")),
        Dimension("claim_status", "claim status", "hc.claim_status", (r"(?:claim\s+)?status(?:es)?",), ("APPROVED", "REJECTED", "PENDING")),
        *_facility_dimensions(_HC_ENCOUNTER_FROM_CLAIM, _HC_FACILITY),
    ),
)
HC_PRESCRIPTIONS = Source(
    "prescriptions", "gold_prescriptions rx JOIN gold_encounters e ON e.encounter_id = rx.encounter_id", ("gold_prescriptions", "gold_encounters"), "e.admit_date", "admission date of the encounter",
    (_ENCOUNTER_TYPE, _DEPARTMENT),
)
HC_PATIENTS = Source(
    "patients", "gold_patients pt", ("gold_patients",), "pt.registration_date", "registration date",
    (
        Dimension("city", "patient city", "pt.city", (r"cit(?:y|ies)", r"locations?"), _CITIES, _BANGALORE),
        Dimension("state", "patient state", "pt.state", (r"states?",), _STATES),
        Dimension("gender", "patient gender", "pt.gender", (r"genders?",), ()),
    ),
)
_ADMISSIONS = "Dated by admit_date, from 2025-06-01 to 2026-03-15."

HEALTHCARE = DomainRegistry(
    domain="healthcare",
    label="healthcare",
    catalog_schema="healthcare.gold",
    version="1.0.0",
    approved_tables=frozenset({"gold_encounters", "gold_facilities", "gold_claims", "gold_prescriptions", "gold_patients"}),
    unsupported_topics=_topics(
        (r"\breadmissions?\b|\bmortality\s+rates?\b|\blength\s+of\s+stay\b|\balos\b", "That clinical measure has no approved definition yet; it needs clinical and compliance review before it can be reported."),
    ),
    subject_words={
        "encounters": {"encounter", "encounters", "visit", "visits", "admission", "admissions"},
        "claims": {"claim", "claims"},
        "prescriptions": {"prescription", "prescriptions", "medication", "medications", "drug", "drugs"},
        "patients": {"patient", "patients"},
    },
    metrics=(
        Metric(
            "encounter_count", "Number of encounters",
            "Number of encounters by admission date, every type and outcome included unless one is named.",
            HC_ENCOUNTERS,
            (Measure("encounter_count", "encounters", "COUNT(*)", "count"),),
            (_HOW_MANY + r"\b(?:encounters?|visits?|admissions?)\b", r"\bencounter\s+(?:counts?|volume)\b", r"\bencounters?\b", r"\badmissions?\b"),
            ("encounter_id", "admit_date"), "COUNT(*)", _ADMISSIONS, example_question="Show encounter volume by month",
        ),
        Metric(
            "billed_amount", "Billed claim amount",
            "Sum of billed_amount by claim date, every claim status included unless one is named.",
            HC_CLAIMS,
            (Measure("billed_amount", "billed amount", "ROUND(SUM(hc.billed_amount), 2)", "amount"),),
            (r"\b(?:total\s+)?billed(?:\s+claim)?\s+amounts?\b", r"\bamounts?\s+billed\b"),
            ("billed_amount", "claim_date"), "SUM(billed_amount)", "Dated by claim_date, from 2025-06-04 to 2026-03-29.",
            data_quality=("20 claims have a negative billed_amount and are included as recorded.",),
            example_question="What is the billed amount by insurer?",
        ),
        Metric(
            "approved_amount", "Approved claim amount",
            "Sum of approved_amount by claim date, every claim status included unless one is named, as on the dashboard.",
            HC_CLAIMS,
            (Measure("approved_amount", "approved amount", "ROUND(SUM(hc.approved_amount), 2)", "amount"),),
            (r"\b(?:total\s+)?approved(?:\s+claims?)?\s+amounts?\b", r"\bapproved\s+claims?\b"),
            ("approved_amount", "claim_date"), "SUM(approved_amount)", "Dated by claim_date, from 2025-06-04 to 2026-03-29.",
            example_question="Summarize approved claim amounts by facility region",
        ),
        Metric(
            "claim_count", "Number of claims",
            "Number of claims by claim date, every status included unless a status is named.",
            HC_CLAIMS,
            (Measure("claim_count", "claims", "COUNT(*)", "count"),),
            (_HOW_MANY + r"\bclaims?\b", r"\bclaim\s+counts?\b"),
            ("claim_id", "claim_date"), "COUNT(*)", "Dated by claim_date, from 2025-06-04 to 2026-03-29.",
            example_question="How many claims are there by status?",
        ),
        Metric(
            "prescription_count", "Number of prescriptions",
            "Number of prescriptions, dated by the admission date of their encounter.",
            HC_PRESCRIPTIONS,
            (Measure("prescription_count", "prescriptions", "COUNT(*)", "count"), Measure("prescription_cost", "prescription cost", "ROUND(SUM(rx.cost), 2)", "amount")),
            (_HOW_MANY + r"\bprescriptions?\b", r"\bprescription\s+(?:counts?|costs?|volume)\b", r"\bprescriptions?\b"),
            ("rx_id", "cost"), "COUNT(*), SUM(cost)", _ADMISSIONS, example_question="How many prescriptions are there by department?",
        ),
        Metric(
            "patient_count", "Number of patients",
            "Number of patient records today.",
            HC_PATIENTS,
            (Measure("patient_count", "patients", "COUNT(*)", "count"),),
            (_HOW_MANY + r"\bpatients?\b", r"\bpatient\s+counts?\b"),
            ("patient_id",), "COUNT(*)", "Current state at the last pipeline load.", supports_time=False,
            example_question="How many patients are there by state?",
        ),
    ),
)

REGISTRIES = {registry.domain: registry for registry in (FINANCE, INSURANCE, RETAIL, HEALTHCARE)}
