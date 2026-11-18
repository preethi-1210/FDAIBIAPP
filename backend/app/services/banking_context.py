"""Banking business context: capabilities, glossary, data dictionary, relationships and rules.

A read-only description of what the Banking data means and what it can answer. It sits beside the
metric registry (banking_registry.py) and refers to it; it never redefines a metric, a formula, a
join or an allowed value. Facts were checked against Databricks banking.gold on VERIFIED_ON with
read-only aggregate queries, and against the PostgreSQL banking schema for consistency only.

Status values:
- VERIFIED: established by the data and the approved registry (no business owner has signed off).
- PROPOSED: a plain-language meaning written from the data, awaiting a business owner's review.
- PENDING_BUSINESS_APPROVAL: needs a business decision; questions that depend on it are refused.
- UNAVAILABLE: the data needed does not exist in banking.gold.
"""

import hashlib
import re
from dataclasses import dataclass

from ..sensitive_data import (
    _ALWAYS_REDACTED_COLUMN,
    _CREDENTIAL_COLUMN,
    _CVV_COLUMN,
    _LINKING_COLUMN,
    _RESTRICTED_COLUMN,
)
from .banking_registry import APPROVED_TABLES, BANKING, CATALOG_SCHEMA, METRICS, REGISTRY_VERSION

CONTEXT_VERSION = "1.0.0"
VERIFIED_ON = "2026-11-11"
VERIFIED = "VERIFIED"
PROPOSED = "PROPOSED"
PENDING = "PENDING_BUSINESS_APPROVAL"
UNAVAILABLE = "UNAVAILABLE"
STATUSES = (VERIFIED, PROPOSED, PENDING, UNAVAILABLE)

# The approved source decision: Databricks gold answers; PostgreSQL banking is for consistency checks.
SOURCES = {
    "analytical": {
        "system": "Databricks",
        "location": CATALOG_SCHEMA,
        "role": "Authoritative source for every Banking figure, including the dashboard.",
    },
    "consistency": {
        "system": "PostgreSQL",
        "location": "aibi_database, schema banking",
        "role": "Read-only consistency validation only. Never queried by Ask AI or the dashboard.",
        "tables": {
            "gold_accounts": "apex_bank_data_accounts_2026_03_15_accounts",
            "gold_loans": "apex_bank_data_loans_2026_03_15_loans",
            "gold_loan_repayments": "apex_bank_data_loan_repayments_2026_03_15_loan_repayments",
            "gold_cards": "apex_bank_data_cards_2026_03_15_cards",
            "gold_branches": "apex_bank_data_branches_branches",
        },
        "notes": (
            "Columns are text and there are no keys; values are cast for comparison.",
            "No transactions table exists in this schema.",
            "Customers exist as two snapshots (5,050 and 100 rows) and do not reconcile to gold_customers (5,000).",
        ),
    },
    "application": {
        "system": "PostgreSQL",
        "location": "aibi_database, schema public",
        "role": "Users, permissions, conversations, query history, reviews and audit. No Banking figures.",
    },
    "fallback": "None. If Databricks is unavailable the answer says so; no other source is used.",
}


# ---------------------------------------------------------------------------------------------
# Business rules
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BusinessRule:
    id: str
    statement: str
    status: str
    evidence: str
    applies_to: tuple[str, ...] = ()
    # What the assistant does while the rule is pending.
    while_pending: str = ""


RULES: tuple[BusinessRule, ...] = (
    BusinessRule(
        "active_account_status", "An active account is an account whose status is ACTIVE.", VERIFIED,
        "gold_accounts.status holds exactly ACTIVE (6,825), DORMANT (783) and CLOSED (392).", ("account_count",),
    ),
    BusinessRule(
        "dormant_account_status", "A dormant account is an account whose status is DORMANT. DORMANT and CLOSED are not combined.", VERIFIED,
        "gold_accounts.status values; the registry lists them as separate filter values.", ("account_count",),
    ),
    BusinessRule(
        "transaction_statuses_included", "Transaction counts and values include every status (SUCCESS, FAILED, PENDING) unless a status is asked for.", VERIFIED,
        "Registry definitions of transaction_volume, transaction_amount and average_transaction_amount.",
        ("transaction_volume", "transaction_amount", "average_transaction_amount"),
    ),
    BusinessRule(
        "successful_means_success_status", "Only status SUCCESS counts as successful. FAILED and PENDING are never counted as successful.", VERIFIED,
        "gold_transactions.status holds SUCCESS, FAILED and PENDING only; there is no CANCELLED or REVERSED status.",
        ("transaction_failure_rate",),
    ),
    BusinessRule(
        "debits_and_credits_added", "Transaction value adds debit and credit amounts together; it is not a net flow.", VERIFIED,
        "Registry definition of transaction_amount.", ("transaction_amount",),
    ),
    BusinessRule(
        "balances_from_accounts", "Account balances come from gold_accounts.balance, never from summing transactions.", VERIFIED,
        "Transactions cover one day; gold_accounts holds the current balance of each account.",
        ("total_account_balance", "average_account_balance"),
    ),
    BusinessRule(
        "current_state_has_no_period", "Balances, account counts, card counts and customer counts are current state; no period or trend applies.", VERIFIED,
        "The registry marks these metrics supports_time=False; no dated balance or status history exists.",
        ("total_account_balance", "average_account_balance", "account_count", "card_count", "customer_count", "total_credit_limit"),
    ),
    BusinessRule(
        "transactions_single_day", "Transaction trends, period comparisons and forecasts cannot be produced: all transactions fall on 2026-03-15.", VERIFIED,
        "MIN and MAX of gold_transactions.txn_timestamp are both 2026-03-15.",
        ("transaction_volume", "transaction_amount", "average_transaction_amount", "transaction_failure_rate"),
    ),
    BusinessRule(
        "unmatched_rows_kept", "Breakdowns keep rows that have no matching account, customer or branch, shown as unmatched, so they reconcile to the total.", VERIFIED,
        "Registry joins are LEFT JOINs; 30 transactions have no account and 25 accounts have no customer.",
    ),
    BusinessRule(
        "anomaly_is_not_fraud", "A detected anomaly or data-quality finding is never described as fraud.", VERIFIED,
        "banking.gold has no fraud label or case data; the registry refuses fraud questions.",
    ),
    BusinessRule(
        "closed_accounts_in_total_balance", "Whether CLOSED accounts are included in total account balance.", PENDING,
        "CLOSED accounts hold INR 99,744,187.21 of INR 1,988,344,989.56. The existing metric includes every status.",
        ("total_account_balance", "average_account_balance"),
        "The existing total_account_balance metric is unchanged and includes CLOSED accounts; its formula is not altered without approval.",
    ),
    BusinessRule(
        "closed_accounts_in_deposits", "Whether CLOSED accounts are included in deposit metrics, and what 'deposits' means.", PENDING,
        "No deposit metric exists. 'Deposits' could mean account balances or CREDIT transactions.",
        while_pending="Deposit questions are refused or clarified.",
    ),
    BusinessRule(
        "active_customer_definition", "What an active customer is.", PENDING,
        "gold_customers has no status column; activity cannot be read from one day of transactions.",
        while_pending="Active-customer questions are refused or clarified.",
    ),
    BusinessRule(
        "loan_default_definition", "How loan default and non-performing loans are calculated.", PENDING,
        "Every one of the 2,000 loans has loan_status ACTIVE; no days-past-due or default field exists.",
        while_pending="Default and NPL questions are refused; missed instalments remain available.",
    ),
    BusinessRule(
        "fraud_classification", "How fraud, suspicious activity and risk indicators are classified, and their thresholds.", PENDING,
        "No fraud label, case or threshold exists in banking.gold.",
        while_pending="Fraud questions are refused; only the fixed data-quality checks run.",
    ),
    BusinessRule(
        "profitability_calculation", "How income, expense, net interest margin and profitability are calculated.", PENDING,
        "banking.gold holds no ledger, income or cost data.",
        while_pending="Profitability questions are refused. Revenue is never calculated by summing transaction amounts.",
    ),
    BusinessRule(
        "payment_definition", "Whether a 'payment' is a transaction on a payment channel, and which channels count.", PENDING,
        "Channels are ATM, NETBANKING, IMPS, POS, UPI and NEFT; there is no RTGS and no payment table.",
        while_pending="Questions about payments are clarified; transaction questions by channel remain available.",
    ),
    BusinessRule(
        "negative_amount_treatment", "How the 60 negative-amount transactions are treated (reversal, refund or error).", PENDING,
        "57 are SUCCESS and 3 FAILED, across DEBIT and CREDIT. There is no reversal flag. They are included in totals today.",
        ("transaction_amount", "average_transaction_amount"),
        "Totals are unchanged and still include them; the data-quality note says so.",
    ),
    BusinessRule(
        "customer_growth_definition", "How customer growth rate is calculated.", PENDING,
        "Onboarding counts by date exist (new_customers); no growth formula is approved.",
        while_pending="Growth-rate questions are clarified; onboarding counts by period remain available.",
    ),
)


