# RYZE AI/BI (no Genie)

Multi-business chat BI: **Banking / Finance / Healthcare / Insurance / Retail**. Natural language → planner → context graph → SQL → guardrail → warehouse → self-hosted TabPFN prediction → synthesis.

Databricks **Genie is not used**. A Databricks SQL Warehouse or Snowflake can be configured as the live source for chat queries, TabPFN input rows, and dashboard metrics.

Ask AI design and operations: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/RUNBOOK.md](docs/RUNBOOK.md). Banking: [report](docs/BANKING_REPORT.md), [audit](docs/BANKING_AUDIT.md), [metric registry](docs/BANKING_METRICS.md), [demo transcript](docs/BANKING_DEMO_TRANSCRIPT.md). Plans and AI usage: [docs/SUBSCRIPTIONS.md](docs/SUBSCRIPTIONS.md).

## Demo logins

| User | Password | Domain |
|---|---|---|
| admin | ryze123 | any domain |
| bank.user | banking123 | Banking |
| fin.user | finance123 | Finance |
| ins.user | insurance123 | Insurance |
| ret.user | retail123 | Retail |
| health.user | healthcare123 | Healthcare (`demo-healthcare` only) |

## Run

Backend (`backend/`):

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --port 8013
```

Frontend (`frontend/`):

```bash
npm install
npm run dev -- --port 3000
```

Open http://localhost:3000. API is http://localhost:8013. Set `NEXT_PUBLIC_API_URL` in `frontend/.env.local` only if the API is hosted somewhere other than `http://localhost:8013`. Keep the API hostname consistent with `OIDC_REDIRECT_URI` (`localhost` or `127.0.0.1`); mixing them prevents the browser from sending the OIDC session cookie to the callback.

## Login and self-sign-up

The landing page supports both sign-in and self-sign-up. Self-sign-up is open in this build (there is no email verification or invite gate). New accounts choose one business domain and are always assigned the analyst role; public sign-up cannot create administrators or grant access to other domains. Usernames are normalized to lowercase, and passwords must be 12-128 characters. Passwords are stored as salted PBKDF2 hashes, and successful registration returns a domain-scoped JWT and signs the user in.

The application account, query history, and conversation memory are configured separately from the business-data warehouse through `DATABASE_URL`. Conversation turns are persisted and scoped to the authenticated user, domain, and (for Healthcare) tenant; the assistant receives only the latest 12 turns as follow-up context. Password sign-in continues to use bearer tokens; optional OpenID Connect sign-in uses an HttpOnly, SameSite=Lax cookie. Existing application accounts must be explicitly linked to their verified provider identity. Users may also explicitly create a new Google-only analyst account after verified Google sign-in; the selected username must be unused, and the account is restricted to one supported business domain. This flow cannot link an existing account, create administrators, or provision Healthcare tenant access. Healthcare access requires an administrator-provisioned `user_domain_tenants` mapping. Do not expose open registration to the public internet until an approved access policy is in place. For production, use a protected persistent PostgreSQL database, set a unique `JWT_SECRET` of at least 32 characters, and set `DEMO_MODE=false`.

## Roles and domain access

There are two roles. **Administrators** (`app_users.domain = 'all'`) can open all five domains and use the Admin console (`/admin`). **Business users** can open their primary domain (`app_users.domain`) plus any domain an administrator has approved (`user_domain_grants`). The backend checks the active domain against these tables on every request, so a revoked domain stops working immediately. A role or domain claimed in a token, URL or request body is never trusted.

