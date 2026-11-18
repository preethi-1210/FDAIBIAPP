# Foresight follow-up: proposed changes

Proposed 2026-11-13. **Not implemented.** No code, formula, data, schema or configuration has changed. All figures come from read-only runs on 2026-11-12 and 2026-11-13; see [FORESIGHT_REVIEW.md](FORESIGHT_REVIEW.md).

**Rules every change below keeps:**

- No approved registry formula changes.
- Authorization (`authorize_context_domain`) and Healthcare tenant isolation are reused unchanged.
- No new tables or dependencies.
- The page layout, theme and components stay the same.

## 1. The three labelling fixes

### F1: Glossary badges outside Banking

| | |
|---|---|
| Problem | Finance's 8 terms show "Verified", and the demo definitions for Insurance, Retail and Healthcare show "Proposed". The badge is derived from column mapping, not approval |
| Change | Non-Banking terms get **"Mapped to source"** or **"Not mapped"**, never an approval word. When any term is not mapped, a one-line note says: "These definitions describe tables that are not in {catalog}.{schema}; they have not been reviewed for this source." Banking keeps its real statuses (Verified, Pending approval, Not in data) |
| Files | `frontend/components/foresight/OverviewView.tsx` (mapping); `frontend/components/foresight/StatusBadge.tsx` (two neutral labels) |
| Backend | none |

### F2: Banking Accounts key hidden

| | |
|---|---|
| Problem | `account_id` is classed as restricted, so the Accounts entity shows no primary key. Counted 2026-11-13, eight key columns in three domains are affected: Banking 3 (`account_id` on accounts, cards and transactions), Finance 2 (`account_id` on accounts and trades), Insurance 3 (`policy_id` on policies, claims and premium payments). Retail and Healthcare have none |
| Change | Key columns (PK or FK) are listed **by name** with the label "Restricted identifier: values are never shown to business users". Restricted columns that are not keys (names, email, phone, PAN, address, date of birth and so on) stay hidden, exactly as now |
| Why this keeps the protection | The data protection works on *values*: SQL refusal for business users, and redaction. Key *names* are already public on the Relationships tab (for example `gold_transactions.account_id = gold_accounts.account_id`) and in the registry joins. No value is exposed, and no protection in `sensitive_data.py` changes |
| Files | `backend/app/services/foresight.py` `_entities` (adds `restricted: true` for sensitive key columns; the hidden count excludes them); `frontend/lib/foresight.ts` (one optional field); `frontend/components/foresight/EntitiesView.tsx` (label) |

### F3: "Dictionary reviewed" wording

| | |
|---|---|
| Change | Banking: "Types and keys checked against Unity Catalog on 2026-11-11; column meanings proposed, not owner-approved." Other domains: "Columns from Unity Catalog; business meanings not yet documented" (unchanged) |
| Files | `backend/app/services/foresight.py` (one string) |

## 2. Proposed Insurance metric: Approved claims to written premium

| Field | Value |
|---|---|
| ID | `approved_claims_to_written_premium` |
| Name | Approved claims to written premium (not a loss ratio) |
| Status | **Proposed**: awaiting insurance owner approval |
| Formula | registry `approved_claim_amount` ÷ registry `written_premium` × 100 |
| Numerator | `SUM(approved_amount)` where `claim_status` is APPROVED or PAID, by `claim_date` (registry, unchanged) |
| Denominator | `SUM(premium_amount)` where `policy_status` ≠ CANCELLED, by `start_date` (registry, unchanged) |
| Value 2026-11-13 | 96.15% (112,132,728.07 ÷ 116,622,300) |
| Answered today by | Ask AI metric route and MCP, through `metrics.py` `claims_to_written_premium`. That implementation was shown to produce the same numerator, denominator and ratio |
| Not | an earned-premium loss ratio. The registry's loss-ratio refusal (`LOSS_RATIO_LIMITATION`) stays as it is and already points users to this metric by name |

**Where it lives:** a short list of proposed derived metrics in `foresight.py`. It is made only of registry metric IDs, so its formula cannot drift from the registry. It is **not** added to the approved insurance registry, because approval belongs to the owner. The Metrics tab shows it with a "Proposed" badge, its parts, and "Answered by: Ask AI and MCP, via `metrics.py`".

**Files:**

- `backend/app/services/foresight.py`: the proposed-metric list and its view.
- `frontend/lib/foresight.ts`: a `"proposed"` status.
- `StatusBadge.tsx`: the label.
- `MetricsView.tsx`: a status filter option.

## 3. Reconciling the dashboard with the registries (plan only)

