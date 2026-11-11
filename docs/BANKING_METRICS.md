# Banking metric registry (v1.0.0)

Generated from `backend/app/services/banking_registry.py`, which is the authority. All sources are in `banking.gold`. Every metric is available to the `admin` and `analyst` roles, within a banking session.

## Transaction failure rate (`transaction_failure_rate`, v1)

Share of transactions whose status is FAILED.

- **Formula:** `ROUND(100.0 * AVG(CASE WHEN t.status = 'FAILED' THEN 1.0 ELSE 0.0 END), 2)` as failure rate
- **Aggregation:** AVG over transactions
- **Source:** `gold_transactions`
- **Required columns:** status
- **Required filter:** none
- **Time semantics:** Dated by txn_timestamp, a DATE column. All current rows fall on 2026-03-15.
- **Dated periods and trends:** yes; **forecast:** no
- **Dimensions:** transaction type, channel, merchant category, transaction status, transaction city, branch region, branch state, account type, customer segment
- **Data quality:** The source holds one business day, 2026-03-15, so it has no trend or history. 60 transactions have negative amounts and are included in totals. 30 transactions have no matching account; they appear as unmatched in region, state, account type and segment breakdowns.
- **Example:** What is the failure rate by channel?

## Average transaction amount (`average_transaction_amount`, v1)

Mean amount per transaction. Counts every transaction status, including FAILED and PENDING; add "successful" to the question to restrict it.

- **Formula:** `ROUND(AVG(t.amount), 2)` as average amount
- **Aggregation:** AVG(amount)
- **Source:** `gold_transactions`
- **Required columns:** amount
- **Required filter:** none
- **Time semantics:** Dated by txn_timestamp, a DATE column. All current rows fall on 2026-03-15.
- **Dated periods and trends:** yes; **forecast:** no
- **Dimensions:** transaction type, channel, merchant category, transaction status, transaction city, branch region, branch state, account type, customer segment
- **Data quality:** The source holds one business day, 2026-03-15, so it has no trend or history. 60 transactions have negative amounts and are included in totals. 30 transactions have no matching account; they appear as unmatched in region, state, account type and segment breakdowns.
- **Example:** What is the average transaction amount for UPI?

## Total transaction amount (`transaction_amount`, v1)

Sum of transaction amounts, debit and credit added together rather than netted. Counts every transaction status, including FAILED and PENDING; add "successful" to the question to restrict it.

- **Formula:** `ROUND(SUM(t.amount), 2)` as total amount, `COUNT(*)` as transactions
- **Aggregation:** SUM(amount)
- **Source:** `gold_transactions`
- **Required columns:** amount
- **Required filter:** none
- **Time semantics:** Dated by txn_timestamp, a DATE column. All current rows fall on 2026-03-15.
- **Dated periods and trends:** yes; **forecast:** no
- **Dimensions:** transaction type, channel, merchant category, transaction status, transaction city, branch region, branch state, account type, customer segment
- **Data quality:** The source holds one business day, 2026-03-15, so it has no trend or history. 60 transactions have negative amounts and are included in totals. 30 transactions have no matching account; they appear as unmatched in region, state, account type and segment breakdowns.
- **Example:** What is the total transaction amount for March 2026?

## Transaction volume (`transaction_volume`, v1)

Number of transactions, reported with their total amount because "volume" is used for both. Counts every transaction status, including FAILED and PENDING; add "successful" to the question to restrict it.

- **Formula:** `COUNT(*)` as transactions, `ROUND(SUM(t.amount), 2)` as total amount
- **Aggregation:** COUNT(*), SUM(amount)
- **Source:** `gold_transactions`
- **Required columns:** txn_id, amount
- **Required filter:** none
- **Time semantics:** Dated by txn_timestamp, a DATE column. All current rows fall on 2026-03-15.
- **Dated periods and trends:** yes; **forecast:** no
- **Dimensions:** transaction type, channel, merchant category, transaction status, transaction city, branch region, branch state, account type, customer segment
- **Data quality:** The source holds one business day, 2026-03-15, so it has no trend or history. 60 transactions have negative amounts and are included in totals. 30 transactions have no matching account; they appear as unmatched in region, state, account type and segment breakdowns.
- **Example:** Show the total transaction volume for March 2026

## New accounts opened (`new_accounts_opened`, v1)

Number of accounts by opening date, whatever their status today.

- **Formula:** `COUNT(*)` as accounts opened
- **Aggregation:** COUNT(*)
- **Source:** `gold_accounts`
- **Required columns:** account_id, open_date
- **Required filter:** none
- **Time semantics:** Dated by open_date, from 2010-01-02 to 2026-06-06.
- **Dated periods and trends:** yes; **forecast:** approved
- **Dimensions:** account type, account status, branch region, branch state, branch city, customer segment
- **Data quality:** The latest month, 2026-06, is only populated to the 6th.
- **Example:** Show the monthly trend of new accounts opened

## Average account balance (`average_account_balance`, v1)