- Business users request more domains from **Access** (`/access`) with a reason. Each request is stored as `pending` (one pending request per user and domain, enforced by a unique index). Administrators see a badge in the header and approve or reject it in **Admin → Access requests**. Approval creates the grant. Every request, decision, grant, revocation and role change is recorded with the actor and timestamp in `access_audit_events` (**Admin → Audit history**).
- Users and administrators switch between their authorized domains with the domain selector in the header (`POST /auth/switch-domain`, which re-issues the session only for an authorized domain). Healthcare additionally requires a provisioned `user_domain_tenants` row.
- Google sign-in and self-sign-up never create administrators. Provision the first administrator server-side, then manage roles in the Admin console:

  ```bash
  python create_user.py --username ops.admin --domain all --display-name "Operations Admin"   # new admin
  python set_user_role.py --username jane.doe --role admin                                  # promote an existing (e.g. Google) user
  python set_user_role.py --username jane.doe --role analyst --primary-domain retail         # demote
  ```

  An administrator cannot change their own role, and the last administrator cannot be removed.

The tables `user_domain_grants`, `domain_access_requests` and `access_audit_events` are created automatically by the API at startup (`ensure_schema`); no manual migration is required.

## Valkey cache and OpenID Connect

The optional shared cache uses MIT-licensed `redis-py` against any Redis-compatible service, including BSD-3-Clause-licensed open-source Valkey. Dashboard summary entries are keyed by authenticated username, domain, role, tenant, warehouse, and simulation mode; only remote non-Healthcare warehouse summaries are cached. Healthcare dashboards bypass caching until an approved source freshness and privacy policy is configured. No tokens, patient records, or natural-language answers are cached. Cache connection failures are logged and the configured warehouse remains the source of truth. The local in-process dashboard cache remains as a fallback for remote non-Healthcare summaries.

Start the local PostgreSQL and Valkey services from the repository root:

```powershell
docker compose up -d postgres valkey
Set-Location backend
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Set these values in `backend\.env` as appropriate. Keep `CACHE_URL` empty to disable shared caching, or set it to `redis://localhost:6379/0`; `CACHE_TTL_SECONDS` controls the maximum dashboard summary TTL (default 30 seconds). `DATABASE_URL` accepts the existing SQLite default or PostgreSQL URLs using either `postgresql+psycopg2://<user>:<password>@localhost:5432/<database>` or `postgresql+psycopg://<user>:<password>@localhost:5432/<database>`. Set `CORS_ORIGINS` to the comma-separated exact frontend origins when deploying outside localhost. Schema setup only creates missing tables and adds known nullable columns/indexes; it never drops or resets a database. A legacy `customers` table without its required `domain` column stops startup with an explicit migration error rather than being rewritten. No Alembic configuration exists in this project.

The backend supports any OIDC provider with discovery metadata; the default discovery URL is Google's. To enable it:

1. Create an OAuth 2.0 **Web application** client in Google Cloud Console. Configure the consent screen and test users as required by Google's current policies.
2. Register the exact redirect URI `http://localhost:8013/auth/oidc/callback` for local testing. For production, register the public HTTPS callback exactly as set in `OIDC_REDIRECT_URI`.
3. Set `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_REDIRECT_URI`, and `FRONTEND_URL` in `backend\.env`. Keep credentials only in the backend environment or a secret manager. `OIDC_DISCOVERY_URL` defaults to `https://accounts.google.com/.well-known/openid-configuration`.
4. Existing application users must be linked by an administrator through an `oauth_identities` row mapping the verified `(issuer, subject)` pair to an existing `app_users.username`. For Google, issuer is `https://accounts.google.com`. Obtain the subject through your trusted identity-provisioning process and insert it through a restricted database administration session; do not log or paste ID tokens. Example SQL (replace the placeholders with trusted values):

   ```sql
   INSERT INTO oauth_identities (issuer, subject, username)
   SELECT 'https://accounts.google.com', '<verified-provider-subject>', username
   FROM app_users
   WHERE username = '<existing-application-username>';
   ```
