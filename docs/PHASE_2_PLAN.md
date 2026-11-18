# Ryze Infinity: Phase 2 plan and security remediation order

Proposed and approved 2026-11-11. Per-step local commits approved; nothing is pushed.

## Progress

| Step | State | Commit | Evidence |
|---|---|---|---|
| R0 | **Open, yours**: change the administrator password; make the repository private in GitHub settings (the GitHub CLI is not installed here); confirm the old token is revoked | | |
| R1 | Done | `a8e87dd` | 10 files untracked, checksums unchanged on disk; `.gitignore` covers `*.bak2`, `banking_import/`, `frontend/.vscode/`; `DEMO_PASSWORD` placeholder |
| R2 | Done | `3addf2e`, `80b466f`, `85a7163` | Diff of modified files read in full; new files pattern-scanned (no live secret, only test fixtures); 527/0/9 before commit; `tsc` clean |
| R3 | Done | `c798be0` | Clean virtual environment installs the manifest; `pip check` clean there (the existing venv has an unrelated `pyasn1` conflict) |
| R4 | Done | `a7c2301` | 20 new tests; full suite 547/0/9; dev server unchanged |
| R5 | **Shadow mode**, awaiting your allowlist review | `957c70b` | 15 new tests; full suite 562/0/9; replay below |
| R6-R9, M0-M10 | Not started | | |

**R5 shadow replay (298 stored statements, read-only):** 263 pass. The 35 refusals are all statements the current checks or the warehouse already refuse: 15 Finance statements on the renamed `total_balance` column, 11 Banking statements on tables outside the registry (already refused by the Banking policy), 7 that do not parse (already refused by the guardrail), 2 Retail statements on `first_name` (already refused for business users). Compiled dashboard SQL conforms. To enforce: review `backend/app/sql_allowlists/*.json`, then set `SQL_ALLOWLIST_MODE=enforce` and run the live registry and banking evaluations.

**Review decision in R5:** restricted fields are marked administrator-only rather than removed, because administrators can query them today and removing them would take that away. Remove them from the files if administrators should lose that access.

**Baseline on the real tree, 2026-11-11:** 527 passed, 0 failed, 9 skipped. Offline evaluation 20/20. TypeScript clean.

## 1. Exposure investigation

Method: every Git object on all local refs (39 commits, 284 blobs) was scanned for credential patterns. The real `.env` secret values were compared exactly against every object and the dump. Only names and counts were reported. GitHub was read through its public API.

| Item | Finding | Action |
|---|---|---|
| Repository | **Public**, 0 forks, last push 2026-11-02 | Make private if it need not be public (you) |
| Current `.env` secrets (`DATABRICKS_TOKEN`, `GOOGLE_API_KEY`, `JWT_SECRET`, `OIDC_CLIENT_SECRET`, `DATABASE_URL`) | **None** appears in any Git object or in the dump | No rotation needed on exposure grounds |
| Administrator password | **The real administrator account uses `DEMO_PASSWORD`, which is published in `.env.example`** | **Change it now** (you) |
| Other 8 accounts | Do not match any published password | None |
| Dump | Older schema, business tables all empty. 4 users on test email domains with argon2 hashes, 163 hashed refresh tokens, 4 tenants, no customer data, no connection strings | Untrack. Rotate those 4 accounts' passwords only if they were reused anywhere |
| 5 `.bak` and 3 backup source files | Old source code, no credentials found | Untrack |
| Databricks-format token, commit `1b11a2e` | Local snapshot refs only, on no remote branch, not the current token | Confirm revoked (you) |
| Card, Aadhaar and PAN patterns | Test fixtures and evaluation prompts | None |
| Database URLs in docs | Placeholders | None |

Limits: the scan covers refs known locally. It cannot show who downloaded the public files. Whether the token is still active was not tested, because using it would be improper.

**History rewrite recommendation: not required.** The dump holds no customer data and no live secret. The one harmful item, the published password, stays in history even after a rewrite unless the password changes, and once it changes it is harmless. A rewrite needs a force-push to both remote branches and is hard to undo. Choose it only if a policy requires removal. Local `refs/agents/*` snapshots are left alone.

## 2. Security remediation order

