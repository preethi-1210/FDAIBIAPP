# Analytics integration test catalog

This catalog describes the automated checks for the natural-language analytics flow:
authenticated request → schema context → Ollama/LLM SQL proposal → SQL guardrails →
PostgreSQL or Databricks execution → normalized result.

## Coverage and test cases

| ID | Area | Type | Test / expected result |
|---|---|---|---|
| DB-01 | PostgreSQL connection | Positive, live | Read-only `SELECT 1` succeeds using `AI_ANALYTICS_TEST_DATABASE_URL`. |
| DB-02 | PostgreSQL connection | Negative, live | An unreachable host, invalid credentials, or unavailable database fails the connection smoke test; no schema/data writes are attempted. |
| DB-03 | PostgreSQL driver | Boundary | Use the same SQLAlchemy dialect/driver in the URL and installed environment; the supplied `postgresql+psycopg` URL requires psycopg v3. |
| DBX-01 | Databricks connection | Positive, live | The configured warehouse authenticates and verifies the selected domain's required tables. |
| DBX-02 | Databricks connection | Negative | Missing credentials or HTTP path reports not-ready; a configured connection failure is reported as disconnected. |
| DBX-03 | Databricks authorization | Negative, boundary | Unknown domain, malformed catalog, and SQL-injected catalog identifiers are rejected. |
| SCH-01 | Schema discovery | Positive | The local SQLAlchemy inspector returns discovered tables, columns, and types. |
| SCH-02 | Schema discovery | Edge | An empty database returns an empty schema mapping. |
| META-01 | Metadata extraction | Positive, edge | Information-schema rows are grouped by table; key casing is normalized and column order is preserved. |
| META-02 | Databricks metadata | Positive | Discovery uses the domain-authorized catalog and configured schema, not a different domain's catalog. |
| META-03 | Table / column discovery | Positive | Returned column names and types match the source metadata and retain their table association. |
| LLM-01 | Ollama | Positive, live | Ollama returns parseable JSON for a deterministic JSON-mode prompt. |
| LLM-02 | Ollama | Negative | Unavailable Ollama/model produces a failing smoke test rather than a successful response-shaped result. |
| SQL-01 | Query generation | Positive | A generated query referencing an authorized table passes source validation and receives a row limit. |
| SQL-02 | Query generation | Negative | A generated query referencing a table absent from retrieved schema is rejected before execution. |
| SQL-03 | Hallucination detection | Negative | A model-invented source table fails authorized-source validation. |
| SQL-07 | Result-grounded synthesis | Negative | The answer is replaced by a deterministic fallback if the model invents numeric values absent from the query result. |
| SQL-04 | Prompt injection | Negative | Model output containing a write statement or multiple statements fails SQL validation. |
| SQL-05 | Query boundary | Boundary | A SELECT without a limit gets the configured row limit; request prompts outside 3–2000 characters fail request validation. |
| SQL-06 | SQL injection prevention | Negative, edge | DML, DDL, semicolon-separated statements, and comment-obfuscated write attempts are rejected. |
| EXE-01 | Query execution | Positive | A read-only SELECT executes against a temporary SQLite database and returns the expected rows. |
| EXE-02 | Query execution | Negative | Invalid SQL and all rejected write/multi-statement inputs fail without changing stored data. |
| EXE-03 | Query execution | Boundary / performance | `fetchmany` enforces the configured maximum row count for large result sets. |
| RES-01 | Result validation | Positive | Result rows have stable column/value mappings matching the SELECT projection. |
| RES-02 | Result validation | Edge | Non-JSON-native database values are normalized to strings for API-safe output. |
| AUD-01 | Audit trail | Positive | Query history exposes its audit ID, source tables, result row count, and guardrail outcome. |
| PERF-01 | Performance guard | Boundary | Large-result retrieval is bounded by `query_row_limit`; live response-time thresholds require a representative warehouse and are not asserted in unit tests. |
| SEC-01 | Security | Negative | Query validation rejects writes, multiple statements, and obfuscated write attempts. |
| SEC-02 | Security | Negative | SQL generation and execution remain restricted to discovered and authorized sources. |
| SEC-03 | Security | Boundary | Databricks catalog selection is restricted to a valid configured domain mapping. |

## Run unit tests

From the repository root:

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m pytest tests\test_analytics_validation.py tests\test_warehouse.py tests\test_databricks_dashboard.py tests\test_llm.py tests\test_schema_rag.py -q
```

## Run live smoke tests

Live checks are opt-in and read-only. They do not create, alter, or delete database
objects. Keep credentials in the backend environment or PowerShell process environment;
do not add them to source files or command output.

```powershell
Set-Location backend
$env:RUN_LIVE_ANALYTICS_TESTS = "1"
$env:AI_ANALYTICS_TEST_DATABASE_URL = "postgresql+psycopg://<user>:<password>@127.0.0.1:5432/ai_analytics"
.\.venv\Scripts\python.exe -m pytest tests\test_analytics_validation.py -q
Remove-Item Env:RUN_LIVE_ANALYTICS_TESTS
Remove-Item Env:AI_ANALYTICS_TEST_DATABASE_URL
```

Databricks live validation additionally requires `WAREHOUSE=databricks` and valid
`DATABRICKS_*` values in `backend\.env`. Set `ANALYTICS_TEST_DOMAIN` to a configured
domain if it is not `banking`. Ollama validation uses the configured `OLLAMA_BASE_URL`
and `OLLAMA_MODEL`.

The default test suite uses mocks and temporary in-memory SQLite databases. It does
not need live credentials, and it does not depend on PostgreSQL, Databricks, or Ollama
being available.
