# Foresight Phase 4: implementation proposal

Prepared 2026-11-12. Design only: no code, data, schema or configuration was changed. Facts below come from read-only queries run on 2026-11-12:

- Databricks `information_schema` and summary counts on gold tables.
- Healthcare read only through the provisioned tenant catalog.
- The registries in code.

Anything marked *to verify* has not been checked yet.

## 1. Current state

See [FORESIGHT_API_TRACE.md](FORESIGHT_API_TRACE.md) for the full trace. In short:

- **One API serves everything.** A single `GET /context` is fetched once, and every tab reuses it.
- **Overview and Entities draw one block** (`page.tsx:212`).
- **No relationships.** In Databricks mode the in-memory graph has no edges, so relationships are always empty.
- **No metrics or rules.** The API returns fixed empty lists.
- **Tables are unfiltered.** They come from Unity Catalog column metadata read at startup, technical tables included.

Metric definitions currently live in **three places**, and Foresight shows none of them:

| Place | What | Used by |
|---|---|---|
| Domain registries: `banking_registry.py` (19), `domain_registries.py` (Finance 7, Insurance 7, Retail 7, Healthcare 6) | Versioned, compiled to SQL, validated | Ask AI registry path, MCP |
| `services/metrics.py`: 10 `MetricDefinition`s (Banking 1, Finance 5, Insurance 3, Retail 1) | Separate formulas | Ask AI "metric" route when a registry does not answer; MCP `business_metric` |
| `_databricks_dashboard_queries` in `orchestrator.py` | Hand-written dashboard SQL | Dashboard |

**Where they overlap:**

- Six `metrics.py` names also exist in a registry: Banking `transaction_volume`, Finance `total_cash_balance` and `gross_trade_value`, Insurance `written_premium` and `approved_claims`, Retail `net_sales`. Their formulas have not been compared.
- The dashboard shows figures no registry defines: "Closed account rate", "Cancelled policy rate", "Cancelled order rate" and an insurance claims-to-premium ratio by region.

## 2. Proposed architecture

```
Foresight UI: Overview | Entities | Relationships | Metrics | Rules   (one view per tab, fetched when opened)
        |  GET /v1/foresight/{section}?domain=      POST /v1/foresight/refresh (administrators)
FastAPI  app/foresight_api.py (router)
        |  authorization: get_current_user + domains_for + Healthcare tenant check (unchanged)
Service  app/services/foresight.py
        |  assembles each view from:
        |    - domain context definitions, in code (one per domain, same shape as banking_context.py)
        |    - metric registries (banking_registry.py, domain_registries.py): the only formula source
        |    - metadata snapshot (ContextGraphService, built at warm-up), with sync time and drift status
        |    - rule check results (cached, run read-only on refresh)
Sources  Databricks gold: column metadata at warm-up and refresh; rule checks on refresh only
         PostgreSQL: users, grants, tenants (authorization); optional later: approval records (needs a schema decision)
```

**Design decisions:**

1. **Metric formulas live in the registries only.** Foresight reads a metric's formula, SQL, dimensions and sources straight from `banking_registry.py` and `domain_registries.py`, and never stores a copy, so Foresight and Ask AI cannot disagree.
   - Governance fields (owner, validation status, approval status, version, effective date) sit beside each metric in that domain's context module, as `banking_context.metric_view` does now.
   - A test pins each registry's formula fingerprint, as the banking test already does.
2. **The `metrics.py` definitions are reconciled, not silently merged.** For each of the 10:
   - If a registry metric has the same meaning, a live check proves both give the same value. Ask AI then uses the registry metric, and the duplicate is retired *only with your approval*.
   - If no registry equivalent exists (`active_account_cash_balance`, `opened_account_balance`, `executed_trades`, `claims_to_written_premium`), it is listed as a separate governed metric with its own status. It is either added to the registry with a reviewed formula, or kept and labelled.
3. **The dashboard is aligned by mapping first, changing later.** Each dashboard figure gets a `metric_id`, or `null` when no registry metric exists. A live test compares dashboard values with the registry values for the same metric. Dashboard numbers do not change in Phase 4 unless you approve a specific change.
4. **One context module per domain**, with one shared shape: capabilities, glossary, data dictionary, relationships, rules and metric governance.
   - The dataclasses move into a small shared module.
   - `banking_context.py` keeps its public names, so nothing that uses it breaks.
   - New: `finance_context.py`, `insurance_context.py`, `retail_context.py`, `healthcare_context.py`.
