# Ryze Infinity: implementation roadmap

Proposed 2026-11-11, awaiting approval. Each phase ends with real test results, updated documentation and a list of blockers. Nothing is committed or pushed without your approval. Paths are under `backend/app/` unless stated.

## Decisions needed before Phase 2

| # | Decision | Recommendation |
|---|---|---|
| 1 | Save the 79 uncommitted paths first? | **Yes.** Otherwise the module move can't be reviewed or undone cleanly |
| 2 | Tracked dump and `.bak` files (S1) | Remove from tracking now; decide separately on rewriting history and rotation |
| 3 | Better Auth is named but absent. Adopt it? | **Keep the current Authlib and JWT flow.** Adopting it means a new auth system and session store and, on the frontend, migrating sessions, with no stated gain. Revisit if you need something it offers |
| 4 | What are the `banking`, `finance` and other side schemas for? | Leave untouched until you say which, if any, is authoritative |
| 5 | Keep `REGISTRY_GENERATED_SQL=true`? | Keep until the AST policy covers that path (Phase 4), or turn off now for stricter behaviour |
| 6 | Databricks service principal and PostgreSQL roles | Needed for production. You or a Databricks administrator must create them |
| 7 | Healthcare data policy and compliance owner | Keep Healthcare out of production until assigned |

## Phases

| Phase | Outcome | Depends on | Risk |
|---|---|---|---|
| Pre-work | Work saved; dump untracked; `google-genai` declared | Decisions 1, 2 | Low |
| 2 Modularization and identity | Target layout; thin routes; repositories begin; startup refuses unsafe production settings | Pre-work | High: about 60 modules, about 250 test patch paths |
| 3 Data and semantic layer | Repositories, query router, declared sources and freshness, restricted roles | Decisions 4, 6 | High |
| 4 LangGraph and routing | Typed workflow with one AST policy, bounded retries, checkpoints; provider registry | Phase 3 | High |
| 5 RAG, MCP, guardrails, HITL | Vector interface, tenant filters, knowledge content for all domains, per-tool permissions, full approval record | Phases 3, 4; content owners | Medium |
| 6 Redis and workers | Shared cache and limits; jobs for ingestion and reports | Queue decision | Medium |
| 7 Observability, testing, governance | Traces, metrics, frontend tests, load and recovery tests | Phases 4-6 | Medium |
| 8 Docker and CI/CD | Images, environments, working pipeline | Docker available; CI committed | Medium |

**Suggested priority inside these phases.** S1, then S4 and S2, then the AST policy (S5), then the data layer. Streaming, Grafana and worker scale-out follow once the core is safe.

## Exact files

**Pre-work:** `backend/requirements.txt`; removal from tracking of `backend/ai_analytics_before_healthcare_fix.backup`, `backend/app/governance.py.finance_20261013_161026.bak`, `backend/app/services/orchestrator.py.bak`, `.bak2`, `.finance_20261013_161026.bak`, `backend/app/services/reports.py.bak`, `backend/backup_finance_dashboard_*`. Files stay on disk.