5. Restart the backend. Existing linked users choose **Sign in with Google**. To create a new Google-only account, choose **Create an account**, enter an unused username and supported business domain, then choose **Sign up with Google**. The new account is created as an analyst only after Google confirms a verified email. Google sign-up cannot link an existing account, create an administrator, or provision Healthcare access. OAuth remains unavailable with a clear status when required configuration is missing; password sign-in continues to work.
6. First-time Google sign-in without a pre-chosen username: when **Sign in with Google** returns a verified identity that is not linked to any account, the API issues no session. It sets a 15-minute HttpOnly `ryze_oidc_onboarding` cookie (scoped to `/auth/oidc/onboarding`) and redirects to `/?onboarding=select`, where a popup asks the user to pick Banking, Finance, Insurance, Retail, or Healthcare. `POST /auth/oidc/onboarding` creates an analyst account (username derived from the Google email, de-duplicated) with that domain, links the Google identity, and sets the normal `ryze_session` cookie. Later Google sign-ins go straight to the saved domain. The `all` (administrator) domain cannot be selected, and the domain cannot be changed through self-service. Choosing Healthcare saves the request but issues no session until an administrator adds the `user_domain_tenants` row shown below; until then sign-in shows a "Healthcare access is pending" popup.

Google does not charge for basic OAuth sign-in, but Google Cloud account, consent-screen, branding, and verification policies apply and can change. Microsoft Entra ID also supports OIDC, but requires an Entra tenant/app registration; account types, tenant controls, and licensing depend on the organization's plan. The app itself uses open-source Authlib and Redis-compatible Valkey; external provider account and deployment policies are separate.

Quick local checks from the repository root:

```powershell
docker compose ps
Set-Location backend
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
$env:WAREHOUSE = "local"
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest tests -q
Set-Location ..\frontend
npm run build
```

The `/health` response reports database connectivity and latency plus cache status (`ok`, `unavailable`, or `disabled`). Request logs include a validated/generated `X-Request-ID` and total latency; successful SQL statements include query latency. Local OIDC testing requires provider credentials and the exact localhost redirect registration; cache behavior and database health are testable without external credentials.

## Analytics validation tests

The backend analytics tests cover PostgreSQL and Databricks connection checks, schema and column discovery, SQL generation/source validation, SQL injection and hallucination rejection, result-grounded synthesis, audit metadata, result normalization, and row limits. See [ANALYTICS_TEST_CATALOG.md](./ANALYTICS_TEST_CATALOG.md) for positive, negative, boundary, and edge cases plus commands to run mocked unit tests and opt-in read-only service smoke tests. The live tests require `RUN_LIVE_ANALYTICS_TESTS=1` and use `AI_ANALYTICS_TEST_DATABASE_URL` for PostgreSQL; secrets should remain in process/backend environment variables and must not be committed.

## Context Intelligence

The landing-page **Watch Demo** link opens an interactive, domain-selectable architecture walkthrough. Its sample labels and concepts are illustrative only; they are not live metrics or a query trace. After sign-in, Dashboard shows the authenticated user, role, domain, and source metadata. **Context Intelligence** appears first in the workspace navigation and opens the read-only context explorer.

The explorer calls the authenticated `GET /context` API. It uses the JWT identity and permitted domain, returns source table/column metadata with sensitive columns omitted, includes glossary definitions scoped to that domain, and reports only relationships supported by database foreign-key metadata. Administrators may select among the domains allowed to their role; analyst requests for another domain are rejected by the API. The API does not treat a client-selected domain as authorization.

Organization scoping, editable/publishable context, approved metric and rule registries, lineage for individual query results, and context synchronization history are not configured in the current backend. The explorer reports these as unavailable or unconfigured rather than presenting sample values as live. `GET /context` is the read-only metadata contract; adding those capabilities requires backend persistence, authorization, and audit endpoints before enabling management controls.

## Ask AI workflow, grounding and review

Each question runs: planner → Unity Catalog metadata and schema retrieval → SQL generation → validation (read-only, authorized catalog/schema, tables **and columns** checked against metadata, requested measures present, sensitive fields) → execution on the configured SQL Warehouse → TabPFN when a prediction is requested → answer synthesis with a numeric grounding check → history. Every response carries a `status` (`complete`, `no_data`, `insufficient_data`, `needs_clarification`, `awaiting_review`, `error`) and a `trace` showing which engine handled each stage. `llm_provider` lists only providers that actually produced output; verified query templates are labelled as such.