5. **No new database tables in Phase 4.** Approvals are recorded in the reviewed code files and their Git history. If you later want approvals or edits from the UI, a separate step proposes a PostgreSQL migration, which needs schema approval.
6. **Metadata is read at warm-up and on an explicit refresh, never on page load.**
   - The snapshot records `synchronized_at` per domain.
   - `POST /v1/foresight/refresh` (administrators only, rate-limited) rebuilds it, re-runs the read-only rule checks, and compares live columns with the dictionary (drift).
   - The existing "Refresh metadata" button calls this endpoint for administrators. For others it only re-fetches, and it is labelled accordingly.

## 3. Domain inventory

### Entities

Each domain's approved business tables come from its registry. Other gold tables are classified as technical:

- **pipeline:** `gold_metadata`, `gold_load_summary`
- **profiling:** `gold_column_profile`, `gold_table_profile`, `gold_data_quality`
- **point-in-time copy:** `snapshot_*`
- **pre-aggregated:** `summary_*`, `agg_*`
- **modelled duplicate:** `dim_*`, `fact_*`

| Domain | Gold tables | Approved business entities (registry) | Not yet approved, data present |
|---|---|---|---|
| Banking | 44 | customers, accounts, transactions, loans, loan repayments, cards, branches (7, plus 10 approved aggregates) | none needed |
| Finance | 38 | accounts, clients, securities, trades (4) | dividends, prices |
| Insurance | 32 | policies, policyholders, claims, premium payments, agents (5) | none |
| Retail | 47 | customers, orders, order items, products, returns, stores (6) | payments, inventory |
| Healthcare | 47 | patients, encounters, claims, facilities, prescriptions (5) | doctors, diagnoses (diagnoses are restricted by policy), lab results |

Banking already has a full dictionary (meaning, key, nullability, sensitivity). The other four would get the same, built from Unity Catalog names and types. Meanings are marked *proposed* until an owner reviews them.

### Relationships verified against the data

Method: for each join, the parent key must be unique among its non-null values. The table gives the child rows with no parent ("unmatched") and the cardinality.

- *Configured* means the join is already used by a registry.
- *Verified* means checked against the data but not used by a registry yet.

No relationship is a declared constraint: Unity Catalog has none.

| Domain | Child → parent (key) | Cardinality | Unmatched | Status |
|---|---|---|---|---|
| Banking | accounts → customers (customer_id) | many-to-one | 25 | configured |
| | accounts → branches (branch_id) | many-to-one | 0 | configured |
| | transactions → accounts (account_id) | many-to-one | 30 | configured |
| | loan repayments → loans (loan_id) | many-to-one | 0 | configured |
| | cards → accounts (account_id) | many-to-one | 0 | verified |
| | cards → customers (customer_id) | many-to-one | 15 | verified |
| | loans → customers (customer_id) | many-to-one | 0 | verified |
| Finance | accounts → clients (client_id) | many-to-one | 20 | configured |
| | trades → accounts (account_id) | many-to-one | 20 | verified |
| | trades → securities (security_id) | many-to-one | 30 | configured |
| Insurance | policies → policyholders (holder_id) | many-to-one | 20 | configured |
| | policies → agents (agent_id) | many-to-one | 0 | configured |
| | claims → policies (policy_id) | many-to-one | 21 | configured |
| | premium payments → policies (policy_id) | many-to-one | 0 | configured |
| Retail | order items → orders (order_id) | many-to-one | 0 | configured |
| | order items → products (product_id) | many-to-one | 30 | configured |
| | orders → stores (store_id) | many-to-one | 0 | configured |
| | orders → customers (customer_id) | many-to-one | 56 | configured; 8 customers have no ID |
| | returns → order items (order_item_id) | many-to-one; at most one return per item | 0 | configured |
| Healthcare | prescriptions → encounters (encounter_id) | many-to-one | 0 | configured |
| | claims → encounters (encounter_id) | one-to-zero-or-one: at most one claim per encounter | 0 | configured |
| | encounters → facilities (facility_id) | many-to-one | 0 | configured |
| | encounters → patients (patient_id) | many-to-one | 56 | verified; 10 patients have no ID |
| | claims → patients (patient_id) | many-to-one | 19 | verified; 14 claims name a different patient from their encounter |