# ---------------------------------------------------------------------------------------------
# Capabilities A to J
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Capability:
    id: str
    name: str
    status: str  # available | partial | unavailable
    metrics: tuple[str, ...]
    pending_rules: tuple[str, ...]
    unavailable: tuple[tuple[str, str], ...]
    note: str = ""


CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        "A", "Customer management", "partial",
        ("customer_count", "new_customers"),
        ("active_customer_definition", "customer_growth_definition"),
        (("Retention and churn", "No closure date or customer history is recorded."),
         ("Inactive customers", "No customer status; transactions cover one day.")),
        "Breakdowns by segment, city and state are available.",
    ),
    Capability(
        "B", "Account management", "partial",
        ("account_count", "new_accounts_opened", "total_account_balance", "average_account_balance"),
        ("closed_accounts_in_total_balance",),
        (("Account closures over time", "No closure date is recorded, only the current status."),
         ("Balance growth", "No dated balances exist.")),
    ),
    Capability(
        "C", "Transaction management", "partial",
        ("transaction_volume", "transaction_amount", "average_transaction_amount", "transaction_failure_rate"),
        ("negative_amount_treatment",),
        (("Trends, comparisons and 'why' questions", "All transactions fall on 2026-03-15."),
         ("Reversals and cancellations", "No reversal or cancellation status exists.")),
    ),
    Capability(
        "D", "Deposit management", "unavailable", (),
        ("closed_accounts_in_deposits",),
        (("Deposit growth and history", "No dated balances exist."),
         ("Interest earned or paid", "No interest ledger exists.")),
    ),
    Capability(
        "E", "Loan management", "partial",
        ("loans_disbursed", "average_interest_rate", "repayment_due", "repayment_collected", "collection_rate", "missed_instalments"),
        ("loan_default_definition",),
        (("Outstanding loan balance", "No outstanding balance field; principal minus amount paid is wrong because payments include interest."),
         ("Applications and approvals", "Only disbursed loans are recorded."),
         ("Credit risk", "No credit score or delinquency classification exists.")),
    ),
    Capability(
        "F", "Payment management", "partial",
        ("transaction_volume", "transaction_failure_rate"),
        ("payment_definition",),
        (("RTGS", "No RTGS transactions exist."),
         ("Settlement and reconciliation", "No settlement data exists.")),
        "Transactions by channel and status are available; calling them payments needs approval.",
    ),
    Capability(
        "G", "Risk and fraud management", "partial", (),
        ("fraud_classification",),
        (("Confirmed fraud", "No fraud labels or cases exist."),
         ("Credit risk", "No credit score or delinquency classification exists.")),
        "Fixed data-quality checks (negative amounts, unmatched accounts) are available and are not fraud findings.",
    ),
    Capability(
        "H", "Financial performance", "unavailable", (),
        ("profitability_calculation",),
        (("Interest income and expense, NIM, operating expense, fees, revenue", "banking.gold holds no ledger, income or cost data."),),
    ),
    Capability(
        "I", "Compliance management", "partial", (),
        ("fraud_classification",),
        (("AML cases and investigations", "No case or investigation data exists."),
         ("Monitoring thresholds", "No approved thresholds exist.")),
        "KYC status (VERIFIED, PENDING, REJECTED) is recorded on gold_customers; no approved KYC metric exists yet.",
    ),
    Capability(
        "J", "Merchant banking", "unavailable", (), (),
        (("Merchants, onboarding and acquiring", "No merchant entity exists in banking.gold."),
         ("Orders, refunds, returns and settlements", "No such tables exist in banking. Orders and order items belong to the Retail demo data.")),
        "gold_transactions.merchant_category is a spend category of a transaction, not a merchant.",
    ),
)