| Difference | Cause, verified | Proposed alignment | Effect on the number |
|---|---|---|---|
| Insurance headline premium: dashboard 120,624,750, registry 116,622,300 | The dashboard sums **all** policies, including 162 CANCELLED (4,002,450). The registry excludes CANCELLED | **Option A (recommended):** the dashboard uses the registry's `written_premium` filter (non-cancelled). **Option B:** keep the dashboard formula and relabel the figure "Premium incl. cancelled policies (not written premium)". Either needs the insurance owner to confirm whether cancelled premium counts as written | A: −4,002,450. B: none |
| Insurance ratio by state: dashboard 146.37%; same formula, unjoined 150.78% | Inner joins to policyholders drop 20 policies (359,950 premium) and 28 claims (5,676,921.19) | Use a LEFT JOIN with an "Unmatched" state, so the state rows add up to the total. Then decide the numerator: keep "Claimed amount to written premium" (labelled, not governed) or switch to the proposed metric (96.15%) after approval | Total becomes 150.78% (claimed), or 96.15% (approved, after approval) |
| Retail net sales: dashboard 855,388,282.83, registry 855,745,065.02 | Headline and breakdown SQL inner-join `gold_products` and `gold_stores`; 30 order lines have no product (356,782.19, all in 2 orders) | Headline figures use the registry's own tables (order items joined to orders only). Category and region breakdowns use LEFT JOINs with an "Unknown product" bucket, as the registry does | +356,782.19, matching the registry |
| Retail orders: dashboard 18,995, registry 18,997 | Same join: the 2 orders whose only lines lack a product | As above | +2, matching the registry |

**Also mapped, without changing numbers:** the dashboard figures that have no registry metric.

- **"Customers":** Banking 3,970 counts account holders, against 5,000 customer records in the registry. Retail 3,976 counts customers who ordered, against 4,000. Insurance 2,338 counts policyholders with a policy.
- **Churn proxies and risk ratios.**

Each gets a plain label saying what it counts, for example "Customers with an account", and is listed in Foresight as "Dashboard indicator, not a governed metric".

**Files, when approved:**

- `backend/app/services/orchestrator.py`: `_databricks_dashboard_queries` for Insurance and Retail only.
- `backend/tests/test_databricks_dashboard.py`: its SQL expectations.
- Dashboard labels: the dashboard response text fields, plus `frontend/app/dashboard/page.tsx` only where a label is hard-coded.

Banking, Finance and Healthcare dashboard figures already match and are not touched.

## 4. The older `metrics.py` definitions: audit and deprecation

### Where they are used

| Consumer | Use | Reached in practice? |
|---|---|---|
| Ask AI metric route (`orchestrator._answer_from_route` → `tools.metrics.calculate` → `match_metric`) | Answers when the domain registry does not | Rarely: the registry answers first. Of the 9 phrasings tested, the registry answered 7; only "balance of accounts opened" and "approved claims to written premium" reach this route |
| Banking, via the same route | `transaction_volume` (a rupee amount) | Only if `BANKING_AGENT_ENABLED=false` |
| MCP tool `business_metric` (`mcp_server.py:153`) | Calls `metrics.calculate` directly, **without the registry** | Yes: every MCP `business_metric` call uses these definitions |
| Knowledge search (`knowledge.py:147`) | Indexes each older definition as a "metric registry" document, next to the registry's own | Yes: definition questions can return two entries for one metric |
| Foresight (`foresight.py`) | Shows them as "Secondary" | Yes |
| `domain_registries.py:12`, `knowledge.py:185` | Import `LOSS_RATIO_LIMITATION` text only | Yes, text only |
| Tests | `test_agentic_platform.py`, `test_finance_queries.py`, `test_finance_retail_validation.py`, `test_foresight.py`; `evals/run_eval.py` | Yes |

### Recommendation per definition

| Definition | Registry equivalent, value proven equal | Recommendation |
|---|---|---|
| banking `transaction_volume` (rupee amount) | `transaction_amount` | **Deprecate.** Its name clashes with the registry's count metric |
| finance `total_cash_balance` | `total_cash_balance` | **Deprecate**: duplicate |
| finance `active_account_cash_balance` | `total_cash_balance`, ACTIVE | **Deprecate**: the registry answers it with a filter |
| finance `executed_trades` | `trade_count`, EXECUTED | **Deprecate** |
| finance `gross_trade_value` | `gross_trade_value` | **Deprecate**: duplicate |
| insurance `written_premium` | `written_premium` | **Deprecate**: duplicate |
| insurance `approved_claims` | `approved_claim_amount` | **Deprecate**: duplicate |
| retail `net_sales` | `net_sales` | **Deprecate**: duplicate |
| finance `opened_account_balance` | Value equals `total_cash_balance`, but it reads `agg_accounts_daily` grouped by opening date, which the registry cannot express | **Keep as secondary** until a finance owner decides |
| insurance `claims_to_written_premium` | none | **Keep.** It implements the proposed metric in section 2 |