| Order | Item | Who | Touches code? |
|---|---|---|---|
| R0 | Change the administrator password; decide repository visibility; confirm the old token is revoked | You | No |
| R1 | Untrack dump, backups and generated file; `.gitignore`; placeholder in `.env.example` | Me | No |
| R2 | Baseline commits, section 3 | Me, after approval | No |
| R3 | Fix `requirements.txt` | Me | Manifest only |
| R4 | Production safeguards for `DEMO_MODE` | Me | Yes |
| R5 | Parsed table and column allowlists, all five domains | Me | Yes |
| R6 | Authentication hardening without schema change | Me | Yes |
| R7 | Sessions, refresh and revocation | Me, after schema approval | Yes, migration |
| R8 | PostgreSQL least-privilege roles | Plan by me, run by you | Startup change, scripts |
| R9 | Databricks service principal | Plan by me, created by your administrator | Small, flagged |

R3-R6 come before any file moves, so each security diff stays small and readable in the current layout.

## 3. Proposed commit contents (R1 and R2)

Nothing is staged. The 79 status entries expand to 113 files. A pattern scan of all uncommitted files found only placeholders and test fixtures. **A line-by-line read of the full diff is still to do, and will be finished before staging.** The changes to `orchestrator.py`, `main.py` and `models.py` interleave several features, so per-feature commits would not pass tests on their own.

| Commit | Contents |
|---|---|
| 1. Stop tracking dumps, backups and build state | Untrack the 9 backup files and `frontend/tsconfig.tsbuildinfo`, kept on disk. `.gitignore`, adding `*.bak[0-9]*` (`.bak2` escapes today), `banking_import/`, `frontend/.vscode/`. `.env.example` placeholder |
| 2. Backend and its tests | 20 modified and 16 new under `backend/app`; `requirements.txt`, `.env.example`, `alembic.ini`, `migrations/`, `knowledge/`, `backup_restore_check.py`, `purge_history.py`; 4 modified and 11 new test files; `evals/` (11) |
| 3. Frontend | 8 modified and 5 new under `frontend/` |
| 4. CI and public documentation | `.github/workflows/ci.yml`, `README.md`, `docs/ARCHITECTURE.md`, `RUNBOOK.md`, `SUBSCRIPTIONS.md`, `BANKING_METRICS.md`, `docs/sql/` |

**Excluded:**

| Files | Why |
|---|---|
| `banking_import/` (6 files, about 14 MB) | Data files |
| `frontend/.vscode/`, `.env`, build output | Local |
| `docs/SECURITY_ASSESSMENT.md`, `ARCHITECTURE_ASSESSMENT.md`, `CURRENT_ARCHITECTURE.md`, `DATA_FLOW.md`, `IMPLEMENTATION_ROADMAP.md`, `TARGET_ARCHITECTURE.md`, this plan, `BANKING_AUDIT.md`, `BANKING_REPORT.md`, `BANKING_DEMO_TRANSCRIPT.md` | They describe open weaknesses, or real figures, and the repository is public. Include once it is private or the items are fixed |

Pushing commit 4 also turns CI on for the pushed branch.

## 4. Security changes

### R3 Gemini dependency

- **Problem.** `google-genai` is used but undeclared. Version 2.32.0 needs `pydantic>=2.12.5`, while `requirements.txt` pins 2.11.3 and 2.13.5 is installed. A clean install from the manifest would downgrade pydantic and could not include Gemini.
- **Files.** `backend/requirements.txt`.
- **Solution.** Add `google-genai==2.32.0` and set `pydantic==2.13.5`. These record what all tests already run on, so nothing is upgraded.
- **Risks.** Low. `kubernetes`, needed through `chromadb`, also resolves differently on a clean install.
- **Tests.** `pip install --dry-run` must succeed; a clean virtual environment in the scratch folder runs the full suite.

### R4 Production safeguards

- **Problem.** `DEMO_MODE` defaults to true and controls security: JWT strength check, secure cookies at 6 sites, HTTP redirects, seeding known accounts, mock payments.
- **Files.** `config.py`, `main.py`, `access_api.py`, `seed.py`, `services/payments.py`, `.env.example`, `tests/conftest.py`, new test file.
- **Solution.**
  - Add `ENVIRONMENT` (`development`, `staging`, `production`), default `development`, so this machine behaves as today with no `.env` change.
  - `demo_mode` default becomes false; your `.env` already sets it explicitly.
  - One helper decides cookie security, from environment rather than demo mode.
  - In `staging` or `production`, startup lists every problem and refuses to run: demo mode on, weak or default secret, non-HTTPS URLs, wildcard origins, mock payments, SQLite, PostgreSQL superuser, default demo password.
  - **A forgotten setting is caught.** If `ENVIRONMENT` is `development` but the frontend, redirect or CORS addresses are not loopback, startup also refuses, unless `ALLOW_INSECURE_DEVELOPMENT=true` is set, which logs a warning on every start.
