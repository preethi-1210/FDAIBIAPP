# Foresight implementation review

Reviewed 2026-11-13. No code, data, schema or configuration was changed by this review.

**How the evidence was gathered:**

- The real API handlers were called in-process.
- The dashboard's own SQL, `services/metrics.py` and the registries' compiled SQL were run read-only against Databricks gold. Healthcare used only its tenant catalog.
- The UI code was read; no browser was available.

**Not verified:** how the page looks in a browser. Section 7 is the checklist for that.

## 1. All five tabs, all five domains

Source: `GET /context/foresight`, plus `GET /context` for the glossary outside Banking.

| | Banking | Finance | Insurance | Retail | Healthcare |
|---|---|---|---|---|---|
| **Overview:** source and last load | banking.gold, loaded | finance.gold, loaded | insurance.gold, loaded | retail.gold, loaded | healthcare.gold (tenant catalog), loaded |
| Overview: capability cards | 6 cards over 10 context capabilities | none | none | none | none |
| Overview: glossary | 43 terms (31 verified, 6 pending, 6 not in data) | 8 | 6 | 5 | 8 |
| **Entities:** approved tables | 7, plus 10 aggregates | 4 | 5 | 6 | 5 |
| Entities: metadata loaded | 17 of 17 | 4 of 4 | 5 of 5 | 6 of 6 | 5 of 5 |
| Entities: business definitions | 17 tables, 133 of 133 columns | 0 | 0 | 0 | 0 |
| Entities: keys shown (PK, FK) | 6, 5 | 2, 2 | 2, 2 | 5, 5 | 3, 5 |
| Entities: sensitive columns hidden | 14 | 8 | 11 | 5 | 8 |
| Entities: technical tables | 27 | 34 | 27 | 41 | 42 |
| **Relationships** (configured / verified) | 7 (4 / 3) | 3 (2 / 1) | 4 (4 / 0) | 5 (5 / 0) | 5 (3 / 2) |
| **Metrics** (registry / secondary) | 20 (19 / 1) | 12 (7 / 5) | 10 (7 / 3) | 8 (7 / 1) | 6 (6 / 0) |
| **Rules** | 42 | 11 | 14 | 10 | 12 |

**Access:** Healthcare was served only to a tenant-assigned user. An administrator without a tenant was refused (403).

**Findings needing a fix (each needs your approval):**

| # | Finding | Evidence | Proposed fix |
|---|---|---|---|
| F1 | Glossary badges outside Banking show mapping, not approval. Finance's 8 code-defined terms show "Verified". The PostgreSQL demo definitions for Insurance, Retail and Healthcare show "Proposed", although they describe demo tables, not gold | `OverviewView.tsx` maps `mapping_status` "mapped" to VERIFIED and anything else to PROPOSED | Show "Mapped to source" or "Not mapped (demo definition)" instead of approval words |
| F2 | Banking Accounts shows no primary key: `account_id` is classified as sensitive, so it is hidden | 6 PKs shown for 7 Banking entities | Show key columns by name with a "restricted" note, or state "key hidden (sensitive)" |
| F3 | Banking entities say "Dictionary reviewed against Unity Catalog", but the column meanings are proposed, not owner-approved | `foresight.py` `_entities` validation text | Change to "Types and keys checked against Unity Catalog; meanings proposed" |

## 2. The ten older metric definitions

`services/metrics.py` is used by the Ask AI metric route and the MCP tool `business_metric`. Each was run against its registry counterpart:

| Older definition | Registry counterpart | Formula | Value |
|---|---|---|---|
| banking `transaction_volume` | `transaction_amount` | Same: `SUM(amount)`, all statuses | Equal: 251,829,230.04 |
| finance `opened_account_balance` | `total_cash_balance` | Different source: `agg_accounts_daily`, against `gold_accounts` | Equal: 3,016,849,156.56 |
| finance `total_cash_balance` | `total_cash_balance` | Same | Equal |
| finance `active_account_cash_balance` | `total_cash_balance` with status ACTIVE | Same, as a filter | Equal: 2,625,316,070.84 |
| finance `executed_trades` | `trade_count` with status EXECUTED | Same, as a filter | Equal: 36,193 |
| finance `gross_trade_value` | `gross_trade_value` | Same | Equal: 14,411,204,517.99 |
| insurance `written_premium` | `written_premium` | Same (non-cancelled) | Equal: 116,622,300 |
| insurance `approved_claims` | `approved_claim_amount` | Same (APPROVED or PAID) | Equal: 112,132,728.07 |
| retail `net_sales` | `net_sales` | Same SQL | Equal: 855,745,065.02 |
| insurance `claims_to_written_premium` | **none** | Approved claims ÷ non-cancelled written premium | 96.15% |

**Conclusions:**

