# Banking reporting periods and data coverage

Transactions cover a single business day, 2026-03-15. This is why daily, weekly and monthly transaction trends, period-over-period transaction comparisons and transaction forecasts cannot be produced. The transaction timestamp in the source file has times within that one day, and the silver layer stores only the date.

Account openings run from 2010-01-02 to 2026-06-06. Customer onboarding runs from 2010-01-01 to 2025-11-18. Card issues run from 2018-01-01 to 2023-06-24. Loan disbursements run from 2024-01-01 to 2025-02-04. Loan instalments run from 2024-01-31 to 2026-03-15 by due date.

Each measure is dated by its own business date: transactions by transaction date, accounts by opening date, loans by disbursement date, repayments by instalment due date, cards by issue date and customers by onboarding date.

A reporting month runs from its first to its last calendar day. A month in which the data stops early is incomplete: it is flagged in trends and left out of forecast history. Relative periods such as last month are measured from today's date, so they can fall after the end of the data, and then no value is reported.

Balances, credit limits and account statuses are current values at the last pipeline load, on 2026-10-22. The source holds no history of them, so they have no trend and no past values.
