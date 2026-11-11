# Banking data dictionary

Banking analytics read the catalog banking and schema gold in Unity Catalog. The approved business tables are gold_transactions, gold_accounts, gold_branches, gold_cards, gold_customers, gold_loans and gold_loan_repayments, with daily and monthly aggregates named agg_*.

gold_transactions holds one row per transaction: amount in INR, channel (ATM, NETBANKING, IMPS, POS, UPI, NEFT), merchant_category (CASH, FOOD, ELECTRONICS, TRAVEL, UTILITIES, GROCERY, SHOPPING, FUEL, JEWELLERY), status (SUCCESS, FAILED, PENDING), txn_type (DEBIT, CREDIT, TRANSFER), city, and txn_timestamp, which is stored as a date with no time of day.

gold_accounts holds one row per account: account_type (CURRENT, SALARY, SAVINGS), status (ACTIVE, DORMANT, CLOSED), the current balance in INR, the opening date, and the branch and customer it belongs to. All accounts are in INR.

gold_branches holds the 20 branches with their city, state and region (NORTH, EAST, WEST, SOUTH). A transaction's region is the region of its account's branch, while its city is where the transaction took place, so the two can differ.

gold_loans holds one row per loan: loan_type (AUTO, PERSONAL, EDUCATION, HOME), principal, annual interest rate, tenure in months, EMI and disbursement date.

gold_loan_repayments holds one row per instalment: its due date, paid date, amount due and amount paid. An instalment recorded with amount_paid 0 and no paid date has been missed.

gold_cards holds one row per card: card_type (CREDIT, DEBIT), status (ACTIVE, EXPIRED, BLOCKED), credit limit and issue date.

gold_customers holds one row per customer: segment (MASS, AFFLUENT, PREMIUM), city, state and onboarding date. It also holds personal data, which analytics answers never return.

Tables named summary_*, snapshot_*, dim_* and fact_* duplicate these or hold per-record figures, and are not used to answer questions.
