# Ask AI runbook

For the people deploying and operating the backend. Settings are described in `backend/.env.example`.

## Deploy

1. Back up PostgreSQL (below).
2. Install: `pip install -r requirements.txt` in `backend/`.
3. Add any new settings to the environment. Existing `.env` files keep working, because all new settings have defaults.
4. Migrate. On a database that predates Alembic, once only: `alembic stamp 0001`. Then `alembic upgrade head`. Both were tested on a restored copy only; the real database has not been stamped.
5. Load banking documents, once they are approved: `python -m app.services.knowledge ingest-dir --dir knowledge/banking --domain banking --approved-by "<approver>"`.
6. Start: `uvicorn app.main:app --port 8013`. Start-up creates missing tables. It does not change existing columns.
7. Check `GET /ready` returns 200, then run `PYTHONPATH=. python -m evals.run_eval --base-url <url>` on a non-production environment.

## Rollback

| Problem | Action |
|---|---|
| Routing gives wrong answers | Set `ASK_AI_AGENTIC_ROUTING=false` and restart; every question goes to the SQL workflow |
| Rate limits block real users | Raise `RATE_LIMIT_QUERY_PER_MINUTE`, or 0 to disable |
| Timeout stops valid long queries | Raise `DATABRICKS_STATEMENT_TIMEOUT_SECONDS`, or 0 for the warehouse default |
| Baseline forecasts unwanted | `PREDICTION_ENGINE=tabpfn`; forecasts report an error when TabPFN cannot run |
| Banking workflow gives wrong answers | `BANKING_AGENT_ENABLED=false` and restart; banking returns to the general path, which is not validated to the same standard |
| Model plans are wrong or slow | `BANKING_LLM_PLANNING=false`; only rule-read questions are answered, the rest get a clarification |
| A registry answer is wrong for finance, insurance, retail or healthcare | Remove the domain from `METRIC_REGISTRY_DOMAINS` and restart; that domain returns to the previous path |
| Plan limits block real users | `SUBSCRIPTION_ENFORCEMENT=false` keeps recording but limits nobody; `SUBSCRIPTIONS_ENABLED=false` removes plans altogether |
| Whole release | Deploy the previous commit. New tables and columns are ignored by old code and can stay |

## Backup and recovery

Databricks gold data is managed by its own pipelines. This application owns PostgreSQL: users, grants, conversations, reviews, audit and knowledge.

```bash
pg_dump -Fc "$DATABASE_URL" -f ryze_$(date +%Y%m%d).backup
pg_restore --clean --if-exists -d "$DATABASE_URL" ryze_YYYYMMDD.backup   # destroys current contents
```

Schedule the dump outside this application. Store it outside the repository, encrypted: it holds password hashes and tokens. `backend/.chroma` can be rebuilt from Unity Catalog metadata and needs no backup.

To prove a backup restores, without touching the real database:

```bash
python backup_restore_check.py --bin "<PostgreSQL bin>" --workdir <temp dir> --test-migrations
```

This dumps, restores into a new temporary database, compares row counts in every table, runs the migrations on the copy, then drops the copy and deletes the dump. A run during this work found no differences across 31 tables and 5,870 rows: dump 6.69 s, restore 4.61 s. No backup schedule has been set up.

## Retention

```bash
python purge_history.py --days 90          # dry run: prints what would go
python purge_history.py --days 90 --apply
```

Removes conversations, messages and query history older than the limit. Audit events and human reviews are kept. Nothing is purged until this is scheduled, and the retention period needs a compliance decision.

## Knowledge documents

```bash
python -m app.services.knowledge ingest --help
python -m app.services.knowledge delete --help
```

Ingest only approved text. Documents containing instruction-like text are refused, and changed text creates a new version.

## Monitoring

- `/health`: liveness. `/ready`: database, warehouse configuration, schema metadata.
- `/metrics`: Prometheus text. Send `METRICS_TOKEN` as a bearer token, or use an administrator session.
- Logs: set `LOG_FORMAT=json`. Each question emits one `ask_ai` event with audit id, route, status, duration, providers and tokens, but not the question text.

## Proposed service levels

These are starting proposals. The only load test so far was 30 requests per level from one laptop, with no model involved.

| Objective | Proposal |
|---|---|
| Availability of `/query` (non-5xx) | 99.5% monthly |
| Metric, knowledge and application answers | p95 under 10 s |
| Generated SQL answers | p95 under 120 s, depends on the LLM provider |
| Sensitive data found in answers or logs | 0 |

## Common failures

| Symptom | Likely cause | What to do |
|---|---|---|
| "Databricks … failing repeatedly" | Warehouse circuit open after 5 failures | Check warehouse state and network; it probes again after 30 s |
| `SSL` or `CERTIFICATE_VERIFY_FAILED` on corporate network | TLS inspection proxy | Ask IT to allow the host or supply the CA; do not disable verification |
| Answers name Ollama instead of Gemini | Gemini quota or key failure; 10 minute cooldown | Check quota. SQL quality is lower on small local models |
| Forecast names "statistical baseline" | TabPFN weights unavailable; skipped for 10 minutes | Expected until weights are installed and licensed |
| `/ready` stays 503 with `schema_metadata_loaded: false` | Warehouse was unreachable at start-up | Restore the warehouse. A background retry recovers without a restart |
| `BAD_REQUEST: Cannot create the resource, please try again later` | Databricks could not create a session (seen 2026-11-03) | Reported as 503. Refusals and clarifications keep working. Retry later; do not count evaluation results from this period as product results |
| Banking question gets `needs_clarification` for a wording you consider clear | Rules could not place a word and the model did not resolve it | Add the wording to `banking_query.py` with a test and an evaluation case |
| 402 on `/query` | The user's plan stopped a model call or feature | Expected. Check their plan and credits in the admin console; see `docs/SUBSCRIPTIONS.md` |
| `Subscription check failed` in logs | Accounting tables unavailable; questions run without limits | Check PostgreSQL and that migration `0003` has run |
| 504 on `/query` | `ASK_DEADLINE_SECONDS` reached | Check the trace for the slow stage, usually a local LLM |
| Metric figures look wrong | Compare the `sql` in the response with the warehouse | Fix the definition in `services/banking_registry.py` (banking) or `services/metrics.py` (other domains), bump its version, and update the evaluation cases |

## Tests

```bash
cd backend
PYTHONPATH=. python -m pytest tests -q
PYTHONPATH=. python -m evals.run_eval --offline
cd ../frontend && npx tsc --noEmit
```

Nothing is deselected. Tests run from an empty directory, so they ignore the local `.env`.

Against real services, read-only:

```bash
PYTHONPATH=. python -m evals.run_banking_eval                    # real Databricks, rules only
PYTHONPATH=. python -m evals.run_banking_eval --only-llm --provider ollama --model <model>
PYTHONPATH=. python -m evals.run_rag_eval
PYTHONPATH=. python -m evals.run_sql_eval --provider ollama --model <model>
PYTHONPATH=. python -m evals.run_demo --base-url <url> --provider ollama --report ../docs/BANKING_DEMO_TRANSCRIPT.md
PYTHONPATH=. python -m evals.load_test --base-url <url> --levels 1,5,10 --requests 30
RYZE_LIVE_MCP=1 PYTHONPATH=".:tests" python -m unittest tests.test_mcp_integration.McpLiveTests
TEST_CACHE_URL=redis://localhost:6379/15 PYTHONPATH=. python -m pytest tests/test_cache_live.py
```

`evals.run_demo` and `evals.run_eval` add history to the demo accounts.
