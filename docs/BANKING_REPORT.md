# Banking domain: implementation and evaluation report

Branch `feature/agentic_ai_mode`, 2026-11-03. Nothing is committed or pushed. Companion documents: [audit and data inventory](BANKING_AUDIT.md), [metric registry](BANKING_METRICS.md), [architecture diagram](ARCHITECTURE.md), [deployment and recovery](RUNBOOK.md), [demo transcript](BANKING_DEMO_TRANSCRIPT.md).

## Verdict

**Ready for a client demonstration, with the conditions below. Not ready for production.** Questions the rules can read are answered correctly against real data, with evidence, and everything else is refused or clarified. No local model is good enough to widen that coverage. Open security items block production.

## Results on the final code

| Check | Result | What it does not show |
|---|---|---|
| Unit and integration tests | 374 passed, 0 failed, 9 skipped | Skips need live services or a real Valkey (3 analytics, 2 workflow, 3 cache, 1 MCP) |
| Banking evaluation, real Databricks, rules only | 62/62 | The dataset informed the fixes, so this is not an unseen score |
| Demonstration through the real API | 30/30 expected | API only; no browser walkthrough was done |
| General API evaluation (all domains) | 28/29; security 8/8 | Fails `sql-retail`, below |
| Offline evaluation | 20/20 | Uses fakes |
| Live MCP, real client over stdio | Passed | stdio only |
| Secrets in API log | 0 card-pattern values; no "cvv" or "aadhaar" text | One run's log, 670 events; a pattern search |
| TypeScript | Clean | `next build` last ran before the final changes |
| CI | **Never run** | No push approved; no `act` or `gh` here |

### Failure not fixed

`sql-retail`, "Show net sales by order status for 2025", as the retail user: HTTP 422. Ollama aborted with `prediction aborted, token repeat limit reached`, twice in a row when reproduced, after about 2 minutes per attempt. No SQL ran and nothing false was shown, but **do not use this question in a retail demo with llama3.2**. The other four domains were not validated like banking.

## Model comparison

Same dataset for every model, on a CPU-only machine.

| Model | Plan cases (8) | Generated SQL (10) | Security failures | Latency |
|---|---|---|---|---|
| Rules only | 62/62 full set; model cases not applicable | n/a | 0 | p50 0.76 s |
| llama3.2 | 4/8; all four misses are refusals, no wrong figure | **0/10 correct**, 3 ran and returned wrong answers, 7 refused | 0 | Plans about 8 s, SQL median 40.6 s |
| qwen3:4b | **No result**: no case finished in 45 min at a 600 s call limit | First 2 correct (146 s, 78 s), then no result in 50 min | None seen | Unusable here |
| deepseek-r1:8b | **No result**: no case finished in 45 min | No result in an earlier run | None seen | Unusable here |
| Gemini gemini-3.5-flash | **Not evaluated**: real 429 quota errors | Not evaluated | n/a | n/a |

Conclusions:

- `BANKING_GENERATED_SQL` stays off. Validation stops bad SQL, but it cannot detect valid SQL that answers the wrong question.
- llama3.2 adds no paraphrase coverage: "How much money moved through UPI?" gets a clarification. It is safe, not useful.
- A stronger model cannot be judged on this hardware. Gemini or a GPU is needed before choosing one.
- The plan prompt was revised (`banking-plan-2`) after seeing llama3.2's replies on these cases, so 4/8 is slightly flattering.

## SQL generation design

1. Rules read every word of the question. A word they cannot place is never dropped.
2. If needed, a model proposes a JSON plan and never SQL. A plan that fails validation becomes a clarification.
3. Only a filter value the model added can account for an unplaced word. A breakdown cannot: that would answer a different question and lose the restriction.
4. `validate_plan` checks the metric, dimensions, values, role, period and intent against the registry.
5. `compile_plan` builds parameterized SQL from registry text. No user or model text enters the statement.
6. `sql_policy.py` parses the statement: one SELECT, approved tables, a row limit, no denied functions, no wildcard, explicit joins. Identifiers are checked again.
7. After execution: shape check, breakdown reconciled to total, rows sanitized for the role. Code writes the answer.

Failures are classified as invalid query, missing object, permission denied, timeout, infrastructure or policy. Only the first two allow one bounded regeneration, and only on the optional generated-SQL path. Infrastructure failures return 503.

**Known risk:** a model can map an unknown word to a real value, for example "rural" to a real segment. The answer then states "Read your question as: ..." and the evidence records it, but the figure is still shown. If that is unacceptable, set `BANKING_LLM_PLANNING=false`.

## Forecasting design

History comes from compiled registry SQL, never from a model. At least 24 complete months are required, and series more than 365 days stale are refused with `insufficient_history`. Naive, seasonal naive and ETS are compared in a 12-origin rolling backtest, reporting MAE and WAPE against the best simple baseline. The last incomplete month is left out, and the forecast continues from the end of the data.