# ---------------------------------------------------------------------------------------------
# Glossary
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Term:
    term: str
    definition: str
    status: str
    # ("metric", metric_id) | ("dimension", "dimension_id=VALUE" or "dimension_id") | ("column", "table.column") | None
    maps_to: tuple[str, str] | None = None
    # Only words that mean exactly this term in this data.
    synonyms: tuple[str, ...] = ()
    # Words people use that could mean this or something else; these need clarification.
    ambiguous: tuple[str, ...] = ()
    rules: tuple[str, ...] = ()
    table: str | None = None
    column: str | None = None


GLOSSARY: tuple[Term, ...] = (
    Term("Customer", "A record in gold_customers: a person or entity holding accounts, cards or loans with the bank.", VERIFIED,
         ("metric", "customer_count"), ("customers", "clients"), table="gold_customers", column="customer_id"),
    Term("New customer", "A customer counted by onboarding_date in the period asked for.", VERIFIED,
         ("metric", "new_customers"), ("customers onboarded", "customers joined"), table="gold_customers", column="onboarding_date"),
    Term("Active customer", "Not defined: the data has no customer status.", PENDING,
         ambiguous=("active customers", "inactive customers"), rules=("active_customer_definition",)),
    Term("Customer segment", "The bank's segment for a customer: MASS, AFFLUENT or PREMIUM.", VERIFIED,
         ("dimension", "customer_segment"), ("segment",), table="gold_customers", column="customer_segment"),
    Term("Customer growth rate", "Not defined: no growth formula is approved.", PENDING,
         ambiguous=("customer growth",), rules=("customer_growth_definition",)),
    Term("Account", "A record in gold_accounts: a CURRENT, SALARY or SAVINGS account held at a branch.", VERIFIED,
         ("metric", "account_count"), ("accounts",), table="gold_accounts", column="account_id"),
    Term("Account type", "The product type of an account: CURRENT, SALARY or SAVINGS.", VERIFIED,
         ("dimension", "account_type"), table="gold_accounts", column="account_type"),
    Term("Active account", "An account whose status is ACTIVE.", VERIFIED,
         ("dimension", "account_status=ACTIVE"), rules=("active_account_status",), table="gold_accounts", column="status"),
    Term("Dormant account", "An account whose status is DORMANT.", VERIFIED,
         ("dimension", "account_status=DORMANT"), ("dormant accounts",), ambiguous=("inactive accounts",),
         rules=("dormant_account_status",), table="gold_accounts", column="status"),
    Term("Closed account", "An account whose status is CLOSED.", VERIFIED,
         ("dimension", "account_status=CLOSED"), ("closed accounts",), table="gold_accounts", column="status"),
    Term("Account opening", "An account counted by open_date, whatever its status today.", VERIFIED,
         ("metric", "new_accounts_opened"), ("accounts opened", "account openings"), table="gold_accounts", column="open_date"),
    Term("Account balance", "The current balance of an account in INR, from gold_accounts.balance. Not derived from transactions.", VERIFIED,
         ("metric", "total_account_balance"), ("total balance",), ambiguous=("money held by customers", "money in the bank"),
         rules=("balances_from_accounts", "closed_accounts_in_total_balance"), table="gold_accounts", column="balance"),
    Term("Deposit balance", "Not defined: no deposit metric exists, and it could mean balances or credits.", PENDING,
         ambiguous=("deposits", "deposit balance", "money deposited"), rules=("closed_accounts_in_deposits",)),
    Term("Transaction", "A record in gold_transactions: one DEBIT, CREDIT or TRANSFER on an account.", VERIFIED,
         ("metric", "transaction_volume"), ("transactions", "txns"), table="gold_transactions", column="txn_id"),
    Term("Transaction volume", "The number of transactions, reported with their total amount.", VERIFIED,
         ("metric", "transaction_volume"), ("transaction count", "number of transactions"),
         rules=("transaction_statuses_included",), table="gold_transactions", column="txn_id"),
    Term("Transaction value", "The sum of transaction amounts, debits and credits added together, in INR.", VERIFIED,
         ("metric", "transaction_amount"), ("transaction amount", "total transaction value"),
         rules=("transaction_statuses_included", "debits_and_credits_added", "negative_amount_treatment"), table="gold_transactions", column="amount"),
    Term("Average transaction amount", "The mean amount per transaction, in INR.", VERIFIED,
         ("metric", "average_transaction_amount"), ("average transaction value",), table="gold_transactions", column="amount"),
    Term("Transaction type", "DEBIT, CREDIT or TRANSFER.", VERIFIED,
         ("dimension", "txn_type"), table="gold_transactions", column="txn_type"),
    Term("Channel", "The channel a transaction used: ATM, NETBANKING, IMPS, POS, UPI or NEFT.", VERIFIED,
         ("dimension", "channel"), ("transaction channel",), table="gold_transactions", column="channel"),
    Term("Successful transaction", "A transaction whose status is SUCCESS.", VERIFIED,
         ("dimension", "status=SUCCESS"), ("successful transactions",), rules=("successful_means_success_status",), table="gold_transactions", column="status"),
    Term("Failed transaction", "A transaction whose status is FAILED.", VERIFIED,
         ("dimension", "status=FAILED"), ("failed transactions",), rules=("successful_means_success_status",), table="gold_transactions", column="status"),
    Term("Transaction failure rate", "The share of transactions whose status is FAILED.", VERIFIED,
         ("metric", "transaction_failure_rate"), ("failure rate",), table="gold_transactions", column="status"),
    Term("Payment", "Not defined: there is no payment table, and calling channel transactions payments needs approval.", PENDING,
         ambiguous=("payments", "payment success rate", "failed payments"), rules=("payment_definition",)),
    Term("Payment settlement", "Settlement of payments between institutions.", UNAVAILABLE, ambiguous=("settlement",)),
    Term("RTGS", "Real Time Gross Settlement transfers.", UNAVAILABLE),
    Term("Merchant", "The business that received a card or payment transaction. No merchant identity exists in banking.gold.", UNAVAILABLE,
         ambiguous=("merchants",)),
    Term("Merchant category", "The spend category recorded on a transaction (for example FOOD, FUEL, TRAVEL). It is not a merchant.", VERIFIED,
         ("dimension", "merchant_category"), ("spend category",), table="gold_transactions", column="merchant_category"),
    Term("Merchant transaction", "A transaction attributed to a specific merchant. Not available: transactions carry only a spend category.", UNAVAILABLE),
    Term("Loan", "A record in gold_loans: a disbursed AUTO, PERSONAL, EDUCATION or HOME loan.", VERIFIED,
         ("metric", "loans_disbursed"), ("loans",), table="gold_loans", column="loan_id"),
    Term("Loan disbursement", "A loan counted by disbursement_date, with its principal in INR.", VERIFIED,
         ("metric", "loans_disbursed"), ("loans disbursed", "disbursements"), table="gold_loans", column="disbursement_date"),
    Term("Loan outstanding", "The amount still owed on loans. Not available: no outstanding balance is recorded.", UNAVAILABLE,
         ambiguous=("outstanding loans", "outstanding loan amount")),
    Term("Loan default", "Not defined: every loan is ACTIVE and no default classification exists.", PENDING,
         ambiguous=("default rate", "npl", "non-performing loans"), rules=("loan_default_definition",)),
    Term("Repayment", "An instalment in gold_loan_repayments, dated by due_date, with emi_due and amount_paid.", VERIFIED,
         ("metric", "repayment_collected"), ("loan repayments", "emi payments"), table="gold_loan_repayments", column="amount_paid"),
    Term("Collection rate", "Amount paid as a share of the amount due, for instalments due in the period.", VERIFIED,
         ("metric", "collection_rate"), ("repayment collection rate",), table="gold_loan_repayments", column="amount_paid"),
    Term("Missed instalment", "An instalment recorded with amount_paid 0.", VERIFIED,
         ("metric", "missed_instalments"), ("missed emis", "unpaid instalments"), table="gold_loan_repayments", column="amount_paid"),
    Term("Interest rate", "The annual interest rate of a loan, in percent.", VERIFIED,
         ("metric", "average_interest_rate"), table="gold_loans", column="interest_rate"),
    Term("Interest income", "Interest earned by the bank. Not available: no income data exists.", UNAVAILABLE,
         rules=("profitability_calculation",)),
    Term("Card", "A record in gold_cards: a CREDIT or DEBIT card.", VERIFIED,
         ("metric", "card_count"), ("cards",), table="gold_cards", column="card_id"),
    Term("Credit limit", "The credit limit of a card in INR. Debit cards have none.", VERIFIED,
         ("metric", "total_credit_limit"), table="gold_cards", column="credit_limit"),
    Term("KYC status", "The know-your-customer state of a customer: VERIFIED, PENDING or REJECTED.", VERIFIED,
         ("column", "gold_customers.kyc_status"), ("kyc",), table="gold_customers", column="kyc_status"),
    Term("Branch", "A record in gold_branches, with city, state and region.", VERIFIED,
         ("column", "gold_branches.branch_id"), ("branches",), table="gold_branches", column="branch_id"),
    Term("Region", "The branch region: NORTH, EAST, WEST or SOUTH.", VERIFIED,
         ("dimension", "region"), table="gold_branches", column="region"),
    Term("Fraud indicator", "A sign that activity may need review. Not defined: no labels or thresholds are approved, and an indicator is never fraud.", PENDING,
         ambiguous=("suspicious transactions", "fraud", "unusual activity"), rules=("fraud_classification", "anomaly_is_not_fraud")),
)


