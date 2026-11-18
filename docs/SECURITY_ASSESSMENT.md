# Ryze Infinity: security assessment

Audited 2026-11-11. Read only. This is a code and configuration review, not a penetration test. Findings marked *2026-11-09* come from the earlier audit and were not re-tested.

**Remediation status, 2026-11-11.**

- S0 and S1 still need your action. The files are untracked in commit `a8e87dd`, but the administrator password and the repository's visibility are unchanged.
- S4 is fixed in `a7c2301`. Staging and production refuse to start with demo mode on, and cookie security now follows `ENVIRONMENT`.
- S5 is in shadow mode in `957c70b`. One parsed allowlist policy now covers all five domains, and it waits for your review before enforcing.
- S20 is unchanged.

## What is already sound

| Control | Evidence |
|---|---|
| Role, domain and tenant re-read from PostgreSQL per request, so a forged claim or revoked grant takes effect at once | `backend/app/auth.py:296-322` |
| Every data route requires a signed-in user; `/admin/*` requires an administrator | Route enumeration in [CURRENT_ARCHITECTURE](CURRENT_ARCHITECTURE.md) |
| Passwords use PBKDF2-SHA256 at 310,000 iterations | `backend/app/security.py` |
| Every warehouse statement passes a parsed check: one statement, read-only, denied functions, row limit | `services/guardrail.py`, `warehouse.py` |
| Registry queries use bound parameters, an AST table allowlist, and identifier protection | `services/sql_policy.py`, `banking_agent.py:231-235` |
| Cache keys scoped by user, domain and tenant | `backend/app/cache.py:102` |
| Healthcare runs only in the tenant's mapped catalog and refuses an unmapped tenant | `warehouse.py:255-280`, `tests/test_domain_registries.py` |
| `.env` and common dump patterns are ignored; compose holds only local development passwords | `.gitignore` |

## Findings