- **No value conflicts.** All nine paired definitions give exactly the registry's value.
- **Conflict 1, a name:** the older `transaction_volume` is a **rupee amount**, while the registry's `transaction_volume` is a **count** with amount. The same name means two things.
- **Conflict 2, no counterpart:** `claims_to_written_premium` has no registry equivalent (section 3).
- **Duplicates:** six definitions duplicate a registry metric outright, and two are registry metrics with a status filter applied. These could be retired in favour of the registry, but only with your approval.

## 3. The Insurance "loss ratio" conflict

| Where | Definition | Value |
|---|---|---|
| Registry | **Refuses** "loss ratio": no earned premium or incurred losses exist | none |
| `metrics.py` (Ask AI, MCP) "Approved claims to written premium" | APPROVED or PAID `approved_amount` ÷ non-cancelled `premium_amount` | **96.15%** |
| Dashboard "Claimed amount to written premium" | All `claimed_amount` ÷ non-cancelled premium, grouped by policyholder state | **146.37%** |
| The same dashboard formula without the state grouping | registry `claimed_amount` ÷ registry `written_premium` | 150.78% |

**Why the dashboard shows 146.37% rather than 150.78%:** its inner joins to policyholders drop 20 policies with no policyholder (premium 359,950) and 28 claims (5,676,921.19). Both differences were reproduced to the paisa.

**A second dashboard difference:** the headline premium, 120,624,750, includes the 162 CANCELLED policies (4,002,450). The registry's `written_premium` is 116,622,300.

**Recommendation.** One governed metric, built only from two existing registry metrics, with no formula changed:

- **ID:** `approved_claims_to_written_premium`
- **Name:** "Approved claims to written premium (not a loss ratio)"
- **Formula:** registry `approved_claim_amount` ÷ registry `written_premium` × 100
- **Today's value:** 96.15%
- **Status:** PROPOSED until an insurance owner approves it

The registry keeps refusing "loss ratio", because no earned premium exists. Approved or paid amounts are the closest available measure of losses; all claimed amounts include rejected and submitted claims.

Then, each step separately approved:

1. Point the dashboard ratio at this definition, or relabel it as "claimed".
2. Decide whether the dashboard's headline premium should exclude cancelled policies, as the registry does.
3. Retire the `metrics.py` copy.

## 4. Is each metric the same in Foresight, Dashboard and Ask AI?

- **Foresight and Ask AI.** Foresight reads the same metric objects Ask AI executes: the registry for the registry path, `metrics.py` for the metric route. Test `test_registry_metrics_and_formulas_are_exactly_the_registrys` checks this for all five domains.
- **Dashboard.** It uses its own SQL. Each headline figure was run and compared with its registry metric:

| Domain | Dashboard figure | Registry equivalent | Result |
|---|---|---|---|
| Banking | Total balance 1,988,344,989.56 | `total_account_balance` | Match |
| Banking | Accounts not closed 7,608 | `account_count` ACTIVE + DORMANT | Match |
| Finance | Cash balance; accounts; active-account cash | `total_cash_balance`; `account_count`; `total_cash_balance` ACTIVE | Match, all 3 |
| Insurance | Premium 120,624,750 | `written_premium` 116,622,300 | **Differs**: the dashboard includes CANCELLED policies |
| Insurance | Policies not cancelled 4,342 | `policy_count` ACTIVE, EXPIRED, LAPSED | Match |
| Retail | Net sales 855,388,282.83 | `net_sales` 855,745,065.02 | **Differs by 356,782.19**: the dashboard drops 30 order lines whose product is missing |
| Retail | Orders 18,995 | `order_count` COMPLETED + RETURNED: 18,997 | **Differs by 2**: orders whose only lines lack a product |
| Healthcare | Approved; encounters; billed | `approved_amount`; `encounter_count`; `billed_amount` | Match, all 3 |

**Dashboard figures with no registry metric**, so Foresight does not show them:

- **"Customers":**
  - Banking 3,970 counts account holders; the registry counts 5,000 customer records.
  - Retail 3,976 counts customers who ordered; the registry counts 4,000.
  - Insurance 2,338 counts policyholders with a policy; there is no registry metric.
  - Healthcare matches: 3,025.
- **Churn proxies:** "Closed account rate", "Cancelled policy rate", "Cancelled order rate".
- **Risk ratios:** Banking "Dormant account ratio", Finance "Cancelled trade ratio", Retail "Cancelled sales ratio", Healthcare "Approved claim amount rate", and the Insurance ratio in section 3.

## 5. Rule classification