def glossary_for_postgres_shape() -> list[dict[str, str]]:
    """Glossary rows shaped like the PostgreSQL glossary table, for the existing consumers.

    Replaces the PostgreSQL banking rows, which describe the synthetic demo tables, without editing
    them. Only terms mapped to a gold column are included.
    """
    return [
        {"domain": "banking", "business_term": term.term.lower(), "table_name": term.table, "column_name": term.column, "description": term.definition}
        for term in GLOSSARY
        if term.table and term.column and term.status == VERIFIED
    ]


# ---------------------------------------------------------------------------------------------
# Data dictionary
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    meaning: str
    key: str | None = None  # "PK" or "FK table.column"
    nulls: int = 0


@dataclass(frozen=True)
class Table:
    name: str
    kind: str  # entity | aggregate
    grain: str
    rows: int
    date_column: str | None
    coverage: str
    columns: tuple[Column, ...]
    notes: tuple[str, ...] = ()


_PIPELINE = (
    Column("ingestion_timestamp", "TIMESTAMP", "Pipeline: when the source file was ingested."),
    Column("source_file", "STRING", "Pipeline: the source file the row came from."),
    Column("_gold_processed_timestamp", "TIMESTAMP", "Pipeline: when the gold layer processed the row."),
    Column("_gold_source", "STRING", "Pipeline: the upstream layer the row came from."),
)
_LAST_LOAD = "Last gold load 2026-10-22 (UTC), from MAX(_gold_processed_timestamp)."


def _agg(name: str, grain: str, by: str, rows: int, measures: tuple[tuple[str, str, str], ...], coverage: str, note: str) -> Table:
    date = Column("_gold_date", "DATE", f"The {by}.") if grain == "day" else Column("_gold_month", "STRING", f"The month of the {by}, as YYYY-MM.")
    return Table(
        name, "aggregate", f"one row per {grain} of {by}", rows, date.name, coverage,
        (date, Column("record_count", "LONG", "Number of source rows in the group."), *(Column(n, t, m) for n, t, m in measures)),
        (note,),
    )


_TXN_MEASURES = (("total_amount", "DOUBLE", "Sum of transaction amounts."), ("avg_amount", "DOUBLE", "Mean transaction amount."))
_ACCT_MEASURES = (("total_balance", "DOUBLE", "Sum of current balances of the accounts opened in the group."), ("avg_balance", "DOUBLE", "Mean current balance of those accounts."))
_CARD_MEASURES = (("total_credit_limit", "LONG", "Sum of credit limits of the cards issued in the group."), ("avg_credit_limit", "DOUBLE", "Mean credit limit of those cards."))
_LOAN_MEASURES = (
    ("total_principal_amount", "LONG", "Sum of principal."), ("avg_principal_amount", "DOUBLE", "Mean principal."),
    ("total_interest_rate", "DOUBLE", "Sum of interest rates; not meaningful as a total."), ("avg_interest_rate", "DOUBLE", "Mean interest rate."),
    ("total_tenure_months", "LONG", "Sum of tenures; not meaningful as a total."), ("avg_tenure_months", "DOUBLE", "Mean tenure."),
    ("total_emi_amount", "LONG", "Sum of EMI amounts."), ("avg_emi_amount", "DOUBLE", "Mean EMI amount."),
)
_REP_MEASURES = (
    ("total_emi_due", "LONG", "Sum of instalment amounts due."), ("avg_emi_due", "DOUBLE", "Mean instalment due."),
    ("total_amount_paid", "LONG", "Sum of amounts paid."), ("avg_amount_paid", "DOUBLE", "Mean amount paid."),
)
_RECONCILED = "Reconciled 2026-11-11: record_count and totals equal the entity table exactly."