**Notes:**

- *Configured* status was confirmed by listing every join clause in each registry: Banking 4, Finance 2, Insurance 4, Retail 5, Healthcare 3.
- Same-name column pairs whose "parent" is not unique (for example policies → claims) are **rejected**, not shown.

### Metrics, existing and requested

Status key:

- **Registry:** approved in a registry; data-verified; no owner sign-off.
- **Secondary:** only in `metrics.py`.
- **Proposed:** the data supports it, but the definition needs your approval.
- **Pending:** depends on a pending business rule.
- **Unavailable:** the data does not exist.

**Banking.** The 19 registry metrics are unchanged; see `docs/BANKING_CONTEXT_AUDIT.md`.

| Requested | Status |
|---|---|
| Customers and accounts; active, dormant and closed accounts; balances; transaction volume and value; credit limits; loans and repayments | Registry |
| Total account balance | Registry. Includes CLOSED accounts; disclosed in the answer |
| Deposits; active customers; payments | Pending |
| Fraud, risk, outstanding loans, default | Unavailable |

**Finance**

| Requested | Status | Notes |
|---|---|---|
| Accounts; cash balance | Registry: `account_count`, `total_cash_balance` | `metrics.py` also defines `total_cash_balance`, to reconcile |
| Active-account cash balance | Secondary: `active_account_cash_balance` | Candidate to add to the registry |
| Trade volume; gross trade value | Registry: `trade_count`, `gross_trade_value` | `metrics.py` has `executed_trades` (EXECUTED only) and `gross_trade_value`, to reconcile. Trade status: EXECUTED 36,193, CANCELLED 1,192, REJECTED 373 |
| Clients; new clients | Registry | |
| Dividends, security prices | Proposed | Tables exist but are not approved |
| AUM, market value, portfolio value, returns, P&L, financial risk | Unavailable | Refused today, and stays refused. No AUM or notional wording. The non-Databricks dashboard label "AUM / notional" stays out of Databricks mode |

**Insurance**

| Requested | Status | Notes |
|---|---|---|
| Policies; written premium | Registry | Dashboard premium total uses all policies; the registry definition is to be compared |
| Policyholders | Proposed | Count of `gold_policyholders` |
| Premium collected; collection rate | Registry | |
| Claims volume; claimed amount; approved amount | Registry | Claim status: PAID 585, APPROVED 158, SUBMITTED 98, REJECTED 137 |
| Approved and rejected claims | Registry, as `claim_count` by status | A "rejection rate" metric is proposed |
| Loss ratio | **Unavailable**: the registry refuses (no earned premium) | Conflict: `metrics.py` `claims_to_written_premium` and the dashboard ratio compute claims over written premium. Needs a decision: keep as a clearly named "claims to written premium" metric, or remove it |
| Settlement and processing time | *To verify* | Whether settlement dates exist |
| Underwriting and risk | Unavailable unless columns are found | |

**Retail**

| Requested | Status | Notes |
|---|---|---|
| Customers; new customers; orders | Registry | |
| Net sales; units sold | Registry | `metrics.py` has `net_sales`, to reconcile. The dashboard excludes future-dated orders, and the registry definition is to be compared |
| Returns; refund amount | Registry | |
| Products and inventory | Proposed | `gold_products`, `gold_inventory` |
| Payments | Proposed | `gold_payments` status exactly mirrors order status (SUCCESS 17,556 = COMPLETED; FAILED 985 = CANCELLED; REFUNDED 1,466 = RETURNED) |
| Order fulfilment | *To verify* | Shipment or delivery columns |
| Retention, repeat purchase | Pending | Needs a definition |
| Profit, margin | Unavailable | Refused: no agreed cost definition |

**Healthcare** (tenant catalog only)

| Requested | Status | Notes |
|---|---|---|
| Patients; encounters; claims; billed; approved | Registry | |
| Prescriptions | Registry: `prescription_count` | |
| Claim rejection rate | Proposed | Status: APPROVED 3,330, REJECTED 680, PENDING 630. The definition must say whether PENDING is in the denominator |
| Utilization (encounters per patient), providers (doctors) | Proposed | `gold_doctors` not yet approved |
| Clinical measures, diagnoses | Unavailable or restricted | Need clinical and compliance review |

### Business rules

Every rule has a **kind**, so the page never claims more than the code does:

