# Ryze Infinity: architecture assessment

Audited 2026-11-11 on branch `feature/agentic_ai_mode`, HEAD `136ec57`, with 79 uncommitted paths. Paths are relative to the repository root.

**Method.** Read only. No application source, dependency, schema, data or environment value was changed. PostgreSQL was queried in read-only transactions for names, counts and grants. Settings were read through the application without printing secrets.

**Not verified in this audit.**

- Databricks was not queried. Gold-layer figures come from the 2026-11-09 audit. The most recent live proof is the registry evaluation (35/35) and banking evaluation (62/62) run earlier on this branch.
- `pip-audit` and `npm audit` were not run. Docker is not installed on this machine, so nothing container-based could be tested.
- Dashboard and report numbers were not checked for correctness.
- `orchestrator.py`, `reports.py` and the frontend pages were read in the relevant parts, not line by line.

**Companion documents:** [CURRENT_ARCHITECTURE](CURRENT_ARCHITECTURE.md), [TARGET_ARCHITECTURE](TARGET_ARCHITECTURE.md), [IMPLEMENTATION_ROADMAP](IMPLEMENTATION_ROADMAP.md), [SECURITY_ASSESSMENT](SECURITY_ASSESSMENT.md), [DATA_FLOW](DATA_FLOW.md).

## 1. Where the brief and the code differ

| The brief says | What the code shows | Effect |
|---|---|---|
| Authentication uses Better Auth | Better Auth appears nowhere: not in `frontend/package.json`, the lockfile or any source. Login is custom: password or Google OIDC through Authlib on FastAPI, then an application JWT | Decision needed. Recommendation: keep the current flow (see the roadmap) |
| `banking` schema holds the designated banking e-commerce tables | It holds 7 bank tables (accounts, branches, cards, customers, loans, repayments; about 60,700 rows). No application code references them. The e-commerce-shaped tables (`customers`, `orders`, `order_items`, `products`) are in `public` | Decision needed on what these side schemas are for |
| PostgreSQL is authoritative for some dashboards | With `WAREHOUSE=databricks`, all analytics go to Databricks. PostgreSQL only serves application data | No metric currently has PostgreSQL as its source |
| Redis is planned for caching and rate limits | Already implemented against Valkey or Redis, with a local fallback. On this machine `CACHE_URL` is unset and nothing listens on 6379 | Code exists; the service is not in use |
| Workers and queues | None exist. One daemon thread warms up schema retrieval | Genuinely missing |
| CI/CD with GitHub Actions | `.github/workflows/ci.yml` exists but is untracked, so it has never run | Written, never exercised |
| Dockerfiles | None. `docker-compose.yml` starts only Valkey and PostgreSQL 16, while the real server is 18.6 | Missing |

An earlier request in this project asked for a different backend layout. This brief's layout replaces it, so that restructure was **not applied** to the real code. It remains a rehearsal in a scratch copy, where all 527 tests passed.

## 2. Existing versus missing components

**Working** = implemented and working. **Partial** = some of it exists. **Improve** = works, needs improvement. **Missing**. **Blocked** = waits on something external.

### Platform and identity

| Component | State | Evidence and gap |
|---|---|---|
| Next.js UI (Home, Dashboard, Ask AI, Foresight, Reports, Settings, Admin, Access, Monitor) | Working | 14 pages under `frontend/app`; TypeScript clean |
| FastAPI endpoints | Improve | 56 routes. 27 of them, and most route logic, sit in the 1,328-line `backend/app/main.py` |
| Password login, Google OIDC, onboarding | Working | `backend/app/auth.py`, `main.py:453-690` |
| Server-side domain authorization | Working | `auth.py:296-322` re-reads the account on every request; a forged claim is ignored |
| Domain access requests and admin approval | Working | `services/access.py`, `access_api.py`, `/admin` page |
| Roles | Partial | Two roles only, derived from `app_users.domain == "all"`. No role table |
| Tenant identity | Partial | Exists for Healthcare only (`user_domain_tenants`, 2 rows). Other domains have no tenant concept |
| Service, repository and API layers | Missing | About 96 ORM queries are spread across routes, services and auth |
| Database migrations | Partial | Alembic 0001-0003 exist, but the real database is unstamped and `ensure_schema` creates tables at startup |
| Centralized configuration | Working | `backend/app/config.py` |

### Data

| Component | State | Evidence and gap |
|---|---|---|
| PostgreSQL pooling and timing | Working | `db.py`: 5 + 10 connections, pre-ping, recycle, slow-query log |
| Databricks pooling, statement timeout, retry | Working | `warehouse.py:663-720` |
| Domain-to-catalog mapping | Working | All five domains map to their own catalog |
| Query router | Partial | One global `WAREHOUSE` setting chooses the engine. No per-metric routing |
| Metric definitions | Working | Banking registry and registries for the other four domains; compiled, parameterized SQL |
| Authoritative source per metric | Partial | Implicit: every registry metric is Databricks. Not a declared field |
| Metadata registry | Partial | Column names and glossary only. Unity Catalog had 0 descriptions and 0 keys on 2026-11-09 |
| Data freshness | Partial | Registry answers report date coverage; no freshness policy |
| Read-only analytical credentials | Blocked | PostgreSQL runs as superuser; Databricks needs a service principal (see security) |

