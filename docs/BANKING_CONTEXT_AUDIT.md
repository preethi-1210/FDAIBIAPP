# Banking business context: Phase 1 audit

Audited 2026-11-11 on branch `feature/agentic_ai_mode` at `957c70b`. Read only: no code, schema, data, `.env` or Databricks object was changed. PostgreSQL was read in read-only transactions; Databricks `banking.gold` was read with aggregate `SELECT`s through the application's own read-only path. Nothing personal was printed.

**Not verified here.**

- The question probe in section 6 ran the rule-based planner only. With `BANKING_LLM_PLANNING=true`, a model may plan some questions that the rules leave unclear; that was not exercised.
- `banking_agent.py` and `frontend/app/context/page.tsx` were read in the relevant parts, not line by line.
- No business owner or documentation outside this repository was available, so nothing could be confirmed as the bank's approved definition. "Established" below means established by the data and the existing registry, not by a business owner.

## 1. Summary

Much of what the brief asks for already exists for Banking, in a different shape:

- An approved, versioned **metric registry** with 19 metrics (corrected from 21 in the first version of this report).
- A **planner** that maps questions to metrics.
- A **compiler** that writes parameterized SQL from the registry.
- Parsed **SQL policies**, sensitive-data rules, and refusals for figures the data cannot support.
- **Evidence** attached to every answer.

The main gaps are these:

1. **Vocabulary.** Common banking words such as deposit, inactive, outstanding, payment, joined and processed are not mapped.
2. **The Foresight page.** For Banking it shows a glossary written for the old synthetic demo tables, no relationships, and no metrics or rules.
3. **Missing data.** Several capabilities have no data at all in Databricks: merchant banking, settlement, refunds, income and expense, fraud labels, outstanding balances and loan defaults. The right answer there is to say so, not to approximate.

## 2. How a banking question is answered today

The brief's required workflow, mapped to what exists:

| Brief step | Today | Where |
|---|---|---|
| Authentication and domain authorization | Done. The account, domain grant and role are re-read from PostgreSQL on every request | `auth.py` `get_current_user` |
| Input checks | Done. Normalization, injection check, other-domain check, sensitive-request check, redaction | `orchestrator.py` `_run`, `services/policy.py`, `sensitive_data.py` |
| Intent detection | Done. Rules first: value, breakdown, trend, compare, forecast, anomaly. A model is used only when the rules cannot read the question | `banking_query.parse_question`, `plan_with_llm` |
| Terminology resolution | Partial. Regex aliases on each metric and value aliases on each dimension. No separate glossary or synonym list | `banking_registry.py` |
| Context and metric retrieval | Done for metrics. The registry chooses the metric, source tables, joins and allowed filter values | `banking_registry.py` |
| Knowledge retrieval | Partial. 4 approved Banking documents through hybrid search, used for "what does X mean" questions | `services/knowledge.py`, `backend/knowledge/banking/` |
| Relationship and rule validation | Done inside the registry. Joins are fixed per dimension, and `validate_plan` rejects any dimension, filter or period the metric does not support | `banking_query.validate_plan` |
| SQL generation | Compiled from the registry, not written by a model. Model-written banking SQL is off (`BANKING_GENERATED_SQL=false`) | `banking_query.compile_plan` |
| SQL validation | Done. Parsed guardrail, banking select policy, compiled-SQL sensitive check, and now the domain allowlist (shadow) | `guardrail.py`, `sql_policy.py`, `domain_sql_policy.py` |
| Execution | Databricks `banking.gold`, pooled, with a statement timeout and circuit breaker | `warehouse.py` |
| Result validation | Partial. Breakdowns are reconciled to the total; empty results are reported as such; fixed-rule anomaly checks exist | `banking_agent._reconcile`, `_transaction_checks` |
| Evidence and provenance | Done. Registry version, definition, sources, period, coverage and data-quality notes travel with each answer | `banking_agent.py` around line 486 |
| Audit | Done. `query_history` holds the question, SQL, tables, row count and audit ID; LLM calls go to `llm_usage_events` | `orchestrator._respond`, `subscriptions.record_llm_events` |

**A correction to the brief's background.** With `WAREHOUSE=databricks`, which is your current setting, **the dashboard does not read PostgreSQL.** `orchestrator._dashboard_metrics_remote_warehouse` sends the dashboard's banking queries to Databricks gold. PostgreSQL holds application data: users, grants, conversations, query history, reviews, plans and the glossary. No banking figure is currently served from PostgreSQL.

## 3. Data sources

### Databricks `banking.gold` (authoritative today)

7 entity tables and 10 aggregates are approved; 27 other tables (`dim_`, `fact_`, `snapshot_`, `summary_`, metadata, profiling) are excluded by the registry. Unity Catalog holds no column comments and no keys.

