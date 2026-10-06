# RYZE AI/BI (no Genie)

Multi-business chat BI: **Banking / Finance / Insurance / Retail**. Natural language → planner → context graph → SQL → guardrail → warehouse → self-hosted TabPFN prediction → synthesis.

Databricks **Genie is not used**. The Databricks SQL Warehouse can be configured as the live source for chat queries, TabPFN input rows, and dashboard metrics.

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

## LLM (planner / SQL / narrative)

Set one or more in `backend/.env`:

- OpenAI: `OPENAI_API_KEY`, `OPENAI_MODEL=gpt-4o-mini`
- Grok: `XAI_API_KEY`, `XAI_MODEL=grok-2-latest`
- Ollama: run `ollama serve` and `ollama pull llama3.2`, then choose Ollama in Ask

`LLM_PROVIDER=auto` uses OpenAI, then Grok. Heuristic SQL still works with no key.

## Local demo data

SQLite is seeded with ~80 demo customers per domain and thousands of generated events. The Analyze page can simulate live ticks when using the local warehouse. This is demo data, not production business data.

Keep `WAREHOUSE=local`, `DEMO_MODE=true`, and `LIVE_SIMULATE=true` for the reproducible local demo. Demo accounts are listed above. The Connect screen labels this mode as sample data; it does not mix sample rows into a Databricks query. Use the **Connect → Run TabPFN diagnostic** button to run an actual local TabPFN model against generated test rows. The TabPFN model download may make the first test take several minutes.

Postgres + PL/pgSQL:

```bash
docker compose up -d
```

`DATABASE_URL=postgresql://ryze:ryze@localhost:5432/ryze_bi`

Optional routines: `backend/sql/seed_plpgsql.sql` (`ryze_live_tick`).

## Databricks SQL Warehouse

Use the following checklist to connect your workspace. Databricks credentials belong only in the backend environment; never put them in `frontend/.env*`, commit them, or paste them into chat.

### 1. Prepare Databricks

1. In your Databricks workspace, create or choose a **SQL Warehouse**, start it, and record its **Server hostname** and **HTTP path** from the warehouse connection details.
2. Choose a catalog and create a schema, for example `main.ryze_bi`.
3. Create Delta tables in that schema with these column names and types. Keep the `domain` column on each business table so the app can scope results to Banking, Finance, Insurance, or Retail.

```sql
CREATE SCHEMA IF NOT EXISTS main.ryze_bi;

CREATE TABLE IF NOT EXISTS main.ryze_bi.customers (
  id BIGINT, domain STRING, name STRING, segment STRING, region STRING,
  account_number STRING, signup_date DATE, churned INT
) USING DELTA;

CREATE TABLE IF NOT EXISTS main.ryze_bi.products (
  id BIGINT, domain STRING, name STRING, category STRING, unit_price DOUBLE
) USING DELTA;

CREATE TABLE IF NOT EXISTS main.ryze_bi.orders (
  id BIGINT, domain STRING, customer_id BIGINT, order_date DATE,
  status STRING, total_amount DOUBLE
) USING DELTA;

CREATE TABLE IF NOT EXISTS main.ryze_bi.order_items (
  id BIGINT, domain STRING, order_id BIGINT, product_id BIGINT,
  quantity INT, amount DOUBLE
) USING DELTA;

CREATE TABLE IF NOT EXISTS main.ryze_bi.claims (
  id BIGINT, domain STRING, customer_id BIGINT, claim_date DATE,
  amount DOUBLE, status STRING, policy_number STRING
) USING DELTA;

CREATE TABLE IF NOT EXISTS main.ryze_bi.glossary (
  id BIGINT, domain STRING, business_term STRING, table_name STRING,
  column_name STRING, description STRING
) USING DELTA;

CREATE TABLE IF NOT EXISTS main.ryze_bi.live_events (
  id BIGINT, domain STRING, event_type STRING, amount DOUBLE, created_at TIMESTAMP
) USING DELTA;
```

For existing tables, add only the fields your approved analytics workflow needs:

```sql
ALTER TABLE main.ryze_bi.customers ADD COLUMN IF NOT EXISTS account_number STRING;
ALTER TABLE main.ryze_bi.claims ADD COLUMN IF NOT EXISTS policy_number STRING;
```

Populate them only with authorized, domain-scoped identifiers. Never store CVV/CVC or card security codes. Prefer vault-backed tokens or masked values where a full account number is not necessary.

Load your approved business data into these tables. Use stable, unique IDs and valid customer/product/order foreign keys. Supported `domain` values are `banking`, `finance`, `insurance`, and `retail`. `claims` is required by the warehouse readiness check; non-insurance rows may be absent. Validate row counts and domain coverage before connecting:

```sql
SELECT domain, COUNT(*) FROM main.ryze_bi.customers GROUP BY domain;
SELECT domain, COUNT(*) FROM main.ryze_bi.orders GROUP BY domain;
SELECT domain, COUNT(*) FROM main.ryze_bi.order_items GROUP BY domain;
SELECT domain, COUNT(*) FROM main.ryze_bi.products GROUP BY domain;
SELECT domain, COUNT(*) FROM main.ryze_bi.claims GROUP BY domain;
```

### 2. Grant the warehouse identity read access

Use a dedicated service identity for the application and grant only the permissions it needs. Replace the example principal with the service-principal name or group configured in your workspace:

```sql
GRANT USE CATALOG ON CATALOG main TO `<application-principal>`;
GRANT USE SCHEMA ON SCHEMA main.ryze_bi TO `<application-principal>`;
GRANT SELECT ON SCHEMA main.ryze_bi TO `<application-principal>`;
```

The connector uses a Databricks access token (`DATABRICKS_TOKEN`) today. For production, store a rotated service identity token in your hosting platform's secret manager and restrict its lifetime, permissions, and network access. Do not use a developer's personal token for a production deployment.

### 3. Configure and run the backend

Install `backend/requirements.txt`, copy `backend/.env.example` to `backend/.env`, and set:

```
WAREHOUSE=databricks
DATABRICKS_SERVER_HOSTNAME=adb-<workspace-id>.<cloud>.databricks.com
DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/<warehouse-id>
# Or omit DATABRICKS_HTTP_PATH and set DATABRICKS_WAREHOUSE_ID=<warehouse-id>.
DATABRICKS_WAREHOUSE_ID=
DATABRICKS_TOKEN=<backend-only-secret>
DATABRICKS_CATALOG=<catalog>
DATABRICKS_SCHEMA=ryze_bi
LIVE_SIMULATE=false
```

Set a persistent `DATABASE_URL` for application users, query history, and human-review records; those records remain in this application database, not in the Databricks business schema. Set a unique, random `JWT_SECRET` before exposing the API. `WAREHOUSE=databricks` makes chat queries, dashboard metrics, and TabPFN input reads use the configured warehouse. `LIVE_SIMULATE=false` prevents the app's demo ticker from changing local data.

The production application environment must also include:

```env
DEMO_MODE=false
JWT_SECRET=<random-secret-at-least-32-characters>
DATABASE_URL=<persistent-postgresql-connection-url>
```

Restart the backend after changing its environment. Sign in and open **Connect**. The **Check connections** action tests the warehouse and checks that all seven expected tables are queryable. A connection that succeeds but is missing a table or SELECT permission is not ready to run analytics.

### 4. Verify every business domain

Sign in with a user assigned to each configured domain (or use a development admin account), then run a small read-only query and open Analyze. The app scopes access with the signed-in domain; it does not automatically change `domain` based on the question. Demo user accounts should not be used in production.

| Domain | Example chat question |
|---|---|
| Banking | `What is total deposit balance by region?` |
| Finance | `What is AUM by region?` |
| Insurance | `What is the loss ratio by product?` |
| Retail | `What is revenue by region?` |

Check that the returned SQL is read-only, results match a direct warehouse query, charts/tables use the expected measures, and an audit entry appears. Use **Analyze** to inspect live dashboard metrics and **Connect** to verify which data mode is active.

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