### AI

| Component | State | Evidence and gap |
|---|---|---|
| LangGraph | Partial | Two small graphs: 2-node plan/retrieve and 4-node registry agent. Most of the flow is procedural in `Orchestrator._run` (about 350 lines) |
| Checkpoints and resumable workflows | Missing | HITL resumes by re-running the request |
| Deterministic metric answers | Working | Registry path for all five domains |
| Model-written SQL | Improve | Off for Banking; on as a fallback for the other four domains; weaker table validation (security finding S5) |
| SQL AST validation | Partial | `validate_sql` is parsed and applies to every execution. Table allowlisting by AST (`enforce_select_policy`) applies only on registry and banking paths |
| Approved joins | Partial | Inside registries only. The generic path has no join graph on Databricks |
| Knowledge RAG | Improve | Hybrid search with versioning, citations and domain filter, but only 4 documents, all Banking |
| Schema RAG | Improve | Chroma, domain filtered, rebuilt on each warm-up, retrievals serialized by one lock |
| Vector store abstraction | Missing | Chroma and in-table embeddings are used directly. pgvector is unavailable on this server |
| RAG tenant filter | Missing | Knowledge tables have no tenant column |
| LLM providers | Improve | Gemini, Ollama, OpenAI, Grok, Anthropic behind functions in `llm.py`, with retries, breaker, bulkhead and cooldown. No provider classes |
| Model capability registry, task-based selection | Missing | One model per provider from settings |
| Token tracking and cost | Partial | Wired to `llm_usage_events`, which has 0 rows. Not yet observed against the real database. Cost needs `LLM_COST_PER_MILLION_TOKENS` |
| MCP server | Improve | Stdio, 7 tools, token validated, input policy applied. One token per process; approved metrics are Banking only |
| MCP client abstraction | Missing | Server only |
| Input guardrails | Working | Pattern based: injection, domain scope, redaction |
| Output guardrails | Partial | Sensitive-field sanitizing exists. Unsupported-claim checks are limited to the synthesis validators |
| HITL | Partial | Works for forecasts; 12 reviews, 9 pending, no expiry. Lacks requested action and execution outcome |
| Forecasting | Improve | ETS and baselines work. TabPFN is off, with no weights and an undecided license |

### Operations

| Component | State | Evidence and gap |
|---|---|---|
| Cache and rate limit code | Working | `cache.py`; scoped keys; local fallback |
| Shared Redis in use | Blocked | No service configured here |
| Workers, queue, idempotent jobs | Partial | Idempotent responses exist for `/query`; no queue, worker or dead-letter handling |
| Request IDs and structured logs | Working | `main.py:199-250`, `services/observability.py` |
| Prometheus metrics | Partial | Hand-written text, per process, no histograms |
| OpenTelemetry, Grafana, error tracking | Missing | OTel packages are present only because `chromadb` needs them |
| Correlation from the frontend | Partial | Backend returns `X-Request-ID`; the frontend does not send one |
| Streaming Ask AI | Missing | No stream on the backend or frontend. `sse-starlette` is installed but unused |
| Ask AI transparency | Partial | Trace, evidence and SQL shown; freshness and intent not consistently shown |
| Frontend tests | Missing | No test runner |
| Backend tests | Working | 527 passed, 0 failed, 9 skipped; mostly fakes |
| Dockerfiles, staging and production configuration | Missing | |
| CI | Blocked | Written, untracked until you approve a commit |
| Backup and recovery | Partial | `backup_restore_check.py` and the runbook exist; not scheduled |

## 3. Main risks

1. **The administrator account uses a password published in this public repository,** and a database dump and `.bak` files are tracked there. Details in [SECURITY_ASSESSMENT](SECURITY_ASSESSMENT.md).
2. **79 uncommitted paths.** Banking, subscriptions, SQL security and registries are all unsaved in Git. Moving about 60 modules on top makes review and recovery much harder. Commit this work before Phase 2.
3. **Tests use fakes.** Passing tests say little about Databricks, Gemini, Valkey or real concurrency. Nine live tests are skipped by default.
4. **`DEMO_MODE=true` in the running configuration** turns off production startup checks and secure cookies.
5. **`orchestrator.py` is 2,451 lines** and holds routing, SQL validation, dashboards and persistence. It is the riskiest file to change and the one the brief changes most.
6. **Thin knowledge content.** Better retrieval cannot compensate for 4 Banking documents and no catalog descriptions.
7. **Scope.** The brief spans eight phases. Several items, such as Grafana, staging and a service principal, need infrastructure this project does not have yet.