| Kind (UI group) | What it means | Banking | Finance | Insurance | Retail | Healthcare |
|---|---|---|---|---|---|---|
| Enforced | Code blocks or changes behaviour; each names its code | 11 (4 platform, 7 registry refusals) | 6 | 6 | 5 | 6, including tenant isolation |
| Shadow | The model-written SQL allowlist: logs only, refuses nothing | 1 | 1 | 1 | 1 | 1 |
| Definition | How an approved metric is calculated (established by data and registry) | 10 | 0 | 0 | 0 | 0 |
| Pending approval | No approved definition; questions are refused or answered with a disclosure | 9 | 0 | 0 | 0 | 0 |
| Data check | Read-only violation count, measured 2026-11-12; not enforced; owner, severity and threshold pending | 0 | 2 | 4 | 2 | 4 |
| Data note | Known source limitation attached to answers | 11 | 2 | 3 | 2 | 1 |

Data checks are a **snapshot dated 2026-11-12**, not live. The page shows that date.

## 6. Illustrative, unreconciled and unavailable content

| Type | Content | Where |
|---|---|---|
| Illustrative | Banking capability cards: overview, objectives, responsibilities and activity wording | `frontend/lib/bankingCapabilities.ts`; each dialog carries an "Illustrative" notice |
| Proposed | Banking column meanings (133) | `banking_context.py` DICTIONARY. The wording is over-strong (F3) |
| Not owner-approved | All 46 registry metrics: "Owner sign-off not recorded"; owner "Not assigned" | registries |
| Unreconciled | 10 `metrics.py` definitions. Now shown equal in value except `claims_to_written_premium` | Metrics tab, "Secondary" |
| Unreconciled | Dashboard figures in section 4 | Dashboard |
| Snapshot | 12 data checks, 2026-11-12; relationship unmatched counts, 2026-11-11/12 | Rules and Relationships tabs |
| Pending | 9 Banking rules; 6 Banking glossary terms | Rules, Overview |
| Not in data | 6 Banking glossary terms; capability gaps (deposits, income, merchant banking, fraud, outstanding loans and others) | Overview, capability dialogs |
| Demo content | Insurance, Retail and Healthcare glossaries describe local demo tables (F1) | Overview glossary |
| Missing | Business definitions for every non-Banking entity and column | Entities tab |

## 7. Browser verification checklist

I could not run a browser. Please check each item for **each domain**, signed in as an administrator, and Healthcare as a tenant-assigned user.

**Page and navigation:**

1. Foresight opens from the sidebar. The header, sidebar and account menu look as before.
2. The source bar shows the user, the source and "loaded" with a time. The domain selector switches domains and reloads every tab.
3. A business user sees only their own domain, with no selector.
4. Signed in as Healthcare without a tenant: an error message, no data.

**Tabs:**

5. Five tabs, with counts on Entities, Relationships, Metrics and Rules. The active tab is indigo, and its count is readable on indigo.
6. **Overview:** two summary cards; the Banking capability cards; the glossary search filters.
7. **Banking capability dialog:**
   - Opens centred and closes with ×, Escape and an outside click.
   - All 5 inner tabs scroll inside the dialog.
   - The "Illustrative" notice is visible.
8. **Entities:**
   - The list and detail panel, and the aggregates toggle (Banking only).
   - The column table scrolls sideways on a phone.
   - The "technical tables" toggle works.
9. **Relationships:** cards show the join, cardinality, unmatched rows and verified date.
10. **Metrics:**
    - Search and the status filter (domains with secondary metrics).
    - The detail dialog shows formula, source, limitations and pending rules (Banking total balance).
11. **Rules:**
    - The groups appear in order: enforced, definitions, pending, checks, notes.
    - Check rules show "rows break this" with a date.

**Layout and other pages:**

12. At about 375 px wide: tabs wrap, cards stack, nothing scrolls sideways except the column table, and dialogs fit the screen.
13. Dashboard, Ask AI, Reports, Admin and the home page look unchanged. The home page still shows its architecture diagram.

**Changes from the previous Foresight page,** which you should confirm are acceptable:

- The six overview cards were removed.
- The "Context architecture" diagram was removed from Foresight. It is still on the home page.
- The "User & sources" tab was removed. Its user and source details remain in the source bar, but the "Permitted domains", "Organization" and "Configuration" lines are no longer shown.
- "Refresh metadata" was renamed "Reload", because it only re-fetches.
- Icons on Foresight now come from React Icons, while other pages use the existing lucide icons. Sizes match, but the drawing style differs slightly.

## 8. Tests

No fixes were made, so nothing new needed re-running. The last runs, on the unchanged tree:

- Backend: 615 passed, 0 failed, 11 skipped.
- TypeScript: clean.
- Evaluations: banking 63/63, registry 35/35, banking-context workflow all checks passed.

After any fix you approve, I will re-run:

- the full backend suite;
- `test_foresight.py` and `test_banking_context*.py`;
- TypeScript;
- the dashboard and registry reconciliation above.