| Metric | Outcome | Model | Backtest MAE vs best baseline | WAPE |
|---|---|---|---|---|
| New accounts opened | Forecast | ETS damped | 6 vs 8 (26.6% lower) | 14.7% |
| Repayments collected | Forecast | ETS damped | INR 2,079,126 vs 2,088,484 (0.4% lower) | 4.8% |
| Loans disbursed | Refused | n/a | 13 months, and ended | n/a |
| Cards issued | Refused | n/a | Stale, ended 2023 | n/a |
| Transactions | Refused | n/a | One day of data | n/a |

The 0.4% improvement on repayments is effectively no skill. The method is selected on the same folds it is scored on, so errors are somewhat optimistic; answers say so. Repayments also include a data shift from 2025-07 that needs a business explanation.

## TabPFN licensing and evaluation

**Not evaluated, disabled, no weights downloaded and no license accepted.**

- The package (installed: `tabpfn` 6.4.1) is under the Prior Labs License, Apache 2.0 plus an attribution requirement.
- TabPFN-2.5 default weights are non-commercial by default. TabPFN-2 weights are Apache 2.0 with "Built with PriorLabs-TabPFN" attribution. Please have legal confirm this, as it reflects my reading of the license terms and not legal advice.
- The baseline to beat, on target "instalment recorded with amount_paid = 0", with a time split at 2025-10-01 (train 28,520 rows, 1.42% positive; test 11,050 rows, 19.19% positive):

| Model | ROC AUC | Average precision | Brier |
|---|---|---|---|
| Base rate | 0.500 | 0.192 | 0.187 |
| Logistic regression | 0.933 | 0.919 | 0.025 |
| Gradient boosting | 0.933 | 0.919 | 0.027 |

The positive rate jumps from 1.42% to 19.19% across the split, so these scores need caution. Features include prior missed instalments, which may carry most of the signal.

## MCP and vector store decisions

- **MCP: kept.** `banking_schema`, `banking_metric` and `banking_history` run through the same workflow. Tested with a real client and server over stdio, including token and other-domain refusal. `ask_business` is off by default.
- **Vector store: neither pgvector nor Qdrant adopted.** Four short documents do not justify either. Embeddings (`nomic-embed-text`) live in PostgreSQL rows. pgvector is the choice if the corpus grows, but it is not installed and was not evaluated live.
- **RAG results**, 18 answerable and 5 unanswerable questions:

| Mode | Top 1 | Top 3 | False hits on unanswerable |
|---|---|---|---|
| Keyword | 9/18 | 14/18 | 0/5 |
| Hybrid (threshold 0.62) | 12/18 | 17/18 | 1/5 ("What is the RBI repo rate today?") |

The threshold was tuned on this same small set. Knowledge documents are marked `draft-pending-client-review`.

- **Valkey: optional, off, not verified.** No real instance was available. The live tests exist and CI is configured with a service, but neither has run.
- **OpenTelemetry: not implemented.** Request ids, metrics and JSON logs are in place.

## Security and privacy results

**Passed on the real API:** injection refused (400), other-domain questions refused (422), CVV and card or account number requests refused (403), a card number in a question redacted, identifier lists refused, approval does not bypass a refusal. Refusals hold during a warehouse outage.

**Open, and blocking production:**

| Severity | Finding | Owner action |
|---|---|---|
| High | The repository is public. Commit `136ec57`, which is also the base of this branch, tracks a database dump with 4 users' password hashes and 163 refresh-token rows, plus `.bak` files | Rewrite history, rotate affected passwords and tokens |
| High | A Databricks-format token appears in `.env.example` history (`1b11a2e`, `4a79d4d`, not on remotes) | Revoke it if it was real |
| High | Databricks runs as a personal owner account, with no column masks or row filters. Only application code protects sensitive columns | Apply `docs/sql/banking_least_privilege.sql` (written, not applied) with a service principal |
| Medium | The local PostgreSQL password is short and in tracked documentation | Rotate if used beyond local development |
| Medium | Injection detection is pattern based | Least privilege is the real control |

**Dependencies.** 39 Python advisories at the last audit, one fixed since (`joserfc`):

| Package | Why it remains |
|---|---|
| `pyasn1` 0.4.8 | `python-jose` requires `<0.5`, while `pyasn1-modules` requires `>=0.6.1`, so `pip check` reports a conflict. Fix: replace `python-jose` |
| `python-jose`, `chromadb`, `ecdsa` | No fixed version listed |
| `filelock` | Fix needs 4.x, which conflicts with the Snowflake connector |
| `oauthlib` | Fix needs 4.0.0, not attempted |
| `pytest` | Fix needs 9, development only |

npm: the critical Next.js issue is fixed (15.5.28). One high (`postcss`) and one moderate (`next`) remain, and fixing them requires the Next 16 major upgrade.

**Licenses:** 190 packages scanned from package metadata. `psycopg` and `psycopg2-binary` are LGPL, and `tabpfn` has its own license. The rest declare permissive or MPL terms; one declares "Dual License" and was not read further.

**Not done:** penetration testing, or Unity Catalog grants on the real workspace.

## Other evaluation results

**Load**, real API and warehouse, one laptop, 30 requests per level, no model involved:

| Concurrency | Errors | Throughput per second | p50 | p95 | Max |
|---|---|---|---|---|---|
| 1 | 0 | 0.86 | 1.26 s | 1.57 s | 2.01 s |
| 5 | 0 | 3.35 | 1.34 s | 2.42 s | 3.21 s |
| 10 | 0 | 2.42 | 1.66 s | 4.33 s | 12.4 s |

Too small to size production. An earlier run of the same test hit a Databricks session outage (`Cannot create the resource`), and those results were discarded.

**Backup and restore,** on a temporary copy: 31 tables and 5,870 rows with no differences, and migrations leave rows unchanged. The real database has not been stamped and no schedule exists.

## CI and regression report

- The original 6 failures were reviewed individually: 5 came from tests reading the local `.env` and pass once isolated, and 1 test expected an outdated recovery sequence and was corrected. See the audit.
- No test is deselected or skipped to pass. CI's previous deselects were removed.
- **CI is expected to fail on its first run** at `pip-audit` and `npm audit --audit-level=high`, because of the open advisories listed above. These steps were left strict rather than silenced.
- Live-run failures found and fixed: dropped filters, a false cross-domain answer, a wrong ratio, audit id collisions, refusals that needed the warehouse, a malformed model plan shown to users, and a breakdown that could silently drop a word.
- Test defect fixed: the live MCP test supplied no token.

## File-by-file implementation

| File | Change |
|---|---|
| `app/services/banking_registry.py` | New: 19 versioned metrics |
| `app/services/banking_query.py` | New: rule parser, plan, validation, compiler, model planning |
| `app/services/banking_agent.py` | New: LangGraph workflow, verification, evidence |
| `app/services/sql_policy.py` | New: parsed SQL policy, failure classification |
| `app/services/forecasting.py` | New: history checks, backtest, baselines, ETS |
| `app/services/orchestrator.py` | Banking hook; refusals before warmup; bounded regeneration; 503 on infrastructure failure |
| `app/services/knowledge.py`, `planner.py` | Hybrid search, registry entries, directory ingest; reference-question routing |
| `app/sensitive_data.py` | Compiled-SQL guard; "salary account" no longer refused |
| `app/mcp_server.py` | Rewritten over the banking workflow |
| `app/models.py`, `config.py`, `seed.py` | Plan state and embedding columns; feature flags |
| `app/main.py`, `services/observability.py` | Readiness recovery; request ids in logs |
| `migrations/`, `alembic.ini`, `backup_restore_check.py` | New: Alembic and restore proof |
| `knowledge/banking/*.md` | New: four draft documents |
| `evals/*` | New: banking, SQL, RAG, tabular, load and demo runners and datasets |
| `tests/*` | New: banking, MCP, migrations, cache; `conftest.py` isolates tests from `.env` |
| `frontend/app/chat/page.tsx`, `components/ResultWorkspace.tsx` | `insufficient_history` status |
| `frontend/package-lock.json`, `requirements.txt` | Security upgrades; statsmodels, alembic |
| `.github/workflows/ci.yml` | Deselects removed; Valkey service; `pip-audit` |
| `.gitignore`, `README.md`, `docs/*` | Dumps and `.bak` files ignored; documentation |

Before committing, leave out `frontend/tsconfig.tsbuildinfo`, which is tracked and modified.

## Known limitations

- **Transactions cover one day** (2026-03-15), so transaction trends, comparisons and forecasts are refused. Trend demos use repayments and account openings.
- **"Transaction volume" counts every status.** INR 251,829,230.04 includes 12,759,030.40 failed and 5,009,478.97 pending; success alone is 234,060,720.67. The answer says so, but the default is a business decision.
- All loans are ACTIVE, so NPA and default rates cannot be derived. Balance history does not exist.
- Coverage is limited to worded patterns the rules know, plus clarification. Expect clarifications on unfamiliar phrasing during a live demo.
- Generated SQL, stronger models and Gemini are unproven here.
- Metric definitions and knowledge documents have not been reviewed by the client.
- Rate limits and idempotency are per process without Valkey.
- Not tested: browser walkthrough, CI, real Valkey, pgvector, TabPFN, multi-instance deployment, restore of the real database in place.

## Ready for a demo

- Volume, amount, averages, type and merchant breakdowns, regional and status views
- Repayment and account-opening trends and year-over-year comparisons
- Follow-ups that keep context
- Forecasts for new accounts and repayments, with backtests, plus administrator review
- Honest refusals: NPA, cross-domain, CVV, insufficient history
- Definition questions with sources

**Conditions:** run it on the validated accounts with Databricks up (a transient outage returns 503), rehearse in the browser first, and use the transcript's questions. Do not present generated SQL, and do not claim the other four domains have this level of validation. Describe transactions as a one-day extract.

## Needed before production

1. Remove the dump from Git history; rotate credentials.
2. Service principal with least-privilege grants and masks.
3. Run CI once; resolve its results.
4. Replace `python-jose`; decide on the Next 16 upgrade.
5. Client sign-off on metrics, the status default and knowledge documents.
6. A multi-month transaction extract.
7. Evaluate a stronger model on Gemini or a GPU.
8. Backup schedule, retention period, stamped database, real Valkey test, larger load test.