DICTIONARY: tuple[Table, ...] = (
    Table("gold_customers", "entity", "one row per customer record", 5000, "onboarding_date", "onboarded 2010-01-01 to 2025-11-18", (
        Column("customer_id", "STRING", "Customer identifier.", "PK", 15),
        Column("first_name", "STRING", "Customer first name."), Column("last_name", "STRING", "Customer last name."),
        Column("dob", "DATE", "Date of birth."), Column("gender", "STRING", "Recorded gender."),
        Column("phone", "LONG", "Phone number."), Column("email", "STRING", "Email address."),
        Column("pan_number", "STRING", "PAN (tax identifier)."), Column("address", "STRING", "Postal address."),
        Column("city", "STRING", "Customer city."), Column("state", "STRING", "Customer state."), Column("pincode", "INT", "Postal code."),
        Column("kyc_status", "STRING", "KYC state: VERIFIED, PENDING or REJECTED."),
        Column("customer_segment", "STRING", "Segment: MASS, AFFLUENT or PREMIUM."),
        Column("onboarding_date", "DATE", "Date the customer was onboarded."),
        Column("updated_at", "TIMESTAMP", "When the record was last updated in the source."),
        *_PIPELINE,
    ), ("15 records have no customer_id; the other 4,985 are unique.", "20 records have a date of birth in the future.", _LAST_LOAD)),
    Table("gold_accounts", "entity", "one row per account", 8000, "open_date", "opened 2010-01-02 to 2026-06-06", (
        Column("account_id", "STRING", "Account identifier.", "PK"),
        Column("customer_id", "STRING", "Owning customer.", "FK gold_customers.customer_id"),
        Column("branch_id", "STRING", "Home branch.", "FK gold_branches.branch_id"),
        Column("account_type", "STRING", "CURRENT, SALARY or SAVINGS."),
        Column("open_date", "DATE", "Date the account was opened."),
        Column("status", "STRING", "ACTIVE, DORMANT or CLOSED, as of the last load."),
        Column("balance", "DOUBLE", "Current balance in INR, as of the last load.", None, 15),
        Column("currency", "STRING", "Currency; every account is INR."),
        *_PIPELINE,
    ), ("Current state only: no balance or status history.", "The latest month, 2026-06, is populated only to the 6th.", _LAST_LOAD)),
    Table("gold_transactions", "entity", "one row per transaction", 100011, "txn_timestamp", "one business day, 2026-03-15", (
        Column("txn_id", "STRING", "Transaction identifier.", "PK"),
        Column("account_id", "STRING", "Account the transaction was posted to.", "FK gold_accounts.account_id"),
        Column("amount", "DOUBLE", "Amount in INR. 60 rows are negative."),
        Column("channel", "STRING", "ATM, NETBANKING, IMPS, POS, UPI or NEFT."),
        Column("city", "STRING", "City where the transaction took place."),
        Column("counterparty_account", "STRING", "The other party's account, where recorded.", None, 3),
        Column("merchant_category", "STRING", "Spend category such as FOOD or FUEL. Not a merchant identity."),
        Column("status", "STRING", "SUCCESS, FAILED or PENDING."),
        Column("txn_timestamp", "DATE", "Transaction date. Despite its name, a DATE with no time."),
        Column("txn_type", "STRING", "DEBIT, CREDIT or TRANSFER."),
        *_PIPELINE,
    ), ("30 transactions refer to an account that does not exist in gold_accounts.", "No duplicate txn_id.", _LAST_LOAD)),
    Table("gold_loans", "entity", "one row per loan", 2000, "disbursement_date", "disbursed 2024-01-01 to 2025-02-04", (
        Column("loan_id", "STRING", "Loan identifier.", "PK"),
        Column("customer_id", "STRING", "Borrowing customer.", "FK gold_customers.customer_id"),
        Column("loan_type", "STRING", "AUTO, PERSONAL, EDUCATION or HOME."),
        Column("principal_amount", "INT", "Principal disbursed, in INR."),
        Column("interest_rate", "DOUBLE", "Annual interest rate, in percent."),
        Column("tenure_months", "INT", "Term in months."),
        Column("emi_amount", "INT", "Monthly instalment, in INR."),
        Column("disbursement_date", "DATE", "Date the loan was disbursed."),
        Column("loan_status", "STRING", "Loan status; every loan is ACTIVE."),
        *_PIPELINE,
    ), ("No outstanding balance, approval or default field.", _LAST_LOAD)),
    Table("gold_loan_repayments", "entity", "one row per instalment", 39570, "due_date", "due 2024-01-31 to 2026-03-15", (
        Column("repayment_id", "STRING", "Instalment identifier.", "PK"),
        Column("loan_id", "STRING", "Loan the instalment belongs to.", "FK gold_loans.loan_id"),
        Column("due_date", "DATE", "Date the instalment was due."),
        Column("paid_date", "DATE", "Date it was paid; empty when unpaid.", None, 2525),
        Column("emi_due", "INT", "Amount due, in INR."),
        Column("amount_paid", "INT", "Amount paid, in INR; 0 when missed."),
        *_PIPELINE,
    ), ("The latest month, 2026-03, is populated only to the 15th.", _LAST_LOAD)),
    Table("gold_cards", "entity", "one row per card", 6000, "issue_date", "issued 2018-01-01 to 2023-06-24", (
        Column("card_id", "STRING", "Card identifier.", "PK"),
        Column("customer_id", "STRING", "Card holder.", "FK gold_customers.customer_id"),
        Column("account_id", "STRING", "Linked account.", "FK gold_accounts.account_id"),
        Column("card_type", "STRING", "CREDIT or DEBIT."),
        Column("credit_limit", "INT", "Credit limit in INR; empty for debit cards.", None, 2989),
        Column("issue_date", "DATE", "Date the card was issued."),
        Column("expiry_date", "DATE", "Card expiry date."),
        Column("status", "STRING", "ACTIVE, EXPIRED or BLOCKED."),
        *_PIPELINE,
    ), ("Issues stop at 2023-06-24.", _LAST_LOAD)),
    Table("gold_branches", "entity", "one row per branch", 20, None, "current", (
        Column("branch_id", "STRING", "Branch identifier.", "PK"),
        Column("branch_name", "STRING", "Branch name."), Column("city", "STRING", "Branch city."),
        Column("state", "STRING", "Branch state."), Column("region", "STRING", "NORTH, EAST, WEST or SOUTH."),
        Column("ifsc_code", "STRING", "IFSC routing code of the branch."),
        *_PIPELINE,
    ), (_LAST_LOAD,)),
    _agg("agg_transactions_daily", "day", "transaction date", 1, _TXN_MEASURES, "2026-03-15", _RECONCILED),
    _agg("agg_transactions_monthly", "month", "transaction date", 1, _TXN_MEASURES, "2026-03", _RECONCILED),
    _agg("agg_accounts_daily", "day", "account opening date", 4453, _ACCT_MEASURES, "2010-01-02 to 2026-06-06",
         "Grouped by opening date, not a balance history. " + _RECONCILED),
    _agg("agg_accounts_monthly", "month", "account opening date", 198, _ACCT_MEASURES, "2010-01 to 2026-06",
         "Grouped by opening month, not a balance history."),
    _agg("agg_cards_daily", "day", "card issue date", 1895, _CARD_MEASURES, "2018-01-01 to 2023-06-24", _RECONCILED),
    _agg("agg_cards_monthly", "month", "card issue date", 66, _CARD_MEASURES, "2018-01 to 2023-06", "Grouped by issue month."),
    _agg("agg_loans_daily", "day", "disbursement date", 396, _LOAN_MEASURES, "2024-01-01 to 2025-02-04", _RECONCILED),
    _agg("agg_loans_monthly", "month", "disbursement date", 14, _LOAN_MEASURES, "2024-01 to 2025-02", "Grouped by disbursement month."),
    _agg("agg_loan_repayments_daily", "day", "instalment due date", 775, _REP_MEASURES, "2024-01-31 to 2026-03-15", _RECONCILED),
    _agg("agg_loan_repayments_monthly", "month", "instalment due date", 27, _REP_MEASURES, "2024-01 to 2026-03", "Grouped by due month."),
)
_TABLES = {table.name: table for table in DICTIONARY}