| Phase | Modify | Create |
|---|---|---|
| 2 | Every module in `backend/app` moves as mapped in [TARGET_ARCHITECTURE](TARGET_ARCHITECTURE.md). Imports in `backend/tests`, `evals`, `migrations`, `create_user.py`, `set_user_role.py`, `purge_history.py`, `backup_restore_check.py`, `README.md`, `docs/ARCHITECTURE.md`, `docs/RUNBOOK.md`, `docs/SUBSCRIPTIONS.md`, `.env.example` | `core/logging.py`, `core/exceptions.py`, `core/dependencies.py`, `identity/*`, `api/*`, first repositories in `data/postgres/` |
| 3 | `warehouse.py`, `db.py`, `banking_registry.py`, `domain_registries.py`, `metrics.py`, `context_graph.py`, callers in `orchestrator.py` | `data/query_router/`, `data/databricks/` repositories, `data/metadata/` registry, reversible migrations, `docs/sql` role scripts |
| 4 | `orchestrator.py`, `assistant_graph.py`, `banking_agent.py`, `planner.py`, `sql_generator.py`, `guardrail.py`, `sql_policy.py`, `llm.py` | `ai/orchestration/state.py` and nodes, `ai/llm/registry.py`, adapters |
| 5 | `knowledge.py`, `schema_rag.py`, `mcp_server.py`, `tools.py`, `policy.py`, HITL code, `models.py`, `frontend/components/ResultWorkspace.tsx`, `frontend/app/chat/page.tsx` | `ai/rag/vector_store.py`, tool audit, migrations, `backend/knowledge/{finance,insurance,retail,healthcare}/` (content from your teams) |
| 6 | `cache.py`, `main.py`, `reports`, `purge_history.py` | `backend/workers/*`, job definitions |
| 7 | `observability.py`, `frontend/lib/api.ts`, pages calling `fetch` directly | OTel setup, Grafana dashboards, frontend tests |
| 8 | `docker-compose.yml`, `.github/workflows/ci.yml`, `docs/RUNBOOK.md` | `backend/Dockerfile`, `frontend/Dockerfile`, worker image, `.dockerignore`, environment examples |

The Phase 2 move has already been rehearsed for an earlier target layout: a scripted move in a scratch copy reproduced 527 passed, 0 failed, 9 skipped. The method carries over. The module map must be redone for this layout and re-verified before the real tree changes.

## Dependencies

| Phase | Package | Status |
|---|---|---|
| 1 | None | |
| Pre-work | `google-genai` | Already installed, undeclared |
| 2-5 | None expected | `langchain-google-genai`, `langchain-postgres` and `ragas` conflict with current pins (dry-run 2026-11-09). Avoid |
| 4 | `langgraph-checkpoint-postgres`, only if checkpoints use PostgreSQL | Upgrades `langgraph-checkpoint`; adds `psycopg-pool` (LGPL) |
| 6 | One queue library | Not chosen. Must be tested on Windows, or run only in containers |
| 7 | `opentelemetry-instrumentation-fastapi`, `-sqlalchemy`; optionally an error tracker; a frontend test runner | OTel API and SDK already present through `chromadb` |
| Only if decision 3 changes | `better-auth` | Not recommended |

Infrastructure: Docker, Valkey, Grafana and Prometheus, and a staging environment do not exist here yet.

## Testing and acceptance

**Rules.** Existing tests are not removed or weakened. Mocked tests are never reported as live integration. Each phase reproduces at least the current baseline: 527 passed, 0 failed, with the 9 skips explained.

| Test type | Today | Added in phase |
|---|---|---|
| Unit | Strong | Each |
| API integration | Fakes | 2 |
| PostgreSQL integration | Temporary databases | 3, on a disposable PostgreSQL 18 |
| Databricks integration | Live evaluations, opt-in | 3, using the service principal |
| Domain authorization | `test_access_control` and others | 2, across all routes |
| Tenant isolation | Healthcare | 3, 5 |
| SQL security | 29 tests | 4, for every path |
| RAG evaluation | `run_rag_eval.py` | 5, with labelled sets per domain |
| LangGraph workflow | Partial | 4, including retry limits and resume |
| LLM provider | 5 tests | 4 |
| MCP authorization | 10 tests, opt-in live | 5 |
| HITL | Partial | 5 |
| Frontend | None | 7 |
| Load | Script exists, not run | 7 |
| Failure recovery | Partial | 6, 7 |

**A phase is accepted when:** its tests pass and results are recorded, the relevant live evaluation has run, nothing in the existing baseline regressed, documentation is updated, and blockers are listed. "Production-ready" will be claimed only against the brief's section 20, one criterion at a time, with evidence.

**Known blockers to full acceptance.** Service principal and PostgreSQL roles; Healthcare compliance; knowledge content for four domains; catalog descriptions; Docker and staging infrastructure; a decision on TabPFN licensing.