| # | Severity | Finding | Evidence | Recommendation | Needs |
|---|---|---|---|---|---|
| S0 | **Critical** | The administrator account in the real database still uses a password that is published in this public repository (`DEMO_PASSWORD` in `.env.example`, 14 commits) | Hash comparison, 2026-11-11: 1 of 9 accounts matches, and it is the administrator | Change that password now. Replace the value in `.env.example` with a placeholder | You change the password |
| S1 | **High** | A database dump, 5 `.bak` source copies and 3 backup source files are tracked in a public repository and pushed. The dump is from an older schema: all business tables are empty. It holds 4 user records on test email domains with argon2 hashes, and 163 hashed refresh tokens | GitHub API: public, 0 forks. `pg_restore` inspection. No live secret from `.env` appears in any Git object | Untrack the files. Make the repository private if it need not be public. History rewrite is optional, see [PHASE_2_PLAN](PHASE_2_PLAN.md) | Your decision on visibility and history |
| S2 | High | The application connects to PostgreSQL as the `postgres` superuser. `analytics_user` exists with no grants | `pg_roles`, `role_table_grants` | Separate application and read-only roles | Your approval, then `DATABASE_URL` changed by you |
| S3 | High | Databricks uses a personal token with no masks or row filters. There are no service-principal settings | *2026-11-09*; `config.py` | Service principal with `SELECT` on approved objects. `docs/sql/banking_least_privilege.sql` is a starting point | Databricks administrator |
| S4 | High | `DEMO_MODE=true` skips the JWT-strength check, makes cookies non-secure and allows HTTP redirects | `main.py:169`, `:283`, `:547`, `:1327` | Refuse startup in non-development environments with demo mode on | Phase 2 |
| S5 | High | Model-written SQL for Finance, Insurance, Retail and Healthcare has no AST table allowlist. It relies on `validate_sql` plus text-based source and column checks | `orchestrator.py:1199-1207` versus `:1508` | Use one AST policy for all paths; decide whether this fallback stays on | `REGISTRY_GENERATED_SQL` decision; Phase 4 |
| S6 | Medium | The PostgreSQL execution path does not open a read-only transaction. It depends on the parsed check and on the account's rights, which are superuser (S2) | `warehouse.py:102-108` | `SET TRANSACTION READ ONLY` and a restricted role. Only reachable with `WAREHOUSE=local` today | Phase 3 |
| S7 | Medium | The session JWT is kept in `localStorage`, readable by any injected script. No Content-Security-Policy or other security headers are set | `frontend/lib/api.ts:6,56`; `next.config.ts` | Move to the `httpOnly` cookie already used by OIDC; add headers | Phase 2, coordinated with the frontend |
| S8 | Medium | One secret signs JWTs and sessions, and is the pepper for legacy password hashes. Tokens cannot be revoked before expiry | `main.py:1324`, `security.py`, `auth.py:43` | Separate secrets; rotation plan. Role and domain are already re-checked, which limits the impact | Your action on `.env` |
| S9 | Medium | Tenant identity exists only for Healthcare and is absent from knowledge tables. Domain filtering alone is not tenant isolation | `models.py`; `services/knowledge.py` | Add `tenant_id` through a reversible migration | Phase 3 and 5; approval |
| S10 | Medium | Healthcare has no compliance sign-off. Diagnoses are excluded by policy, and no Healthcare question has reached `query_history` in the real database | 0 Healthcare rows | Keep it out of production until reviewed | Compliance owner |
| S11 | Medium | No declared policy decides which domains' questions and schema may be sent to Gemini | `llm.py`, `sql_generator.py` | Per-domain provider policy; Healthcare defaults to local or refusal | Your decision; Phase 4 |
| S12 | Medium | MCP uses one token per process, and tool calls are logged but not stored as an audit record. Approved metrics are Banking only | `mcp_server.py:30-45` | Per-call identity, persisted tool audit, all five domains | Phase 5 |
| S13 | Medium | With `CACHE_URL` unset, rate limits are per process, so several workers multiply the allowance | `cache.py:75-100` | Run Valkey wherever more than one process serves requests | Phase 6 |
| S14 | Medium | HITL has no requested-action or execution-outcome fields. Nine reviews are pending, the oldest from 2026-10-28, and reviews never expire | `HitlReview` model, `hitl_reviews` | Additive columns and expiry | Phase 5; approval |
| S15 | Medium | The real database is unstamped by Alembic, and tables are created at startup outside migrations | `alembic_version` absent; `seed.py:116-126` | Back up, stamp, then stop creating tables at startup | Your approval |
| S16 | Low | Audit events have no tenant column | `AccessAuditEvent` | Additive column | Phase 7 |
| S17 | Low | `services/warehouse.py` is unused, has undefined names and its own catalog map | Static check | Prove obsolete, then remove with approval | Phase 2 |
| S18 | Low | Dependency vulnerabilities unknown: audits were not run, and CI has never run. `python-jose` forces an old `pyasn1` | CI untracked; `pip check` | Run CI; consider moving to `joserfc`, already a dependency | Phase 8 |
| S19 | Low | A Databricks-format token appears in `.env.example` in one local snapshot, `1b11a2e`. It is on no remote branch and differs from the current token | Local `refs/agents/*` only | Confirm in Databricks that it is revoked | You |
| S20 | Low | The local PostgreSQL password is 4 characters. It is on no Git object and the server is on loopback | Length check only | Replace when the restricted roles are created | You |

## Text-to-SQL requirements

| Requirement | Registry path | Model-written path |
|---|---|---|
| Metadata grounding | Yes | Yes, names and glossary only |
| Domain table allowlist | AST | Text-based |
| Column allowlist | Declared in registry | Text-based |
| Approved joins | Declared in registry | None on Databricks |
| AST parsing, SELECT only, single statement | Yes | Yes |
| Dangerous functions blocked | Yes | Yes |
| Catalog and schema restriction | AST, plus session namespace | Session namespace |
| Timeout | Yes | Yes |
| Row limit | Yes | Yes |
| Cost or resource controls | Timeout and rows only | Timeout and rows only |
| Read-only credentials | **Not yet** (S2, S3) | **Not yet** |
| Query audit | `query_history` | `query_history` |
| Safe errors | Yes | Yes |

## Domain and tenant checks

| Boundary | State |
|---|---|
| API | Enforced by `get_current_user` |
| Service | Implicit, by passing `user.domain` |
| Repository | Missing: no repository layer. Enforced at warehouse by catalog resolution |
| Metadata retrieval | Filtered by domain |
| RAG retrieval | Filtered by domain before scoring; no tenant |
| MCP | Domain from the validated token; no per-call identity |
| Frontend selection | Cannot override the backend; switching domain re-authorizes |