| Kind | Meaning | Example |
|---|---|---|
| `enforced` | Code blocks or changes behaviour; links to the code and its test | Domain isolation, sensitive-field redaction, banking pending-rule refusals, CLOSED-balance disclosure |
| `check` | A read-only SQL count of violations, run on refresh; reported, never enforced | "Approved amount must not exceed claimed amount" |
| `definition` | States how a metric is calculated | "Transaction value adds debits and credits" |
| `pending` | Needs an owner's decision; questions that depend on it are refused or disclosed | Loss-ratio threshold |

Proposed `check` rules, with current violation counts. No thresholds are set: severity and owner are pending.

| Domain | Rule | Violations now |
|---|---|---|
| Finance | Trade price and quantity are positive | 70 |
| Finance | Cash balance is not negative | 0 |
| Finance | Account status is ACTIVE, DORMANT or CLOSED; trade status is EXECUTED, CANCELLED or REJECTED | 0, by construction |
| Insurance | Approved amount ≤ claimed amount | 25 |
| Insurance | REJECTED claims have no approved amount | 0 |
| Insurance | Claim date is on or after policy start | 0 |
| Insurance | Amount paid ≤ amount due | 0 |
| Retail | Refund ≤ item value | 46 |
| Retail | Return date is on or after order date | 13 |
| Retail | Payment status corresponds to order status | 0 mismatches |
| Retail | Stock is not negative; discount between 0 and 1 | *to verify*: column names differ |
| Healthcare | Approved amount ≤ billed amount | 45 |
| Healthcare | Claim patient matches encounter patient | 14 |
| Healthcare | At most one claim per encounter | 0 |
| Healthcare | REJECTED claims have no approved amount | 0 |
| Healthcare | Discharge on or after admission | *to verify* |
| Banking | Existing 19 context rules, plus checks: negative amounts (60), transactions without an account (30), accounts without a customer (25) | as recorded |

**Rules that need your decision:** loss-ratio thresholds, fraud and suspicious-activity conditions, loan eligibility, return eligibility windows, refund policy, KYC completeness, and duplicate-claim tolerance. These are documented only as `pending`, with no invented threshold or compliance requirement.

## 4. API contracts

**Recommendation: dedicated read-only endpoints**, keeping `GET /context` unchanged for the dashboard and older clients.

| Method and path | Who | Returns |
|---|---|---|
| `GET /v1/foresight/overview?domain=` | authorized users | summary counts, sources, sync time, completeness, rule-check summary |
| `GET /v1/foresight/entities?domain=&include_technical=false` | authorized users | business entities with dictionary columns; technical tables only on request |
| `GET /v1/foresight/relationships?domain=` | authorized users | verified relationships only |
| `GET /v1/foresight/metrics?domain=&status=` | authorized users | governed metric views, read from the registry |
| `GET /v1/foresight/rules?domain=&kind=` | authorized users | rules with kind, status and the latest check result |
| `POST /v1/foresight/refresh?domain=` | administrators | rebuilds the snapshot, runs checks, returns drift |

Each response is a Pydantic model with `domain`, `context_version`, `registry_version`, `synchronized_at` and `items`.

Illustrative examples, not observed output:

```json
GET /v1/foresight/relationships?domain=finance
{"domain": "finance", "context_version": "1.0.0", "synchronized_at": "2026-11-12T09:00:00Z",
 "items": [{"child": "gold_trades", "child_column": "security_id", "parent": "gold_securities", "parent_column": "security_id",
            "join": "LEFT JOIN gold_securities s ON s.security_id = t.security_id", "cardinality": "many-to-one",
            "type": "verified_against_data", "unmatched_children": 30, "verified_on": "2026-11-12",
            "explanation": "Each trade is of one security; 30 trades name a security that is not in gold_securities."}]}

GET /v1/foresight/metrics?domain=insurance
{"items": [{"id": "written_premium", "name": "Written premium", "formula": "SUM(premium_amount)", "unit": "INR",
            "source": {"catalog": "insurance", "schema": "gold", "tables": ["gold_policies"], "columns": ["premium_amount"]},
            "dimensions": ["policy_type", "policy_status"], "validation_status": "REGISTRY_APPROVED",
            "approval_status": "OWNER_SIGN_OFF_NOT_RECORDED", "owner": null, "version": "1", "effective_date": null,
            "used_by": ["ask_ai", "mcp"], "dashboard_kpi": "volume"}]}
```

