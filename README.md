# RYZE AI/BI (no Genie)

Multi-business chat BI: **Banking / Finance / Insurance / Retail**. Natural language → planner → context graph → SQL → guardrail → warehouse → self-hosted TabPFN prediction → synthesis.

Databricks **Genie is not used**. A Databricks SQL Warehouse or Snowflake can be configured as the live source for chat queries, TabPFN input rows, and dashboard metrics.

## Demo logins

| User | Password | Domain |
|---|---|---|
| admin | ryze123 | any domain |
| bank.user | banking123 | Banking |
| fin.user | finance123 | Finance |
| ins.user | insurance123 | Insurance |
| ret.user | retail123 | Retail |

## Run

Backend (`backend/`):

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --port 8011
```

Frontend (`frontend/`):

```bash
npm install
npm run dev -- --port 3001
```

Open http://localhost:3001. API is http://127.0.0.1:8011. Set `NEXT_PUBLIC_API_URL` in `frontend/.env.local` only if the API is hosted somewhere other than `http://localhost:8011`.

## Login and self-sign-up

The landing page supports both sign-in and self-sign-up. Self-sign-up is open in this build (there is no email verification or invite gate). New accounts choose one business domain and are always assigned the analyst role; public sign-up cannot create administrators or grant access to other domains. Usernames are normalized to lowercase, and passwords must be 12-128 characters. Passwords are stored as salted PBKDF2 hashes, and successful registration returns a domain-scoped JWT and signs the user in.

The application account, query history, and conversation memory are configured separately from the business-data warehouse through `DATABASE_URL`. Conversation turns are persisted and scoped to the authenticated user and domain; the assistant receives only the latest 12 turns as follow-up context. Do not expose open registration to the public internet until an approved access policy is in place. For production, use a protected persistent PostgreSQL database, set a unique `JWT_SECRET` of at least 32 characters, and set `DEMO_MODE=false`. The current browser stores the JWT in local storage; use an approved SSO and secure server-side session design before enterprise rollout.

## Context Intelligence

The landing-page **Watch Demo** link opens an interactive, domain-selectable architecture walkthrough. Its sample labels and concepts are illustrative only; they are not live metrics or a query trace. After sign-in, Dashboard shows the authenticated user, role, domain, and source metadata. **Context Intelligence** appears first in the workspace navigation and opens the read-only context explorer.

The explorer calls the authenticated `GET /context` API. It uses the JWT identity and permitted domain, returns source table/column metadata with sensitive columns omitted, includes glossary definitions scoped to that domain, and reports only relationships supported by database foreign-key metadata. Administrators may select among the domains allowed to their role; analyst requests for another domain are rejected by the API. The API does not treat a client-selected domain as authorization.

Organization scoping, editable/publishable context, approved metric and rule registries, lineage for individual query results, and context synchronization history are not configured in the current backend. The explorer reports these as unavailable or unconfigured rather than presenting sample values as live. `GET /context` is the read-only metadata contract; adding those capabilities requires backend persistence, authorization, and audit endpoints before enabling management controls.

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
DATABRICKS_DOMAIN_SCHEMA=silver
LIVE_SIMULATE=false
```

Set a persistent `DATABASE_URL` for application users, query history, and human-review records; those records remain in this application database, not in the Databricks business schema. Set a unique, random `JWT_SECRET` before exposing the API. `WAREHOUSE=databricks` makes chat queries, dashboard metrics, and TabPFN input reads use the configured warehouse. `LIVE_SIMULATE=false` prevents the app's demo ticker from changing local data.

The production application environment must also include:

```env
DEMO_MODE=false
JWT_SECRET=<random-secret-at-least-32-characters>
DATABASE_URL=<persistent-postgresql-connection-url>
```

Restart the backend after changing its environment. Sign in and open **Connect**. **Run connection check** verifies the catalog and required tables for the signed-in user's domain; it is an explicit action rather than a slow call made whenever the profile loads. A connection that succeeds but is missing a table or SELECT permission is not ready to run analytics. The dashboard queries are adapted to the table/column names in these `silver` schemas; other chat and TabPFN workflows still require compatible business-table schemas.

The **Benchmarks** page runs `POST /warehouse/verify`: it checks the active domain catalog/schema and expected tables, then executes a read-only representative aggregate and displays the SQL and returned values. It validates the data path; it does not claim model accuracy. For Banking, the configured schema has `customers.credit_score`, not the `fact_customers` table name in the Genie example. The verification query uses the existing `banking.silver.customers` source and returns the credit-score statistics and buckets directly from Databricks.

Chat SQL reads use the authenticated domain's mapped catalog and schema. Before execution, source table names and any explicit catalog/schema qualifiers are checked against the retrieved domain schema; unavailable tables produce an explicit error instead of silently querying another domain. Query lineage shows actual Databricks source tables rather than inferred Bronze/Gold layers. `/me` reports configuration only; the **Monitor** page loads audit activity scoped to the signed-in user and domain. Remote dashboard aggregates are cached for 30 seconds and the browser refreshes them every 30 seconds to avoid repeatedly submitting nine warehouse queries while the page is open.

### 4. Verify every business domain

Sign in with a user assigned to each configured domain (or use a development admin account), then run a small read-only query and open Dashboard. The app scopes access with the signed-in domain; it does not automatically change `domain` based on the question. Demo user accounts should not be used in production.

| Domain | Example chat question |
|---|---|
| Banking | `What is total deposit balance by region?` |
| Finance | `What is AUM by region?` |
| Insurance | `What is the loss ratio by product?` |
| Retail | `What is revenue by region?` |

Check that the returned SQL is read-only, results match a direct warehouse query, charts/tables use the expected measures, and an audit entry appears. Use **Dashboard** to inspect live metrics, **Benchmarks** to run the direct aggregate check, **Monitor** to inspect scoped audit activity, and **Connect** to verify which data mode is active.

For a non-demo installation, set `DEMO_MODE=false` and a unique `JWT_SECRET` (at least 32 characters) before starting the API. Startup rejects the development JWT secret in this mode. This prevents the application from creating or advertising the built-in demo users. New user passwords are stored as salted PBKDF2 hashes; existing demo hashes are upgraded after a successful login. Start the API once to initialize its authentication/history database, then create each real login interactively (the password is not passed as a command-line argument):

```powershell
cd backend
python create_user.py --username analyst --domain insurance --display-name "Insurance Analyst"
```

Repeat with `banking`, `finance`, or `retail`. Use domain `all` only for a tightly controlled administrator account. If a production database has previously been seeded with demo accounts, remove those accounts before exposing the application.

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
