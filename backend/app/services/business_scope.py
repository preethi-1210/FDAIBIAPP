"""Recognise questions that are plainly not about the business, before any planner, tool or query runs.

Deliberately conservative: a question is out of scope only when it names a clearly non-business
topic (recipes, weather, jokes, travel plans...) AND uses no business or data vocabulary. Anything
else continues to the governed workflow, which still refuses unsupported business questions with the
list of approved measures. So "total spend at travel merchants" is a business question, while
"I need to travel from Coimbatore to Chennai" is not.
"""

import re

_OFF_TOPIC = re.compile(
    r"\b(?:"
    # Food and cooking
    r"recipes?|cook(?:ing)?|bak(?:e|ing)|cakes?|cookies?|biryani|pizza|dessert|ingredients?|calories"
    # Weather
    r"|weather|rain(?:ing|fall)?|sunny|snow(?:ing)?|humidity"
    # Entertainment and creative writing
    r"|jokes?|funny|make me laugh|riddles?|poems?|poetry|haiku|limerick|lyrics|sing(?: me)?|songs?"
    r"|(?:bedtime |short )?stor(?:y|ies) about|romantic|love letter|movies?|films?|netflix|tv shows?|celebrit(?:y|ies)"
    # Travel and directions
    r"|travel(?:l?ing)?|trips?|vacation|tourist|sightseeing|itinerary|flights?|hotels?|directions? to|nearest"
    r"|(?:train|bus|cab|taxi) (?:to|from|tickets?)"
    # Sport, astrology, personal life
    r"|cricket|football|soccer|ipl|nba|horoscope|zodiac|astrology|dating|girlfriend|boyfriend"
    r")\b",
    re.IGNORECASE,
)

# Words that make a question about the business, in any of the five domains. Kept to nouns and
# measures: generic words such as "how many" or "total" also appear in everyday questions.
_BUSINESS = re.compile(
    r"\b(?:"
    r"revenue|sales|sold|profit|margin|income|expenses?|costs?|budget|kpis?|metrics?|reports?|dashboards?|analytics?|data"
    r"|trends?|growth|churn|segments?|regions?|branch(?:es)?|stores?|channels?|categor(?:y|ies)"
    r"|customers?|clients?|accounts?|balances?|deposits?|withdrawals?|transactions?|txns?|payments?|transfers?|upi|neft|imps|atm"
    r"|loans?|emis?|repayments?|instal?lments?|interest|credit|debit|cards?|merchants?|spend(?:ing)?|spent|amount|money"
    r"|portfolios?|investments?|assets?|holdings?|ledger|invoices?"
    r"|polic(?:y|ies)|premiums?|claims?|underwriting|insured|coverage|loss ratio"
    r"|orders?|products?|inventory|stock|basket|discounts?|returns?|shipments?"
    r"|patients?|encounters?|diagnos[ie]s|admissions?|readmissions?|providers?|procedures?|visits"
    r"|inr|usd|rupees?|dollars?"
    r")\b",
    re.IGNORECASE,
)


def is_out_of_scope(question: str) -> bool:
    """True only for a question that names a non-business topic and nothing about the business."""
    return bool(_OFF_TOPIC.search(question)) and not _BUSINESS.search(question)