def sensitivity(column: str) -> str:
    """The existing sensitive-data policy's view of a column, so the dictionary cannot disagree with it."""
    if _CVV_COLUMN.search(column) or _CREDENTIAL_COLUMN.search(column):
        return "never"
    if _ALWAYS_REDACTED_COLUMN.search(column):
        return "always_redacted"
    if _RESTRICTED_COLUMN.search(column):
        return "restricted"
    if _LINKING_COLUMN.search(column):
        return "linking_key"
    return "business" if not column.startswith(("_gold", "ingestion", "source_file")) else "technical"


def table(name: str) -> Table | None:
    return _TABLES.get(name)


# ---------------------------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Relationship:
    child: str
    child_column: str
    parent: str
    parent_column: str
    cardinality: str
    unmatched_children: int
    used_in_registry: bool
    note: str = ""


# Verified 2026-11-11 with LEFT JOIN counts on banking.gold. Unity Catalog declares no keys, so
# these are verified against the data, not declared constraints.
RELATIONSHIPS: tuple[Relationship, ...] = (
    Relationship("gold_accounts", "customer_id", "gold_customers", "customer_id", "many-to-one", 25, True),
    Relationship("gold_accounts", "branch_id", "gold_branches", "branch_id", "many-to-one", 0, True),
    Relationship("gold_transactions", "account_id", "gold_accounts", "account_id", "many-to-one", 30, True),
    Relationship("gold_loans", "customer_id", "gold_customers", "customer_id", "many-to-one", 0, False),
    Relationship("gold_loan_repayments", "loan_id", "gold_loans", "loan_id", "many-to-one", 0, True),
    Relationship("gold_cards", "account_id", "gold_accounts", "account_id", "many-to-one", 0, False,
                 "Every card's customer equals its account's customer."),
    Relationship("gold_cards", "customer_id", "gold_customers", "customer_id", "many-to-one", 15, False),
)
# Asked about in the brief; no such relationship exists in banking.gold.
UNAVAILABLE_RELATIONSHIPS = (
    ("accounts to payments", "No payment table."),
    ("merchants to payments", "No merchant or payment table."),
    ("payments to refunds", "No payment or refund table."),
    ("orders to order items", "Retail demo data in PostgreSQL public, not banking."),
)


def join_clause(relationship: Relationship, child_alias: str, parent_alias: str) -> str:
    return (
        f"LEFT JOIN {relationship.parent} {parent_alias} ON {parent_alias}.{relationship.parent_column} = "
        f"{child_alias}.{relationship.child_column}"
    )


# ---------------------------------------------------------------------------------------------
# Metric governance (beside the registry; no formula is defined or changed here)
# ---------------------------------------------------------------------------------------------

def registry_fingerprint() -> str:
    """Hash of every metric id and measure expression; tests pin it so formulas cannot change unreviewed."""
    text = "|".join(f"{metric.id}:{measure.alias}={measure.expression}" for metric in METRICS for measure in metric.measures)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _source_tables(metric) -> tuple[str, ...]:
    return tuple(name for name in metric.source.tables if name in APPROVED_TABLES)


def metric_view(metric) -> dict:
    """The brief's metric fields for one registry metric, read from the registry itself."""
    pending = tuple(rule.id for rule in RULES if rule.status == PENDING and metric.id in rule.applies_to)
    catalog, schema = CATALOG_SCHEMA.split(".")
    tables = _source_tables(metric)
    return {
        "id": metric.id,
        "name": metric.label,
        "description": metric.definition,
        "formula": "; ".join(f"{measure.label} = {measure.expression}" for measure in metric.measures),
        "aggregation": metric.aggregation,
        "source_catalog": catalog,
        "source_schema": schema,
        "source_tables": list(tables),
        "source_columns": list(metric.required_columns),
        "date_semantics": metric.time_semantics,
        "supports_time": metric.supports_time,
        "time_granularity": ["day", "week", "month"] if metric.supports_time else [],
        "domain": "banking",
        "freshness": _LAST_LOAD,
        "business_owner": None,
        "validation_status": "REGISTRY_APPROVED",
        "validation_note": (
            f"Approved in registry {REGISTRY_VERSION}; checked against banking.gold. No business owner sign-off is recorded."
            + (f" Pending business decisions: {', '.join(pending)}." if pending else "")
        ),
        "pending_rules": list(pending),
        "data_quality": list(metric.data_quality),
        "version": metric.version,
    }


def metric_registry() -> list[dict]:
    return [metric_view(metric) for metric in METRICS]


