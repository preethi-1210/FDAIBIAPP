DOMAINS = ("banking", "finance", "healthcare", "insurance", "retail")

DOMAIN_LABELS = {
    "banking": "Banking",
    "finance": "Finance",
    "healthcare": "Healthcare",
    "insurance": "Insurance",
    "retail": "Retail",
}

DEMO_USERS = [
    {"username": "admin", "password": "ryze123", "domain": "all", "display_name": "Platform Admin"},
    {"username": "bank.user", "password": "banking123", "domain": "banking", "display_name": "Banking Analyst"},
    {"username": "fin.user", "password": "finance123", "domain": "finance", "display_name": "Finance Analyst"},
    {"username": "health.user", "password": "healthcare123", "domain": "healthcare", "display_name": "Healthcare Analyst"},
    {"username": "ins.user", "password": "insurance123", "domain": "insurance", "display_name": "Insurance Analyst"},
    {"username": "ret.user", "password": "retail123", "domain": "retail", "display_name": "Retail Analyst"},
]

PROMPTS = {
    "retail": [
        "How many orders did we get last week?",
        "What is revenue by region?",
        "Show monthly sales trend",
        "Which product categories sell the most?",
        "Which customers are likely to churn next month?",
        "Break down revenue by customer segment",
    ],
    "banking": [
        "What is total deposit balance by region?",
        "Show transaction volume for last week",
        "Which products have the highest balances?",
        "Which customers are likely to churn next month?",
        "Break down accounts by segment",
        "Forecast next month transaction volume",
    ],
    "finance": [
        "How many accounts are there by status?",
        "What is the total cash balance across accounts?",
        "What is the active-account cash balance?",
        "How many trades were executed?",
        "What is the gross trade value?",
        "Which securities have the highest trading activity?",
        "What dividend records are available?",
        "How do account openings vary by month?",
    ],
    "insurance": [
        "What is gross written premium by region?",
        "Show claims paid last week",
        "What is the loss ratio by product?",
        "Which policyholders are likely to churn next month?",
        "Break down premium by segment",
        "Forecast next month claim severity",
    ],
    "healthcare": [
        "How many synthetic patients are enrolled by region?",
        "Show encounter volume by month",
        "What is the encounter cancellation rate?",
        "Summarize approved claim amounts by facility region",
        "Show quality metrics by reporting period",
        "Which diagnosis categories are most common?",
    ],
}


# What the Finance terms mean in finance.gold. The glossary rows in PostgreSQL describe the local
# demo tables (orders, products), which do not exist in that catalog.
_FINANCE_GOLD_GLOSSARY = (
    ("cash balance", "gold_accounts", "cash_balance", "Cash balance recorded on each account."),
    ("account status", "gold_accounts", "status", "Status of an account. Values in the data: ACTIVE, DORMANT, CLOSED."),
    ("active-account cash balance", "gold_accounts", "cash_balance", "Sum of cash_balance for accounts with status ACTIVE."),
    ("trade", "gold_trades", "trade_id", "One record in gold_trades. Status values in the data: EXECUTED, CANCELLED, REJECTED."),
    ("gross trade value", "gold_trades", "price", "Sum of price multiplied by quantity over all trades, every status included."),
    ("security", "gold_securities", "security_id", "One record in gold_securities, identified by security_id."),
    ("dividend", "gold_dividends", "amount_per_share", "One record in gold_dividends: an amount per share for a security on an ex_date."),
    ("client", "gold_clients", "client_id", "One record in gold_clients, identified by client_id."),
)


def finance_gold_glossary() -> list[dict[str, str]]:
    return [
        {"domain": "finance", "business_term": term, "table_name": table, "column_name": column, "description": description}
        for term, table, column, description in _FINANCE_GOLD_GLOSSARY
    ]


def normalize_domain(value: str | None) -> str:
    if not value:
        return "retail"
    key = value.strip().lower()
    if key in {"all", *DOMAINS}:
        return key
    aliases = {"bank": "banking", "retail": "retail", "ins": "insurance", "fin": "finance"}
    return aliases.get(key, "retail")