- **Grounding:** an aggregate over no matching rows is reported as "no records" with the source's real date coverage, never as a measured zero. LLM answers citing numbers that cannot be traced to the result are discarded in favour of a deterministic summary.
- **Follow-ups:** self-contained questions are classified deterministically; follow-ups that refer to earlier turns ("compare it…", "why did that…") use one LLM planner call that rewrites them as a standalone question.
- **Providers:** the selected provider is used as-is. Transient errors (5xx, overload) are retried on the same provider; exhausted quotas are not retried, and nothing is answered from a failed call.
- **Predictions:** time-series questions ("next month/quarter") are projected by TabPFN from date-part and lag features of the actual history, returning the next period with an 80% range. Fewer than 10 periods is reported as insufficient history. Record-level questions (for example churn) use the existing cross-validated scoring.
- **HITL:** with `HITL_PREDICTION_REVIEW=true` (default), business users' prediction requests are held: the historical data is shown, an administrator (never the requester) approves or rejects in the HITL tab, and the requester chooses "Run approved prediction" to resume in the same conversation. Administrators' own requests are not held.
- **TabPFN weights:** the model loads its weights from the local TabPFN cache (on Windows `%APPDATA%\tabpfn`). If they are missing and cannot be downloaded (for example a proxy that breaks TLS verification), predictions report that no prediction was produced. Download the `tabpfn-v2.5-*.ckpt` files through an approved channel, or configure your corporate CA bundle; do not disable certificate verification.
- **Live tests:** `RYZE_LIVE_TESTS=1 pytest tests/test_ask_ai_workflow.py` additionally runs read-only checks against the configured Databricks warehouse and LLM.

## LLM (planner / SQL / narrative)

Set one or more in `backend/.env`:

- OpenAI: `OPENAI_API_KEY`, `OPENAI_MODEL=gpt-4o-mini`
- Grok: `XAI_API_KEY`, `XAI_MODEL=grok-2-latest`
- Anthropic: `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` (default `claude-haiku-4-5`)
- Ollama: install/start Ollama, pull a model, then set `LLM_PROVIDER=ollama` and `OLLAMA_MODEL` to the exact installed tag. The default is `qwen3:4b`.

For example, in PowerShell:

```powershell
ollama pull qwen3:4b
ollama pull deepseek-r1:8b
```

The app uses one Ollama model at a time; change `OLLAMA_MODEL` and restart the backend to switch. Other Ollama library options include `gemma3:4b` and `mistral:7b`; check their download size and your machine's memory before pulling. Ollama serves its OpenAI-compatible API at `http://127.0.0.1:11434/v1`. `LLM_PROVIDER=auto` prefers configured Anthropic, then OpenAI, then Grok, then local Ollama. Heuristic SQL still works if no LLM service is reachable. API keys belong only in the backend environment or a secrets manager.

### Local schema RAG and MCP

The assistant uses LangGraph to plan and retrieve context, LangChain with ChromaDB for a local persistent vector index, and Ollama `nomic-embed-text` for local embeddings. The index contains only database table/column descriptions and the business glossary; it does not index business rows or user conversations. Install the Python requirements, run `ollama pull nomic-embed-text`, then start the backend. Set `RAG_EMBEDDING_MODEL` or `CHROMA_DB_PATH` in `backend/.env` only if you need to change the defaults. Chroma's `.chroma/` data directory is local and ignored by git.

An optional stdio MCP server exposes `list_business_tables` and `ask_business` tools. Start it from `backend/` with `python -m app.mcp_server` and set `MCP_ACCESS_TOKEN` in the server process environment to a short-lived JWT for a least-privileged user. Each tool revalidates that token against the application database; analytics still passes through domain scoping, sensitive-data checks, read-only SQL guardrails, audit history, and HITL handling. Do not commit or share the MCP token.

## Local demo data