# The brief's starting list of metrics, and where each stands.
REQUESTED_METRICS: tuple[tuple[str, str, str | None, str], ...] = (
    ("Total Customers", VERIFIED, "customer_count", ""),
    ("Active Customers", PENDING, None, "active_customer_definition"),
    ("New Customers", VERIFIED, "new_customers", ""),
    ("Total Accounts", VERIFIED, "account_count", ""),
    ("Active Accounts", VERIFIED, "account_count", "Filtered to account_status ACTIVE."),
    ("Dormant Accounts", VERIFIED, "account_count", "Filtered to account_status DORMANT."),
    ("Total Account Balance", VERIFIED, "total_account_balance", "Includes CLOSED accounts; closed_accounts_in_total_balance is pending."),
    ("Total Deposits", PENDING, None, "closed_accounts_in_deposits"),
    ("Transaction Count", VERIFIED, "transaction_volume", ""),
    ("Transaction Value", VERIFIED, "transaction_amount", ""),
    ("Average Transaction Value", VERIFIED, "average_transaction_amount", ""),
    ("Successful Payments", PENDING, None, "payment_definition"),
    ("Failed Payments", PENDING, None, "payment_definition"),
    ("Outstanding Loan Balance", UNAVAILABLE, None, "No outstanding balance field exists."),
    ("Loan Default Rate", PENDING, None, "loan_default_definition"),
)


def context_summary() -> dict:
    """Everything above as plain data, for the API and tests."""
    return {
        "context_version": CONTEXT_VERSION,
        "verified_on": VERIFIED_ON,
        "registry_version": BANKING.version,
        "sources": SOURCES,
        "capabilities": [cap.__dict__ for cap in CAPABILITIES],
        "glossary": [term.__dict__ for term in GLOSSARY],
        "metrics": metric_registry(),
        "requested_metrics": [
            {"name": name, "status": status, "metric_id": metric_id, "note": note} for name, status, metric_id, note in REQUESTED_METRICS
        ],
        "dictionary": [
            {
                **{k: v for k, v in tbl.__dict__.items() if k != "columns"},
                "columns": [{**col.__dict__, "sensitivity": sensitivity(col.name)} for col in tbl.columns],
            }
            for tbl in DICTIONARY
        ],
        "relationships": [rel.__dict__ for rel in RELATIONSHIPS],
        "unavailable_relationships": [{"relationship": name, "reason": reason} for name, reason in UNAVAILABLE_RELATIONSHIPS],
        "rules": [rule.__dict__ for rule in RULES],
    }


__all__ = [
    "CAPABILITIES", "DICTIONARY", "GLOSSARY", "RELATIONSHIPS", "RULES", "SOURCES", "STATUSES",
    "context_summary", "glossary_for_postgres_shape", "metric_registry", "registry_fingerprint", "sensitivity", "table",
]


# ---------------------------------------------------------------------------------------------
# Workflow integration: terminology resolution, rule disclosures, planner context
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Resolution:
    """What the business context made of a question before the planner reads it."""

    question: str
    terms: tuple[str, ...] = ()
    rewrites: tuple[tuple[str, str], ...] = ()
    refusal: str | None = None
    clarification: str | None = None
    rules: tuple[str, ...] = ()


def _compiled(entries):
    return tuple((term, re.compile(pattern, re.IGNORECASE), *rest) for term, pattern, *rest in entries)


# Data the bank does not hold. Checked first: each is refused with its reason, never approximated.
_UNAVAILABLE = _compiled((
    ("Merchant", r"\bmerchants?\b(?![-\s]*categor)|\bmerchant[-\s]level\b|\bacquir(?:ing|er)\b",
     "Merchant-level figures are not available: banking.gold records only a spend category (merchant_category) on each "
     "transaction, not the merchant. No query was run. You can ask by merchant category instead, for example "
     "\"transaction amount by merchant category\".", ()),
    ("Payment settlement", r"\bsettle(?:ment|ments|d)?\b|\breconcil(?:e|ed|iation|iations)\b",
     "Settlement and reconciliation figures are not available: banking.gold holds no settlement data. No query was run.", ()),
    ("Refund", r"\brefund(?:s|ed)?\b|\bchargebacks?\b",
     "Refund figures are not available: banking.gold holds no refund or return data. No query was run.", ()),
    ("RTGS", r"\brtgs\b",
     "RTGS figures are not available: no RTGS transactions exist. Recorded channels are ATM, IMPS, NETBANKING, NEFT, POS and UPI. "
     "No query was run.", ()),
    ("Loan outstanding", r"\boutstanding\b",
     "Outstanding balances are not available: banking.gold records no outstanding balance, and principal minus amount "
     "paid would be wrong because payments include interest. No query was run. Loans disbursed, repayments due and "
     "collected, and missed instalments are available.", ()),
    ("Loan applications", r"\bloans?\b[^.?!]*\b(?:approv\w*|applications?|applied|rejected)\b|\b(?:approv\w*|applications?)\b[^.?!]*\bloans?\b",
     "Loan applications and approvals are not available: banking.gold records only disbursed loans. No query was run.", ()),
    ("Deposit growth", r"\bdeposits?\b[^.?!]*\b(?:grow\w*|year[-\s]over[-\s]year|yoy|trend\w*|history)\b",
     "Deposit growth is not available: banking.gold holds only each account's current balance, with no dated balances. "
     "No query was run.", ("closed_accounts_in_deposits",)),
    ("Interest income", r"\b(?:operating|interest)\s+(?:expenses?|costs?)\b|\bexpenses?\b|\bfee\s+income\b|\bfees\b",
     "Income and expense figures are not available: banking.gold holds no ledger, income or cost data, and the "
     "profitability calculation needs business approval. Revenue is never estimated from transaction amounts. No query was run.",
     ("profitability_calculation",)),
    ("Credit risk", r"\bcredit\s+risk\b|\brisk\s+(?:scores?|ratings?|grades?)\b|\b(?:higher|high|elevated|more)\s+risk\b|\brisky\b",
     "Credit risk is not available: banking.gold holds no credit score, delinquency or default classification, and the "
     "loan default definition needs business approval. No query was run. Missed instalments by loan type are available.",
     ("loan_default_definition",)),
    ("Fraud indicator",
     r"\bsuspicious\b|\binvestigat\w*\b|\baml\b|\bmoney\s+laundering\b|\bthresholds?\b|\bunusual(?:ly)?\s+(?:high|large|withdrawal\w*)\b"
     r"|\bunusual\s+high[-\s]value\b|\bhigh[-\s]value\s+(?:transfers?|transactions?)\b|\brepeated(?:ly)?\s+fail\w*\b|\btransaction\s+frequency\b",
     "Suspicious-activity and fraud classification is not available: banking.gold has no fraud labels or cases, and "
     "anomaly thresholds need business approval. A detected anomaly would be an indicator for review, never confirmed fraud. "
     "No query was run. The approved data checks are available: ask \"Are there any anomalies in transactions?\"",
     ("fraud_classification", "anomaly_is_not_fraud")),
))