Mean current balance per account, in INR. Accounts with no recorded balance are ignored.

- **Formula:** `ROUND(AVG(a.balance), 2)` as average balance
- **Aggregation:** AVG(balance)
- **Source:** `gold_accounts`
- **Required columns:** balance
- **Required filter:** none
- **Time semantics:** Current state at the last pipeline load; the source holds no history of this figure.
- **Dated periods and trends:** no, current state only; **forecast:** no
- **Dimensions:** account type, account status, branch region, branch state, branch city, customer segment
- **Data quality:** 15 accounts have no balance.
- **Example:** What is the average balance by account type?

## Total account balance (`total_account_balance`, v1)

Sum of current balances, in INR. Accounts with no recorded balance are ignored.

- **Formula:** `ROUND(SUM(a.balance), 2)` as total balance
- **Aggregation:** SUM(balance)
- **Source:** `gold_accounts`
- **Required columns:** balance
- **Required filter:** none
- **Time semantics:** Current state at the last pipeline load; the source holds no history of this figure.
- **Dated periods and trends:** no, current state only; **forecast:** no
- **Dimensions:** account type, account status, branch region, branch state, branch city, customer segment
- **Data quality:** 15 accounts have no balance.
- **Example:** What is the total balance by region?

## Number of accounts (`account_count`, v1)

Number of accounts held today, in any status unless a status is asked for.

- **Formula:** `COUNT(*)` as accounts
- **Aggregation:** COUNT(*)
- **Source:** `gold_accounts`
- **Required columns:** account_id
- **Required filter:** none
- **Time semantics:** Current state at the last pipeline load; the source holds no history of this figure.
- **Dated periods and trends:** no, current state only; **forecast:** no
- **Dimensions:** account type, account status, branch region, branch state, branch city, customer segment
- **Example:** How many dormant accounts are there?

## Repayment collection rate (`collection_rate`, v1)

Amount paid as a share of the amount due, for instalments due in the period.

- **Formula:** `ROUND(100.0 * SUM(r.amount_paid) / NULLIF(SUM(r.emi_due), 0), 2)` as collection rate
- **Aggregation:** SUM(amount_paid) / SUM(emi_due)
- **Source:** `gold_loan_repayments`
- **Required columns:** amount_paid, emi_due
- **Required filter:** none
- **Time semantics:** Dated by due_date, from 2024-01-31 to 2026-03-15.
- **Dated periods and trends:** yes; **forecast:** no
- **Dimensions:** loan type
- **Data quality:** Instalments are dated by due date, and the latest month (2026-03) is only populated to the 15th. From 2025-07 some instalments are recorded with amount_paid 0 and no paid_date, so collected amounts fall below amounts due.
- **Example:** What is the collection rate by loan type?

## Missed instalments (`missed_instalments`, v1)

Number of instalments recorded with amount_paid 0.

- **Formula:** `SUM(CASE WHEN r.amount_paid = 0 THEN 1 ELSE 0 END)` as missed instalments
- **Aggregation:** COUNT where amount_paid = 0
- **Source:** `gold_loan_repayments`
- **Required columns:** amount_paid
- **Required filter:** none
- **Time semantics:** Dated by due_date, from 2024-01-31 to 2026-03-15.
- **Dated periods and trends:** yes; **forecast:** no
- **Dimensions:** loan type
- **Data quality:** Instalments are dated by due date, and the latest month (2026-03) is only populated to the 15th. From 2025-07 some instalments are recorded with amount_paid 0 and no paid_date, so collected amounts fall below amounts due.
- **Example:** How many instalments were missed by month?

## Repayment amount due (`repayment_due`, v1)

Sum of instalment amounts due, in INR, by due date.

- **Formula:** `SUM(r.emi_due)` as amount due
- **Aggregation:** SUM(emi_due)
- **Source:** `gold_loan_repayments`
- **Required columns:** emi_due
- **Required filter:** none
- **Time semantics:** Dated by due_date, from 2024-01-31 to 2026-03-15.
- **Dated periods and trends:** yes; **forecast:** approved
- **Dimensions:** loan type
- **Data quality:** Instalments are dated by due date, and the latest month (2026-03) is only populated to the 15th. From 2025-07 some instalments are recorded with amount_paid 0 and no paid_date, so collected amounts fall below amounts due.
- **Example:** Forecast the EMI due for next month

## Repayment amount collected (`repayment_collected`, v1)

Sum of instalment amounts paid, in INR, by due date.

- **Formula:** `SUM(r.amount_paid)` as amount collected, `COUNT(*)` as instalments
- **Aggregation:** SUM(amount_paid)
- **Source:** `gold_loan_repayments`
- **Required columns:** amount_paid
- **Required filter:** none
- **Time semantics:** Dated by due_date, from 2024-01-31 to 2026-03-15.
- **Dated periods and trends:** yes; **forecast:** approved
- **Dimensions:** loan type
- **Data quality:** Instalments are dated by due date, and the latest month (2026-03) is only populated to the 15th. From 2025-07 some instalments are recorded with amount_paid 0 and no paid_date, so collected amounts fall below amounts due.
- **Example:** Show the monthly trend of loan repayments