SQLite is seeded with ~80 demo customers per domain and thousands of generated events. The Dashboard page can simulate live ticks when using the local warehouse. This is demo data, not production business data.

Keep `WAREHOUSE=local`, `DEMO_MODE=true`, and `LIVE_SIMULATE=true` for the reproducible local demo. Demo accounts are listed above. The Connect screen labels this mode as sample data; it does not mix sample rows into a Databricks query. Use the **Connect → Run TabPFN diagnostic** button to run an actual local TabPFN model against generated test rows. The TabPFN model download may make the first test take several minutes.

PostgreSQL + PL/pgSQL (local development):

```bash
docker compose up -d
```

Then set the backend `.env` to `DATABASE_URL=postgresql://ryze:ryze@localhost:5432/ryze_bi` and restart the API. These are development-only credentials from the repository's Docker Compose file; replace them and use a managed secret for any shared or production database. On an empty database, startup creates the application tables and seeds synthetic demo data when `DEMO_MODE=true`. PostgreSQL stores app history/auth data and the local business sample tables; the separate `WAREHOUSE` setting can still select Databricks.

The Banking sample is generated data, not real account-holder data. Alongside shared `customers`, `products`, `orders`, `order_items`, and `glossary`, the app creates dedicated `accounts`, `transactions`, and `loans` tables and seeds them for existing demo Banking customers. The account identifiers use a `DEMO-BANK-` prefix and are synthetic; transaction and loan amounts are generated. Re-running startup does not duplicate this Banking sample. These tables are created and seeded only when `DEMO_MODE=true`.

Optional PostgreSQL routine: startup creates `ryze_live_tick`; the SQL source is `backend/sql/seed_plpgsql.sql`.

### Healthcare demo and tenant provisioning

The local Healthcare workspace seeds only generated, de-identified demo rows under tenant ID `demo-healthcare`. Synthetic patient keys use `DEMO-PAT-`; no real patient data or production Healthcare connection is included. The dashboard reports patients, encounters, approved claims, encounter cancellation, and facility quality/region summaries instead of retail revenue or customer churn.

The proposed source tables are `healthcare_patients`, `healthcare_encounters`, `healthcare_diagnoses`, `healthcare_procedures`, `healthcare_claims`, `healthcare_providers`, `healthcare_facilities`, and `healthcare_quality_metrics`. Each must contain `tenant_id`. Local SQLAlchemy tables use composite tenant-scoped primary/foreign keys. Every generated Healthcare SELECT is parsed and rewritten server-side to constrain each source table to the authenticated tenant; non-Healthcare sources and record-level identifier projections are rejected. Dashboard results and saved query/conversation/review history are also tenant-scoped. Clinical prediction is intentionally disabled until a validated model and clinical governance policy are configured. These controls do not establish a production source connection or regulatory compliance; verify the real catalog, column types, privacy controls, small-cohort policy, retention rules, and source permissions before connecting healthcare data.

Healthcare assignments are authoritative rows in `user_domain_tenants`, unique per `(username, domain)`. Only a database administrator or the `create_user.py` provisioning command should create them. Example for an existing application user (use the actual authorized tenant ID):

```sql
INSERT INTO user_domain_tenants (username, domain, tenant_id)
SELECT username, 'healthcare', '<authorized-tenant-id>'
FROM app_users
WHERE username = '<existing-application-username>';
```