# Words with no approved meaning, or more than one: the user chooses.
_AMBIGUOUS = _compiled((
    ("Active customer", r"\b(?:in)?active\s+customers?\b|\bcustomers?\b[^.?!]*\binactive\b|\binactive\b[^.?!]*\bcustomers?\b",
     "\"Active\" and \"inactive\" customers have no approved definition yet: customer records carry no status, and "
     "transactions cover a single day. This needs business approval, so no figure is reported. Available: number of "
     "customers, and new customers onboarded by period, segment, city or state.", ("active_customer_definition",)),
    ("Dormant account", r"\binactive\b",
     "\"Inactive\" is not an account status in the data. Accounts are ACTIVE, DORMANT or CLOSED. Do you mean dormant "
     "accounts, or dormant and closed accounts?", ("dormant_account_status",)),
    ("Customer growth rate", r"\bgrowth\s+rates?\b|\bcustomer\s+growth\b|\bcustomers?\b[^.?!]*\bgrow\w*\b",
     "Customer growth rate has no approved formula yet, so it is not calculated. Available: new customers onboarded "
     "by period, for example \"monthly trend of new customers onboarded\".", ("customer_growth_definition",)),
    ("Deposit balance", r"\bdeposit(?:s|ed)?\b",
     "\"Deposits\" has no approved definition yet. It could mean the current balance of accounts (total account balance, "
     "which includes CLOSED accounts) or money credited to accounts (CREDIT transactions). Which do you mean? For example: "
     "\"total account balance by region\" or \"total amount of credit transactions\".", ("closed_accounts_in_deposits",)),
    ("Account balance", r"\bmoney\s+(?:held|kept|in\s+the\s+bank)\b|\bheld\s+by\s+customers\b",
     "That could mean the current balance of accounts or deposits, and deposits have no approved definition. Do you mean "
     "\"total account balance\"? It includes CLOSED accounts, an inclusion still awaiting business approval.",
     ("closed_accounts_in_total_balance", "closed_accounts_in_deposits")),
    ("KYC status", r"\bkyc\b|\bknow\s+your\s+customer\b",
     "KYC status is recorded on each customer (VERIFIED, PENDING or REJECTED), but no approved KYC metric exists yet, "
     "and \"incomplete\" could mean PENDING, REJECTED or both. This needs business approval, so no figure is reported.", ()),
    ("Payment", r"\bpayments?\b",
     "\"Payment\" has no approved definition yet: there is no payment table, and treating channel transactions as "
     "payments needs business approval. You can ask about transactions instead, for example \"failed transactions by "
     "channel\" or \"transaction failure rate by channel\".", ("payment_definition",)),
))

# Phrases with exactly one meaning in this data, rewritten to the registry's own words.
_REWRITES = _compiled((
    ("Repayment", r"\b(?:loan|emi)\s+payments?\b", "loan repayments"),
    ("New customer", r"\b(?:new\s+)?customers?\s+(?:who\s+|that\s+)?(?:have\s+|has\s+)?(?:joined|signed\s+up|were\s+onboarded|got\s+onboarded)\b", "customers onboarded"),
    ("Account opening", r"\b(?<!new\s)accounts?\s+(?:(?:were|was|have\s+been|has\s+been|got)\s+)?opened\b", "new accounts opened"),
    ("Customer", r"\bclients?\b", "customers"),
))


def resolve_terminology(question: str) -> Resolution:
    """Map business words to approved meanings before planning, or say why they cannot be mapped."""
    for term, pattern, reason, rules in _UNAVAILABLE:
        if pattern.search(question):
            return Resolution(question, (term,), refusal=reason, rules=rules)
    rewritten, rewrites, terms = question, [], []
    for term, pattern, replacement in _REWRITES:
        match = pattern.search(rewritten)
        if match:
            rewrites.append((match.group(), replacement))
            terms.append(term)
            rewritten = pattern.sub(replacement, rewritten)
    for term, pattern, message, rules in _AMBIGUOUS:
        if pattern.search(rewritten):
            return Resolution(question, (*terms, term), tuple(rewrites), clarification=message, rules=rules)
    lowered = rewritten.lower()
    for item in GLOSSARY:
        words = (item.term.lower(), *item.synonyms)
        if item.status == VERIFIED and item.term not in terms and any(re.search(rf"\b{re.escape(word)}s?\b", lowered) for word in words):
            terms.append(item.term)
    return Resolution(rewritten, tuple(terms), tuple(rewrites))


def rules_for(metric_id: str) -> tuple[BusinessRule, ...]:
    return tuple(rule for rule in RULES if metric_id in rule.applies_to)


def rule_disclosures(metric_id: str, filters: dict[str, list[str]]) -> list[str]:
    """Statements an answer must carry because a business rule behind it is still pending."""
    notes = []
    statuses = set(filters.get("account_status") or [])
    if metric_id in {"total_account_balance", "average_account_balance"} and (not statuses or "CLOSED" in statuses):
        figure = "total" if metric_id == "total_account_balance" else "average"
        notes.append(
            f"CLOSED accounts are included in this current {figure} balance. This follows the existing registry definition; "
            "official business approval of including CLOSED accounts is still pending, so this is not an owner-approved business KPI."
        )
    return notes


def planner_context() -> str:
    """Approved meanings for the model planner, and the words it must never map to a metric."""
    mapped = [
        f"- {term.term}: {term.definition} -> {term.maps_to[0]} {term.maps_to[1]}"
        for term in GLOSSARY
        if term.status == VERIFIED and term.maps_to and term.maps_to[0] in {"metric", "dimension"}
    ]
    blocked = sorted({word for term in GLOSSARY if term.status != VERIFIED for word in (term.term.lower(), *term.ambiguous)})
    return (
        "Approved business terms:\n" + "\n".join(mapped)
        + "\nThese words have no approved meaning or no data in banking.gold. Never map them to a metric; put them in "
        "unsupported_terms: " + ", ".join(blocked) + "."
    )