- **Risks.** A LAN demo needs that override. Until R8 is done, the superuser check blocks production by design.
- **Tests.** Each refusal, each cookie site, and development unchanged.

### R5 Parsed allowlists for five domains

- **Problem.** Only registry and Banking paths use a parsed table allowlist. `_validate_query_columns` **returns without checking** when SQL has a CTE or subquery, when parsing fails, or when a table is unknown.
- **Files.** `services/sql_policy.py`, `orchestrator.py`, `warehouse.py`, `domains.py`; new per-domain allowlist data; tests.
- **Solution.**
  - One `enforce_domain_policy` taking a domain's catalog, schema, tables and columns. It builds on `enforce_select_policy`.
  - sqlglot scope analysis resolves columns through CTEs and subqueries. Anything unresolved **fails closed**.
  - Model-written SQL reaches the warehouse only through one function that requires this policy, so no path can skip it. Healthcare's tenant scoping stays and runs first.
  - Allowlist source: a file per domain generated from Unity Catalog names, excluding metadata and profile tables and existing restricted columns, **reviewed by you**. At run time it is intersected with the live schema. No catalog mapping changes.
  - Compiled dashboard and report SQL stays code-owned; a test asserts it conforms.
- **Risks.** Valid questions could be refused. Before enforcing, I replay the 298 stored statements, both evaluation sets and all compiled SQL in shadow mode, and report every rejection. Coverage may drop for tables you exclude.
- **Tests.** CTE, subquery, alias, union, cross-domain, system catalog and denied-column cases per domain; live registry 35/35 and banking 62/62.

### R6 Authentication without schema change

- **Problem.** Tokens lack issuer, audience and ID. Password login keeps the JWT in `localStorage`. One secret has three jobs. No security headers. RBAC checks are scattered.
- **Files.** `auth.py`, `main.py`, `access_api.py`, `config.py`, `frontend/lib/api.ts`, pages reading the token, `next.config.ts`, tests.
- **Solution.**
  - Validate `iss`, `aud`, required `exp`, `iat`, `jti` and a fixed algorithm.
  - Password login sets the same `httpOnly` cookie OIDC uses, and the frontend stops storing the token. `access_token` stays in the response for API clients.
  - Add an Origin check for cookie-authenticated writes, and security headers.
  - Optional `SESSION_SECRET`, required in production.
  - One permission map in RBAC, keeping the current admin and analyst roles.
- **Risks.** Everyone signs in once more. `MCP_ACCESS_TOKEN` needs re-issuing. Production frontend and API must be same-site.
- **Tests.** Tampered, expired, wrong-audience and wrong-algorithm tokens; cookie flags; cross-origin refusal; browser login, domain switch and logout.

### R7 Refresh and session invalidation (schema change, separate approval)

- **Problem.** Tokens last 8 hours, have no refresh and logout only clears the browser.
- **Solution.** Alembic `0004` adds one table, `auth_sessions`. Short access tokens carry a session ID, already checked within the per-request account lookup. Rotating refresh token in an `httpOnly` cookie, with reuse detection. Logout and administrators can revoke.
- **Precondition.** Backup, then stamp the database at `0003` after comparing it to the models, rehearsed on a throwaway database first.
- **Files.** `models.py`, new migration, `auth.py`, new `/auth/refresh`, `frontend/lib/api.ts`.
- **Risks.** Stamping changes the real database, which is why it is separate.

### R8 PostgreSQL roles (plan)

- **Obstacle.** Startup runs DDL: `create_all`, 17 added columns, 3 indexes and `CREATE OR REPLACE FUNCTION`. A restricted role cannot do this.
- **Plan.**
  1. A setting to skip startup DDL, with migrations doing that work.
  2. Reviewed scripts in `docs/sql/`, not run by me: `ryze_app` gets DML on application tables in `public` only, with sequences and no DDL. Migrations run under a separate owner role. `analytics_user` gets `SELECT` only where approved. Neither gets the side schemas.
  3. You set the new `DATABASE_URL`. Rollback is restoring the old value.