| Table | Rows | Coverage | Notes |
|---|---|---|---|
| `gold_customers` | 5,000 | onboarded 2010-01-01 to 2025-11-18 | 4,985 distinct IDs (15 null); `kyc_status` VERIFIED 4,266 / PENDING 588 / REJECTED 146; `customer_segment` MASS, AFFLUENT, PREMIUM |
| `gold_accounts` | 8,000 | opened 2010-01-02 to 2026-06-06 | ACTIVE 6,825, DORMANT 783, CLOSED 392; types CURRENT, SALARY, SAVINGS; current balance only, no balance history; 25 accounts with no matching customer |
| `gold_transactions` | 100,011 | **one day, 2026-03-15** | SUCCESS 92,995 / FAILED 5,043 / PENDING 1,973; DEBIT 65,107 / CREDIT 34,864 / TRANSFER 40; channels ATM, NETBANKING, IMPS, POS, UPI, NEFT (no RTGS); `merchant_category` is a spend category, not a merchant; 30 with no matching account; no duplicate IDs |
| `gold_loans` | 2,000 | disbursed 2024-01-01 to 2025-02-04 | every loan ACTIVE; no outstanding balance, approval or default field |
| `gold_loan_repayments` | 39,570 | due 2024-01-31 to 2026-03-15 | `emi_due`, `amount_paid`, `paid_date`; all link to a loan |
| `gold_cards` | 6,000 | issued to 2023-06-24 | CREDIT/DEBIT; ACTIVE/EXPIRED/BLOCKED |
| `gold_branches` | 20 | | region, state, city; all accounts link to a branch |

### PostgreSQL `banking` schema (present, unused)

7 tables named `apex_bank_data_*`: raw CSV imports in which **every column is `text`**, with **no primary or foreign keys** and no transactions table. Customers exist as two snapshots: 2026-03-14 with 5,050 rows and 2026-03-15 with 100 rows, overlapping on 102 rows.

**Reconciliation, read-only:** accounts match `gold_accounts` exactly, in count and in balance per status, to the paisa. Loans (2,000), repayments (39,570) and cards (6,000) match in count. Customers do not match: 5,050 + 100 against 5,000 in gold. These tables look like the files the gold layer was built from. No application code reads them.

### PostgreSQL `public` (demo data)

`accounts` (100), `customers` (320), `loans` (37) and `transactions` (542) are synthetic rows created by `seed.py` in demo mode. They are not the bank's data. `orders`, `order_items` and `products` are Retail demo data; per your brief, they are not banking entities.

## 4. The ten capabilities against the data

| Capability | Answerable today | Possible with a definition you approve | Not possible from current data |
|---|---|---|---|
| A Customers | customer count, new customers by onboarding date, by segment, city, state | "active customer" (for example, has at least one ACTIVE account); customer growth rate from onboarding counts | inactivity by behaviour (transactions cover one day); retention and churn (no closure dates or history) |
| B Accounts | count by status and type, active and dormant counts, total and average balance, accounts opened by date | whether CLOSED balances (₹9.97 crore) belong in "total balance" | account closures over time (no closure date); balance growth (no history) |
| C Transactions | count, value, average, by type, channel, status, city, category | treatment of the 60 negative amounts (reversals?) | trends, "today" or "why it decreased": one business day only |
| D Deposits | none by that name | "total deposits" as the balance of ACTIVE and DORMANT accounts, or of all accounts; "money deposited" as successful CREDIT transactions | deposit growth, year over year, interest paid |
| E Loans | loans disbursed, principal, interest rate, repayments due and collected, collection rate, missed instalments | none safely | outstanding balance (no field; principal minus paid is wrong because payments include interest); approvals and applications; default and NPL rates (every loan ACTIVE); credit risk |
| F Payments | transactions by channel and status, failure rate | "payment" meaning a transaction on a payment channel (UPI, NEFT, IMPS, POS); payment success rate as status SUCCESS | RTGS (no rows); settlement; reconciliation |
| G Risk and fraud | fixed-rule data checks (negative amounts, unmatched accounts) | anomaly indicators: high frequency per account, repeated failures, high-value transfers, using thresholds you set. These are anomalies, never fraud | confirmed fraud (no labels or cases); credit risk (no scores or delinquency) |
| H Financial performance | none | none | interest income and expense, NIM, operating expense, fees, revenue: no ledger or income data. Correctly refused today |
| I Compliance | KYC status counts (PENDING 588, REJECTED 146) | monitoring-threshold breaches, once thresholds are approved | AML cases, investigations, regulatory reports |
| J Merchant banking | none | none | no merchant, acquiring, order, refund or settlement tables in banking. `merchant_category` is a spend category |