### Deprecation steps

Nothing is deleted.

1. **Mark, no behaviour change.**
   - Add an optional `deprecated_by: str | None = None` to `MetricDefinition` and set it on the 8 definitions. No formula changes.
   - Foresight shows "Deprecated: use {registry metric}".
   - A test asserts that each deprecated definition and its replacement compile to the same aggregation and filters.
2. **Stop serving duplicates, behaviour change for your approval.**
   - Knowledge search skips deprecated definitions, so a term returns the registry definition once.
   - MCP `business_metric` tries the domain registry first and uses `metrics.py` only for the two kept definitions.
   - Ask AI is unchanged, since the registry already answers first.
3. **Remove**, later and separately approved: after owner confirmation and one release with no deprecated definition answering a question. Logs show this, because each answer records its source.

**Files for steps 1–2:**

- `backend/app/services/metrics.py`: a field only.
- `backend/app/services/knowledge.py`.
- `backend/app/mcp_server.py`.
- `backend/app/services/foresight.py`.
- `frontend/components/foresight/MetricsView.tsx` and `StatusBadge.tsx`.
- Tests.

## 5. One definition everywhere

| Surface | After these changes |
|---|---|
| Ask AI | Registry first, unchanged. The metric route serves only the two kept definitions in practice |
| MCP | Registry first (step 2), then the kept definitions |
| Foresight | Registry metrics (approved), the proposed metric, kept secondary definitions, deprecated definitions marked, and dashboard indicators listed as not governed |
| Dashboard | Every headline figure that represents a registry metric carries its `metric_id` and the registry definition text in its existing detail line. Insurance and Retail numbers are aligned once approved (section 3) |
| Tests | A live check (`RYZE_LIVE_TESTS=1`) compares each mapped dashboard figure with the registry value; offline tests check the mapping |

**Files:**

- `orchestrator.py`: adds `metric_id` and a definition to the dashboard response. Fields are only added, so the response stays compatible.
- `frontend/app/dashboard/page.tsx`: the existing detail text reads the definition when present.
- `foresight.py`: the dashboard mapping.

## 6. Test plan

| Area | Tests |
|---|---|
| F1 | No frontend test runner exists, so `tsc` plus a browser check: badges read "Mapped to source" or "Not mapped" and never "Verified" outside Banking |
| F2 | Backend: a key column flagged restricted is listed with its key; non-key restricted columns (email, names, phone, PAN, address, date of birth, expiry) are never listed. The existing `test_sensitive_columns_are_never_listed` is extended, not weakened |
| F3 | String assertion |
| Proposed metric | Its parts exist in the registry; its formula is built from them; status is "proposed"; it is not in the approved registry. A live check confirms the `metrics.py` value equals registry approved ÷ written |
| Deprecation | Each deprecated definition maps to an existing registry metric with equal aggregation and filter (offline), and equal value (live). Knowledge returns one definition per term. MCP `business_metric` uses the registry first (fake executor). Existing tests referencing older definitions keep passing, because nothing is removed |
| Dashboard alignment (once approved) | Updated SQL expectations in `test_databricks_dashboard.py`; live equality of dashboard and registry for every mapped figure, all five domains; Healthcare in its tenant catalog only |
| Access | `test_foresight.py` cross-domain and tenant tests, unchanged and re-run |
| Regression | Full backend suite (615 passed, 11 skipped today); `tsc`; banking (63), registry (35) and banking-context evaluations; the read-only reconciliation script from the review |

## 7. Order and approvals

| Step | Changes behaviour? | Needs |
|---|---|---|
| 1. F1, F2, F3 | Labels only | This approval |
| 2. Proposed Insurance metric in Foresight | Display only | This approval |
| 3. Deprecation step 1 (mark) | No | This approval |
| 4. Deprecation step 2 (knowledge, MCP) | Yes: fewer duplicate answers | Your explicit approval |
| 5. Dashboard metric mapping and definition text | Text only | Your explicit approval |
| 6. Dashboard Insurance and Retail number alignment | Yes: numbers change | Insurance and retail owner confirmation |
| 7. Removal of deprecated definitions | Yes | Later, separately |