When creating a new Healthcare account, use the interactive command from `backend\`:

```powershell
.\.venv\Scripts\python.exe create_user.py --username analyst --domain healthcare --tenant-id "tenant-prod" --display-name "Healthcare Analyst"
```

Replace `tenant-prod` with the administrator-approved tenant ID. For OIDC, a verified identity-provider claim is optional and can only confirm (never grant) the mapped tenant. Set `OIDC_TENANT_CLAIM` to the exact top-level claim name, such as `tenant_id`; leave it empty if the provider does not supply one. A mismatched claim fails sign-in.

## Databricks SQL Warehouse

Use the following checklist to connect your workspace. Databricks credentials belong only in the backend environment; never put them in `frontend/.env*`, commit them, or paste them into chat.

The backend accepts both `DATABRICKS_SERVER_HOSTNAME` / `DATABRICKS_TOKEN` / `DATABRICKS_WAREHOUSE_ID` and the aliases `DATABRICKS_HOST` / `DATABRICKS_PAT` / `WAREHOUSE_ID`. An HTTP path is still required; when omitted, it is derived from the warehouse ID. Genie is not used.

## Snowflake

Install `backend/requirements.txt`, then set the Snowflake connection in the backend environment:

```env
WAREHOUSE=snowflake
SNOWFLAKE_ACCOUNT=<account-identifier>
SNOWFLAKE_USER=<service-user>
SNOWFLAKE_PASSWORD=<backend-only-secret>
SNOWFLAKE_ROLE=<least-privilege-role>
SNOWFLAKE_WAREHOUSE=<warehouse>
SNOWFLAKE_DATABASE=<database>
SNOWFLAKE_SCHEMA=PUBLIC
LIVE_SIMULATE=false
```

The connector checks for the same ten application tables documented above and executes guarded, read-only `SELECT` queries. Use a dedicated role with warehouse usage and `SELECT` permissions on those tables. Snowflake remains the business-data source; the separate `DATABASE_URL` holds app accounts, query history, and review records. Never put Snowflake passwords in frontend configuration, source control, or chat.

### 1. Use the catalogs already in the workspace

The dashboard uses the existing domain-specific `silver` schemas rather than assuming a shared `main.ryze_bi` schema:

| Domain | Catalog | Dashboard source tables |
|---|---|---|
| Banking | `banking` | `accounts`, `branches`, `transactions`, `products`, `claims` |
| Insurance | `insurance` | `policies`, `claims`, `products`, `payments`, `customers_with_payment_test_data` |
| Retail | `retail` | `retail_orders`, `retail_products`, `retail_customers` |

The dashboard explicitly selects aggregate measures and safe dimensions. It does not select card numbers, CVV, passwords, phone numbers, or email addresses from the test-data columns. Finance is not present in the current workspace's accessible catalog list; configure an authorized Finance catalog and add it to `DATABRICKS_DOMAIN_CATALOGS` before enabling that domain's live dashboard.

Grant the application service identity `USE CATALOG`, `USE SCHEMA`, and `SELECT` on the applicable catalogs and `silver` schemas. The connector checks expected table names and can report a catalog/schema permission or table issue. Store the rotated `DATABRICKS_TOKEN` in the backend secret environment, not in source control, a frontend variable, or chat.

### 3. Configure and run the backend

Install `backend/requirements.txt`, copy `backend/.env.example` to `backend/.env`, and set:

```
WAREHOUSE=databricks
DATABRICKS_SERVER_HOSTNAME=adb-<workspace-id>.<cloud>.databricks.com
DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/<warehouse-id>
# Or omit DATABRICKS_HTTP_PATH and set DATABRICKS_WAREHOUSE_ID=<warehouse-id>.
DATABRICKS_WAREHOUSE_ID=
DATABRICKS_TOKEN=<backend-only-secret>
DATABRICKS_CATALOG=banking
DATABRICKS_SCHEMA=silver
DATABRICKS_DOMAIN_CATALOGS=banking:banking,insurance:insurance,retail:retail
DATABRICKS_DOMAIN_SCHEMA=gold
DATABRICKS_HEALTHCARE_TENANT_CATALOGS=demo-healthcare:healthcare
LIVE_SIMULATE=false
```

Set a persistent `DATABASE_URL` for application users, query history, and human-review records; those records remain in this application database, not in the Databricks business schema. Set a unique, random `JWT_SECRET` before exposing the API. `WAREHOUSE=databricks` makes chat queries, dashboard metrics, and TabPFN input reads use the configured warehouse. `LIVE_SIMULATE=false` prevents the app's demo ticker from changing local data.

To enable a Healthcare source on Databricks, provision a separate catalog for each tenant. Add `healthcare:<default-catalog>` to `DATABRICKS_DOMAIN_CATALOGS` for schema discovery, and map each operational `user_domain_tenants.tenant_id` to its isolated catalog with `DATABRICKS_HEALTHCARE_TENANT_CATALOGS=tenant-id:catalog`. The dashboard uses aggregate-only queries against the catalog's `gold` schema (`agg_claims_monthly`, `agg_claims_daily`, `gold_claims`, `gold_patients`, `gold_encounters`, and `gold_facilities`); it never falls back to another tenant's catalog or local sample data. Provision user-to-tenant rows separately in the operational PostgreSQL database and grant the service identity access only to the mapped catalogs.

The production application environment must also include:

```env
DEMO_MODE=false
JWT_SECRET=<random-secret-at-least-32-characters>
DATABASE_URL=<persistent-postgresql-connection-url>
```

Restart the backend after changing its environment. Sign in and open **Connect**. **Run connection check** verifies the catalog and required tables for the signed-in user's domain; it is an explicit action rather than a slow call made whenever the profile loads. A connection that succeeds but is missing a table or SELECT permission is not ready to run analytics. The dashboard queries are adapted to the table/column names in these `silver` schemas; other chat and TabPFN workflows still require compatible business-table schemas.

The **Benchmarks** page runs `POST /warehouse/verify`: it checks the active domain catalog/schema and expected tables, then executes a read-only representative aggregate and displays the SQL and returned values. It validates the data path; it does not claim model accuracy. For Banking, the configured schema has `customers.credit_score`, not the `fact_customers` table name in the Genie example. The verification query uses the existing `banking.silver.customers` source and returns the credit-score statistics and buckets directly from Databricks.

Chat SQL reads use the authenticated domain's mapped catalog and schema. Before execution, source table names and any explicit catalog/schema qualifiers are checked against the retrieved domain schema; unavailable tables produce an explicit error instead of silently querying another domain. Query lineage shows actual Databricks source tables rather than inferred Bronze/Gold layers. `/me` reports configuration only; the **Monitor** page loads audit activity scoped to the signed-in user and domain. Remote dashboard summaries use the optional Valkey cache with `CACHE_TTL_SECONDS` (default 30 seconds), plus the process-local fallback; the browser refreshes every 30 seconds to avoid repeatedly submitting warehouse queries while the page is open.

### 4. Verify every business domain

Sign in with a user assigned to each configured domain (or use a development admin account), then run a small read-only query and open Dashboard. The app scopes access with the signed-in domain; it does not automatically change `domain` based on the question. Demo user accounts should not be used in production.

| Domain | Example chat question |
|---|---|
| Banking | `What is total deposit balance by region?` |
| Finance | `What is AUM by region?` |
| Insurance | `What is the loss ratio by product?` |
| Retail | `What is revenue by region?` |
| Healthcare | `Show encounter volume by month` (synthetic local demo, or verified tenant-scoped source) |

Check that the returned SQL is read-only, results match a direct warehouse query, charts/tables use the expected measures, and an audit entry appears. Use **Dashboard** to inspect live metrics, **Benchmarks** to run the direct aggregate check, **Monitor** to inspect scoped audit activity, and **Connect** to verify which data mode is active.

For a non-demo installation, set `DEMO_MODE=false` and a unique `JWT_SECRET` (at least 32 characters) before starting the API. Startup rejects the development JWT secret in this mode. This prevents the application from creating or advertising the built-in demo users. New user passwords are stored as salted PBKDF2 hashes; existing demo hashes are upgraded after a successful login. Start the API once to initialize its authentication/history database, then create each real login interactively (the password is not passed as a command-line argument):

```powershell
cd backend
python create_user.py --username analyst --domain insurance --display-name "Insurance Analyst"
```

Repeat with `banking`, `finance`, or `retail`. For Healthcare, include `--tenant-id` with the administrator-approved mapping; all other domains reject that option. Use domain `all` only for a tightly controlled administrator account. If a production database has previously been seeded with demo accounts, remove those accounts before exposing the application.

### 5. Verify TabPFN end to end

The Connect page's **Run TabPFN diagnostic** action performs real cross-validated inference with `TabPFNClassifier` on generated synthetic records. A passing result verifies the Python package can load a model and run locally; its displayed test accuracy is only for that synthetic test and is **not** a business KPI. Some TabPFN weight downloads require model access on Hugging Face. If the diagnostic reports HTTP 401/403, sign in to Hugging Face, accept the model's access terms, and provide an access token with read permission as the backend process's `HF_TOKEN` secret (or pre-provision the approved model weights/cache). Do not commit the token or put it in frontend configuration. Restart the backend after configuring the secret.

To verify a business prediction, run `Predict which customers are likely to churn` in Ask after confirming the warehouse has enough labeled rows and useful feature columns. The prediction requires at least eight labeled result rows and at least two examples per class. Check the TabPFN prediction table, confidence values, and audit record. The first inference may download model weights; allow outbound access to the model source or configure a pre-provisioned model cache in your deployment. Errors are shown explicitly; the app does not quietly switch to a different prediction model.

### 6. Deliberate sample-data mode

If the production tables are not ready or the warehouse is unavailable, use the local seeded data deliberately for a demo:

```env
WAREHOUSE=local
DEMO_MODE=true
LIVE_SIMULATE=true
```

Restart the API and confirm **Connect** says **Demo mode · seeded sample data**. If Databricks is selected but the connection check fails, the app reports the failure rather than silently presenting sample data as live. Switch back to `WAREHOUSE=databricks` only after the connection and table checks pass.

The chat welcome screen includes three clickable sensitive-data demo prompts for the signed-in domain. They show a one-sentence, domain-specific protection notice without sending the request to the analytics API. The dashboard displays an insurance claims-to-premium loss ratio; Banking uses delinquent exposure, while Finance and Retail use clearly labeled cancelled-trade/sales demo proxies.

### Production checklist

- Set a random `JWT_SECRET`, a persistent protected `DATABASE_URL`, `DEMO_MODE=false`, `WAREHOUSE=databricks`, and `LIVE_SIMULATE=false`.
- Provision application login identities and strong password hashes in the application's `app_users` table before disabling demo mode; demo accounts are intentionally not seeded or advertised when `DEMO_MODE=false`. Use a fresh production database, or remove demo users from an existing database before exposing it.
- Put the Databricks token and LLM keys in the deployment secret manager. Do not ship `.env` files or secrets with the frontend.
- Use HTTPS, a restricted API origin, network allow-lists, least-privilege warehouse grants, backups, log redaction, and a retention policy for query history.
- The chat displays a single-sentence, domain-specific sensitive-data notice for detected requests; its client-side detector is demo UX, not an authorization boundary. The API independently enforces recognized sensitive requests. Payment card numbers are always masked to their last four digits, even for admins; CVV/CVC and card security codes must never be returned or stored. Wildcard column selection is blocked to prevent accidental retrieval of unclassified sensitive columns. Use a PCI-compliant payment provider for payment authorization.
- To make approved identifiers available to an admin, add the appropriate domain-scoped columns to the live source schema (for example, `customers.account_number` or `claims.policy_number`). Admin requests inspect Databricks `information_schema`, select only the matching explicit field plus safe display columns, and remain filtered to the signed-in domain. Local demo mode uses clearly synthetic `DEMO-ACCT-...` and `DEMO-POL-...` values; they are not real account or policy credentials. Production mode never seeds these demo values. Prefer vault tokens or masked account identifiers where possible.
- Before public/enterprise rollout, replace the app's browser-local bearer-token session with your approved SSO/OIDC and secure HttpOnly cookie/session design, and complete a threat-model and access-control review. The current demo login/domain boundary is not a complete enterprise identity system.
- Validate that generated SQL, row/column-level data policy, model outputs, and audit retention are appropriate for your real data before enabling general users.