## 5. The seven context components

| Component | State | Detail |
|---|---|---|
| 1 Business glossary | **Wrong for Banking** | The 7 banking rows in the PostgreSQL `glossary` table describe the demo tables: "npl" as delinquent status, "churn" as `customers.churned`, "loan" as `outstanding_balance`. None of these exist in gold, and "npl" contradicts the registry's refusal. Finance already replaces its rows with a gold glossary in code (`domains.finance_gold_glossary`); Banking has nothing equivalent |
| 2 Metric registry | **Exists, 19 metrics** | Each has an ID, label, definition, source tables, joins, measures, aggregation, date column and meaning, data-quality notes, version, roles and an example. Missing fields from the brief: business owner, validation status, explicit freshness, catalog and schema per metric (one constant today) |
| 3 Data dictionary | Partial | `knowledge/banking/data_dictionary.md` is prose. No per-column meaning, key, nullability or sensitivity table. Sensitivity is computed by regex in `sensitive_data.py` and recorded in the new `sql_allowlists/banking.json` |
| 4 Relationship registry | Implicit | The joins are fixed inside registry dimensions (account to branch, account to customer, transaction to account, repayment to loan). Section 7 verified them against the data. The Foresight page reads foreign keys from the database, and Unity Catalog has none, so it shows **no** relationships |
| 5 Business rules | Partial | Documented in `knowledge/banking/business_rules.md` and in metric definitions: all statuses included unless asked; debit and credit added, not netted; negative amounts kept; current balances only. Not a structured list with approval status |
| 6 Approved query examples | Implicit | Every registry metric compiles to approved SQL. No list of question-to-SQL pairs exists |
| 7 Governance and security | Mostly done | See section 9 |

## 6. Terminology: the brief's 46 example questions

Rule-based planner only, offline:

- **13 map directly to an approved metric.** Examples: "How many active accounts", "total account balance", "average transaction amount", "loan repayment collected".
- **7 are refused correctly.** These are default rate, interest income, NIM and revenue.
- **The rest ask for clarification or match nothing.** Typical reasons:
  - Words with no synonym: deposit, inactive, outstanding, payment, joined, processed.
  - Data that does not exist: merchants, settlement, refunds, income.
  - Analysis types with no approved definition: growth rate, "why", risk.

Two mappings are risky as they stand:

- **"merchants" maps to `merchant_category`.** "Which merchants generate the highest value" would be answered by spend category, which is not what was asked.
- **"How much money is held by customers"** has no mapping. Mapping it to total account balance needs your rule on CLOSED accounts.

## 7. Relationships verified against the data

| Join | Key | Result |
|---|---|---|
| accounts to customers | `customer_id` | 25 accounts have no customer |
| accounts to branches | `branch_id` | all match |
| transactions to accounts | `account_id` | 30 transactions have no account |
| cards to accounts and customers | `account_id`, `customer_id` | used only as filters today |
| loans to customers | `customer_id` | all match |
| repayments to loans | `loan_id` | all match |
| accounts to payments, merchants to payments, payments to refunds, orders to order items (banking) | | **no such tables in banking** |

The registry uses `LEFT JOIN` everywhere, so unmatched rows show as "unmatched" instead of disappearing, and breakdowns reconcile to the total. All IDs are unique, so these joins do not double-count, apart from the 15 null customer IDs.

## 8. Business rules

**Established by the data and registry:**

- Account status values are ACTIVE, DORMANT and CLOSED; "active" means `status = 'ACTIVE'`.
- Transaction totals include every status unless one is asked for, and add debits and credits.
- Balances come from `gold_accounts.balance`, never from summed transactions.
- Loan default and NPL cannot be calculated.
- Income and profitability cannot be calculated.

**Need your approval before use:**

1. Whether "total balance" and "deposits" include CLOSED accounts.
2. What "active customer" means.
3. Whether "payment" means a transaction on a payment channel, and which channels count.
4. How the 60 negative-amount transactions are treated. There is no reversal status, so "reversed" and "cancelled" cannot be identified, and PENDING is neither success nor failure.
5. Anomaly thresholds for frequency, high value and repeated failures. Results must be labelled as indicators, not fraud.
6. Whether a "growth rate" compares onboarding counts period over period.

## 9. Security and governance

**Already in place:**

- Domain isolation through account re-checks and catalog mapping.
- Admin and business-user separation.
- Parsed SQL validation before every execution.
- Dangerous functions and statements blocked.
- Bound parameters.
- Sensitive columns restricted or redacted, and CVV never returned.
- Prompt-injection and other-domain checks.
- Audit in `query_history`.
- Provenance in the evidence attached to each answer.

