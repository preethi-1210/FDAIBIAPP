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
        "What is AUM by region?",
        "Show trade notional by month",
        "Which instruments have the largest market value?",
        "Which clients are likely to churn next month?",
        "Break down AUM by segment",
        "Project next quarter trading notional",
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


def normalize_domain(value: str | None) -> str:
    if not value:
        return "retail"
    key = value.strip().lower()
    if key in {"all", *DOMAINS}:
        return key
    aliases = {"bank": "banking", "retail": "retail", "ins": "insurance", "fin": "finance"}
    return aliases.get(key, "retail")