## Average interest rate (`average_interest_rate`, v1)

Mean annual interest rate across loans, unweighted by principal.

- **Formula:** `ROUND(AVG(l.interest_rate), 2)` as average interest rate
- **Aggregation:** AVG(interest_rate)
- **Source:** `gold_loans`
- **Required columns:** interest_rate
- **Required filter:** none
- **Time semantics:** Dated by disbursement_date, from 2024-01-01 to 2025-02-04.
- **Dated periods and trends:** yes; **forecast:** no
- **Dimensions:** loan type
- **Example:** What is the average interest rate by loan type?

## Loans disbursed (`loans_disbursed`, v1)

Number and principal of loans by disbursement date, in INR.

- **Formula:** `COUNT(*)` as loans, `SUM(l.principal_amount)` as principal
- **Aggregation:** COUNT(*), SUM(principal_amount)
- **Source:** `gold_loans`
- **Required columns:** loan_id, principal_amount
- **Required filter:** none
- **Time semantics:** Dated by disbursement_date, from 2024-01-01 to 2025-02-04.
- **Dated periods and trends:** yes; **forecast:** approved
- **Dimensions:** loan type
- **Data quality:** All 2000 loans have status ACTIVE, so loan status has no analytical value. Disbursements stop at 2025-02-04, 14 months of history.
- **Example:** How many loans were disbursed by loan type?

## Total credit limit (`total_credit_limit`, v1)

Sum of card credit limits, in INR, across all card statuses unless one is asked for.

- **Formula:** `SUM(c.credit_limit)` as total credit limit
- **Aggregation:** SUM(credit_limit)
- **Source:** `gold_cards`
- **Required columns:** credit_limit
- **Required filter:** none
- **Time semantics:** Current state at the last pipeline load; the source holds no history of this figure.
- **Dated periods and trends:** no, current state only; **forecast:** no
- **Dimensions:** card type, card status
- **Example:** What is the total credit limit by card type?

## Cards issued (`cards_issued`, v1)

Number of cards by issue date, whatever their status today.

- **Formula:** `COUNT(*)` as cards issued
- **Aggregation:** COUNT(*)
- **Source:** `gold_cards`
- **Required columns:** card_id, issue_date
- **Required filter:** none
- **Time semantics:** Dated by issue_date, from 2018-01-01 to 2023-06-24.
- **Dated periods and trends:** yes; **forecast:** approved
- **Dimensions:** card type, card status
- **Data quality:** Issues stop at 2023-06-24, so the series is out of date.
- **Example:** How many cards were issued in 2022?

## Number of cards (`card_count`, v1)

Number of cards held today, in any status unless a status is asked for.

- **Formula:** `COUNT(*)` as cards
- **Aggregation:** COUNT(*)
- **Source:** `gold_cards`
- **Required columns:** card_id
- **Required filter:** none
- **Time semantics:** Current state at the last pipeline load; the source holds no history of this figure.
- **Dated periods and trends:** no, current state only; **forecast:** no
- **Dimensions:** card type, card status
- **Example:** How many blocked cards are there?

## New customers onboarded (`new_customers`, v1)

Number of customers by onboarding date.

- **Formula:** `COUNT(*)` as customers onboarded
- **Aggregation:** COUNT(*)
- **Source:** `gold_customers`
- **Required columns:** customer_id, onboarding_date
- **Required filter:** none
- **Time semantics:** Dated by onboarding_date, from 2010-01-01 to 2025-11-18.
- **Dated periods and trends:** yes; **forecast:** approved
- **Dimensions:** customer segment, customer city, customer state
- **Data quality:** 15 customers have no customer_id and are still counted.
- **Example:** Show the monthly trend of new customers onboarded

## Number of customers (`customer_count`, v1)

Number of customer records today.

- **Formula:** `COUNT(*)` as customers
- **Aggregation:** COUNT(*)
- **Source:** `gold_customers`
- **Required columns:** customer_id
- **Required filter:** none
- **Time semantics:** Current state at the last pipeline load; the source holds no history of this figure.
- **Dated periods and trends:** no, current state only; **forecast:** no
- **Dimensions:** customer segment, customer city, customer state
- **Data quality:** 15 customers have no customer_id and are still counted.
- **Example:** How many customers are in each segment?

## Tables generated SQL may read

`agg_accounts_daily`, `agg_accounts_monthly`, `agg_cards_daily`, `agg_cards_monthly`, `agg_loan_repayments_daily`, `agg_loan_repayments_monthly`, `agg_loans_daily`, `agg_loans_monthly`, `agg_transactions_daily`, `agg_transactions_monthly`, `gold_accounts`, `gold_branches`, `gold_cards`, `gold_customers`, `gold_loan_repayments`, `gold_loans`, `gold_transactions`
