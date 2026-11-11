# Banking business rules and data quality

Transaction amounts add debits and credits together rather than netting them, and they include every status unless a status is asked for. Of 100,011 transactions, 92,995 succeeded, 5,043 failed and 1,973 are pending, so a total across all statuses is larger than the value of completed transactions.

Transaction volume is reported as a count together with its total amount, because the term is used for both.

Sixty transactions have negative amounts and remain in totals. Thirty transactions refer to an account that does not exist in gold_accounts, so they appear as unmatched in breakdowns by region, state, account type or customer segment.

Fifteen accounts have no recorded balance and are left out of balance totals and averages. Fifteen customer records have no customer identifier, and twenty-five accounts refer to a customer that is not present, so they appear as unmatched in breakdowns by segment. Twenty customer records have a date of birth in the future.

All 2,000 loans have status ACTIVE, so non-performing loans and default rates cannot be derived. From 2025-07 some instalments are recorded with amount_paid 0, so the amount collected falls below the amount due and the collection rate declines.

Between the bronze and silver layers, 200 of the 100,211 raw transaction records were removed by the pipeline; the reason is not recorded in the gold schema.

Profitability, income, churn, credit scores and fraud labels are not present in this data.