- **Tests.** Rehearsal on a throwaway database, positive and refused operations, then the full flow.
- **Constraints kept.** `public` and `banking` tables stay intact. Any new banking tables go in `banking`. Finance, Insurance, Retail and Healthcare schemas are not repurposed.

### R9 Databricks service principal (plan)

- **Plan.**
  1. Your administrator creates one service principal with `CAN USE` on the warehouse, and `USE CATALOG`, `USE SCHEMA` and `SELECT` on approved gold objects for each domain and tenant catalog. `docs/sql/banking_least_privilege.sql` extends to all five.
  2. Simplest start: a service-principal token in `DATABRICKS_TOKEN`, with **no code change**.
  3. Later: OAuth machine-to-machine behind `DATABRICKS_AUTH_MODE`, default `token`. It needs `databricks-sdk`, which is not installed, so it needs separate approval.
- **Tests.** `/warehouse/verify` per domain, live 35/35 and 62/62, and proof that out-of-scope catalogs are refused. Rollback is the old token.

## 5. Modularization

### Dependency findings

49 modules, 175 internal imports, **no cycles**, even counting 6 lazy imports. Most depended on: `config` (23), `domains` and `models` (10). Most dependent: `orchestrator` (33 modules), `main` (18). About 250 test patch paths name current modules. `app.domains` and `app.governance` are the only names that collide with target packages.

### Reversible method

1. **Compatibility modules.** Each moved file leaves one line at its old path that aliases the new module in `sys.modules`. Old imports and test patches reach the same object, so **each step runs against unmodified tests**.
2. **One small step at a time**, leaves first. Before each step I save a patch and tree snapshot to the scratch folder. Rollback is restoring it, or reverting the commit if you approve per-step commits.
3. **Rehearse in a scratch copy, then apply.** The shim method is new and is proved on the first step before reuse.
4. **Guards added first (M0):** a saved OpenAPI contract that must stay identical, a check that every path the frontend calls exists, an import-each-module test, undefined-name and cycle checks.

Applying a step reloads your dev server on port 8013.

### Steps

| Step | Scope | Risk |
|---|---|---|
| M0 | Guards above, layer rules as a test. No moves | Low |
| M1 | `core/`: config, security, exceptions, logging, reliability, cache, dependencies | Low |
| M2 | `identity/`: split `auth.py` into authentication, authorization, rbac, tenant_resolver; `services/access.py` | Medium |
| M3 | `data/postgres/`, `data/databricks/`: db, models, seed, warehouse | Medium |
| M4 | `domains/<five>/`: `DomainModule` per domain with registry, prompts, allowlist. `data/semantic_layer/`, `data/metadata/`. Catalog values stay in `.env` | Medium |
| M5 | `governance/`, `ai/guardrails/`, `billing/` | Medium |
| M6 | `ai/llm`, `rag`, `routing`, `text_to_sql`, `mcp`, `forecasting`, then `orchestration` last | Medium |
| M7 | `dashboard/`, `reports/`: dashboard SQL leaves `orchestrator.py` | Medium |
| M8 | `api/`: split `main.py` into routers; `uvicorn app.main:app` stays | High: 82 test patches |
| M9 | Services and repositories, detailed below | High |
| M10 | Rewrite imports and patch paths, remove compatibility modules | Medium, scripted |

### M9 layers

- **PostgreSQL repositories, one area at a time:** users and grants, conversations, history, reviews, knowledge, subscriptions, audit. About 96 ORM calls move out of routes and services.
- **Databricks repository:** wraps today's pooled execution. Every call needs a scope of domain and tenant, so authorization is checked at repository level too.
- **Query router, interface only:** it returns Databricks for analytics, matching your decision. Declared sources come in Phase 3.
- **Routes:** validate, authorize, call a service, return a Pydantic model.
- **LangGraph, RAG, MCP and HITL** behaviour is unchanged. They call the new layers through stable interfaces.

Not moved without proof: `services/warehouse.py` stays, unused, until you approve removal. `.bak` files stay on disk.

## 6. Verification per step

| Check | When |
|---|---|
| Full backend suite against the 527/0/9 baseline; any new tests reported separately | Every step |
| Offline evaluation, TypeScript, OpenAPI diff, frontend path check, static checks | Every step |
| Live registry and banking evaluations, read-only on Databricks | R5, M3, M4, M6, M8, M10 |
| Browser login, dashboard, Ask AI | R6, M2, M8, M10 |
| Architecture documents updated | End of each step |

Skipped tests stay reported as skipped. Mocked tests are not described as live.