**Gaps relevant to this work:**

- The Foresight API filters sensitive columns but shows the wrong banking glossary to every user.
- The new domain allowlist is still in shadow mode, waiting on your review.
- Databricks access uses a personal token with no column masks (S3), and PostgreSQL uses a superuser (S2).
- Your brief makes `postgres` the mandatory username, while the approved Phase 2 plan (R8) would move the application to a restricted role. That is your call later; nothing changes now.

## 10. Source selection

**Today:** one global `WAREHOUSE` setting, so every banking figure comes from Databricks gold. There is no fallback, and a Databricks outage returns 503. This matches your earlier decision to keep Databricks as the analytical source.

**Recommendation:**

- Keep Databricks gold as the only source for banking figures.
- Do not route anything to the PostgreSQL `banking` schema. It duplicates gold for accounts, loans and cards, has no transactions, has no keys, stores numbers as text, and has a different customer count, so serving from both would risk the conflicting answers your brief wants to avoid.
- Record the source per metric explicitly, as `source: databricks.banking.gold`, so a future second source is a deliberate change.
- Add a read-only reconciliation test comparing PostgreSQL `banking` accounts with gold, which match today. It would detect drift without serving any figure from PostgreSQL.
- Never fall back silently. If gold is unavailable, say so.

## 11. Proposed plan for Phases 2 to 5

All of this extends existing modules. Nothing new is installed, no table is created or changed, and no PostgreSQL or Databricks data is written.

| Phase | Work | Files |
|---|---|---|
| 2 Foundation | A banking context definition in code, alongside the registry. It holds:<br>• capabilities A to J, each with status (available, needs approval, unavailable) and the reason<br>• a glossary with synonyms, each term pointing to a metric or a dimension value<br>• a data dictionary for the 7 gold tables: type from Unity Catalog, meaning, keys, nullability, sensitivity from the existing rules, verified or proposed<br>• relationships from section 7 with their orphan counts<br>• business rules with approval status<br><br>Add the brief's missing metric fields to `Metric`: owner, validation status, freshness, source catalog and schema. Defaults keep existing metrics unchanged | new `app/services/banking_context.py`; `banking_registry.py` (fields only) |
| 2 Glossary fix | Serve the banking glossary from the context definition, as Finance does, instead of the PostgreSQL demo rows. **The PostgreSQL rows are not edited** | `main.py` `/context`, `context_graph.py` |
| 3 Workflow | Feed glossary synonyms into the planner, so "deposit", "inactive" and "joined" resolve. Mark "merchants" as a spend category or refuse. Add refusals for merchant banking, settlement, refunds, RTGS, outstanding balance and approvals. Generate approved examples by compiling each metric's example question. Add `capability` and `rule` entries to evidence. New metrics (active customers, deposits, payment success rate, anomaly indicators) only after you approve section 8 | `banking_query.py`, `banking_registry.py`, `banking_agent.py`, `knowledge/banking/*.md` |
| 4 Foresight UI | The page already has sections for glossary, metrics, rules, relationships and source metadata. Fill them from `/context` for Banking, and add capabilities, dictionary, examples and validation status to the same page. Business users see their own domain only, with sensitive columns hidden as now. Administrators see approval status. Editing definitions in the UI needs a database table, so it is proposed as a later, separately approved step | `main.py` `/context` (fields added, none removed), `frontend/app/context/page.tsx` |
| 5 Tests | Glossary, metric, rule and relationship retrieval; terminology mapping for the 46 questions; refusal tests for every unavailable capability; SQL for active accounts, volume, value, growth, deposits and payment failures; cross-domain and injection cases. Live, read-only checks: reconciliation, orphan counts, status totals, and registry and banking evaluations against gold | `tests/test_banking_context.py`, existing evaluation runners |

## 12. Decisions needed from you

1. **Approve the plan in section 11, or change it.**
2. **The six business rules in section 8.** Any you leave unapproved stay as clear refusals.
3. **Source selection.** Confirm Databricks gold is the only banking source, and that the PostgreSQL `banking` schema is used for reconciliation only.
4. **Editing definitions in the UI.** Phase 4 can show definitions read-only now. Letting administrators edit them needs a new table, which is a schema change.

## Phase 2 status (2026-11-11)

Implemented: `backend/app/services/banking_context.py`, with:

- 10 capabilities
- 43 glossary terms
- a governed view of all 19 registry metrics
- the brief's 15 requested metrics, with their status
- a data dictionary for the 17 approved tables
- 7 verified relationships
- 19 rules, 9 of them pending business approval

The verified gold glossary now replaces the PostgreSQL demo rows for `/context` and the context graph. Tests are in `backend/tests/test_banking_context.py`. Nothing is committed.