**Authorization:**

- The existing `get_current_user`, `domains_for` and Healthcare tenant check are reused unchanged.
- A user cannot ask for a domain they are not granted.
- Healthcare entities and checks run only in the tenant's catalog.
- Analysts see restricted columns by sensitivity class but not by name. Showing names to administrators is a decision for you.
- No credentials or connection details appear in any response.

## 5. Frontend design

`frontend/app/context/page.tsx` keeps its route. Each tab gets its own component under `components/foresight/`, fetched when first opened, with the domain selector shared.

| View | Content |
|---|---|
| Overview | Domain summary, sources and catalog, entity, relationship, metric and rule counts by status, last sync, drift, rule-check summary. No table list |
| Entities | Business entities first; technical tables behind a filter. Detail panel: meaning, columns with type, key and sensitivity, related metrics, rules and relationships |
| Relationships | Table of verified joins: join condition, cardinality, unmatched count, explanation. Optional simple diagram |
| Metrics | Searchable list with status badges (registry, secondary, proposed, pending, unavailable). Detail: formula, SQL, dimensions, sources, owner, version, dashboard and Ask AI usage |
| Rules | Grouped by kind, with status badges. `check` rules show their last count and run time. `enforced` rules link to the code and its test |

The current shared block at `page.tsx:212` is replaced by these per-tab components. The overview cards each open their own tab.

## 6. Test plan

Each check runs for every domain: Banking, Finance, Insurance, Retail and Healthcare.

| Check | How |
|---|---|
| API shape | Offline Pydantic-model tests per endpoint |
| Formula equals registry | Fingerprint per registry |
| Metric matches dashboard and Ask AI | Live, read-only value comparison per mapped metric |
| `metrics.py` duplicates reconcile | Live comparison |
| Relationship counts still hold | Live (`RYZE_LIVE_TESTS=1`) |
| Rule checks reproduce the counts above | Live |
| Domain access refused across domains | Offline |
| Healthcare: tenant catalog only; refusal without a tenant | Offline and live |
| Empty or unavailable metadata (warehouse down) | Offline |
| Refresh: administrators only, rate limit, cache invalidation | Offline |
| Frontend: each tab renders its own data and its empty state | No frontend test runner exists, so `tsc` plus a manual browser check. Adding a runner needs a dependency, for your approval |
| Regressions | Full backend suite, offline evaluation, banking (62), registry (35) and banking-context (46) evaluations |

## 7. Phases

| Step | Work | Needs approval |
|---|---|---|
| 4a | Shared context shape; Finance, Insurance, Retail and Healthcare context modules (dictionary, verified relationships, rules, metric governance); formula fingerprints | Plan approval |
| 4b | `metrics.py` and dashboard reconciliation report (live, read-only); no behaviour change | Plan approval |
| 4c | `/v1/foresight/*` endpoints and refresh; `/context` unchanged | Plan approval |
| 4d | Frontend views | Plan approval |
| 4e | Tests and live verification across all five domains | Plan approval |
| later | Retiring duplicates; changing dashboard formulas; new registry metrics from the proposed lists; PostgreSQL approval tables | **Separate approval each** |

## 8. Risks and approvals

- **Duplicate metric formulas** (`metrics.py`, dashboard) may give different numbers from the registry. 4b measures this before anything changes.
- **Insurance "loss ratio" conflict**: the registry refuses it, while `metrics.py` and the dashboard compute claims over written premium. A decision is needed.
- **Meanings and rules are proposed, not approved**: there is no business owner sign-off. Everything is labelled by status.
- **Healthcare**: the clinical and compliance review is still outstanding (security finding S10). Only the tenant catalog is read.
- **Refresh runs Databricks queries.** Administrator-only and rate-limited, it is the one action that queries Databricks from Foresight.
- **No frontend test runner.** UI verification is manual unless you approve adding one.
- **Phase 3 changes are still uncommitted**, and Phase 4 builds on them.

**Decisions needed from you:**

1. Dedicated `/v1/foresight/*` endpoints with `/context` kept, as recommended.
2. Approval records kept in code and Git rather than PostgreSQL tables.
3. The Insurance loss-ratio conflict.
4. Whether administrators may see restricted column names.
5. Whether proposed metrics are listed as "proposed" in Foresight now, without being answerable until approved.
