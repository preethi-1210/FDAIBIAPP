# Foresight page: API trace

Investigated 2026-11-12. Read only: no code, data, schema or configuration was changed.

**Runtime evidence.** The real `GET /context` handler was called in-process against the configured PostgreSQL, in a read-only transaction, and against Databricks `information_schema`. The schema-RAG index refresh was disabled so nothing on disk changed. No HTTP request was sent to the running server, because that would need a user's token.

**Note.** The working tree contains uncommitted Phase 2 and 3 banking-context changes. One of them alters the banking glossary served by `/context`, and it is marked below.

## 1. Frontend architecture

| Item | Where |
|---|---|
| Route | `/context`, labelled "Foresight" in the navigation (`frontend/components/Shell.tsx:11`) |
| Page component | `ContextIntelligencePage` in `frontend/app/context/page.tsx`. Its heading is "Insight Intelligence" |
| Imported components | `Shell` (layout, navigation, account menu); `ContextArchitecture` and the `BusinessContext` type (`frontend/components/ContextArchitecture.tsx`) |
| State | `context` (the whole API response), `section` (which tab is active), `search`, `selectedTable`, `inspectingCard`, `domain` (`page.tsx:34-41`) |

The page has **five** tabs, not three (`page.tsx:199-205`):

- `overview` → "Overview"
- `entities` → "Entities & relationships"
- `glossary` → "Business glossary"
- `governance` → "Metrics & rules"
- `access` → "User & sources"

There is no separate "Relationship" tab. Relationships appear in three places:

- the "Entities & relationships" tab, inside the selected table's panel
- the "Verified relationships" overview card
- layer 3 of the architecture diagram

## 2. API calls

### When the page opens

| Request | Caller | Purpose |
|---|---|---|
| `GET /context` | `loadContext()`, `page.tsx:43-56`, run once by `useEffect` at `page.tsx:58-60` | All Foresight content |
| `GET /me` | `Shell.tsx:48` | Account menu |
| `GET /v1/subscription/me` | `PlanMenuLink`, `lib/subscription.ts:77` | Plan shown in the account menu |

The dashboard also calls `GET /context` (`app/dashboard/page.tsx:151`).

### Other triggers

| Action | Request |
|---|---|
| Switching any tab | None. `setSection(id)` only changes state (`page.tsx:206`) |
| Clicking an overview card | None. It changes `section` and opens a dialog (`page.tsx:165-168`) |
| Changing the domain | `GET /context?domain=<id>` (`page.tsx:126-129`) |
| "Refresh metadata" | `GET /context?domain=<current>` (`page.tsx:90`) |

### API client

`frontend/lib/api.ts`:

- **Base URL:** `NEXT_PUBLIC_API_URL`, otherwise `http://localhost:8013`.
- **Method:** GET, the default.
- **Headers:** `Content-Type: application/json`, plus `Authorization: Bearer <ryze_token from localStorage>` when present.
- **Credentials:** `credentials: "include"`, so the `ryze_session` cookie is also sent.
- **Errors:** a 401 clears storage and goes to `/login`. A 403 with "no longer has access" does the same. Anything else throws `ApiError`, which the page shows as a red alert with Retry (`page.tsx:146-152`).

**One fetch, reused everywhere.** One response object (`context`) is fetched once and reused by every tab, every card, every dialog and the diagram.

## 3. Frontend to backend mapping

| UI section | Frontend code | API | Backend function | Response field |
|---|---|---|---|---|
| Overview cards | `OVERVIEW_CARDS`, `page.tsx:24-31` | `GET /context` | `user_context` | `tables`, `relationships`, `glossary`, `metrics`, `rules` (counts) |
| Overview tab | `page.tsx:212-251` | same | same | `tables`, `relationships` |
| Entities & relationships tab | **the same block**, `page.tsx:212-251` | same | same | `tables`, `relationships` |
| Business glossary | `page.tsx:253-269` | same | same | `glossary` |
| Metrics & rules | `page.tsx:271-282` | same | same | `metrics`, `rules` |
| User & sources | `page.tsx:284-305` | same | same | `user`, `source` |
| Architecture diagram | `ContextArchitecture.tsx:73-98` | same | same | `tables`, `glossary`, `relationships`, `user`, `source` |

## 4. Backend execution chain

```
GET /context?domain=<optional>
  main.py:1183  user_context(domain, db=Depends(get_db), user=Depends(get_current_user))
    auth.py:296  get_current_user
      decodes the JWT (Bearer header or ryze_session cookie) with JWT_SECRET (auth.py:303)
      re-reads app_users and checks domain access in PostgreSQL (auth.py:314)
      healthcare: requires a tenant assignment (auth.py:316-318)
    main.py:1189  domains_for(db, user)  (services/access.py:81)
      admin: every domain; otherwise app_users + user_domain_grants in PostgreSQL
    main.py:1191  403 if the requested domain is not permitted
    main.py:1193-1203  healthcare: requires a user_domain_tenants row
    main.py:1205  graph = orchestrator.graph  (a ContextGraphService held in memory)
    main.py:1206-1211  glossary:
      banking, finance (Databricks mode): Python code (banking_context.py; domains.finance_gold_glossary)
      other domains: SELECT from the PostgreSQL glossary table
    main.py:1212-1225  tables = graph.domain_tables[domain]; sensitive columns removed (is_sensitive_column)
    main.py:1227-1235  relationships = graph edges between visible tables
    main.py:1238-1252  glossary mapping_status: "mapped" if the column is in a visible table
    main.py:1254-1270  source: settings, warehouse_status(), databricks_domain_catalog()
    main.py:1272-1293  JSON; metrics=[], rules=[], synchronized_at=None (fixed values)
```

**The graph is not built per request.** It is built by `Orchestrator.warmup` → `ContextGraphService.rebuild` (`orchestrator.py:957-959`, `context_graph.py:37-75`). That happens:

- at API startup (`main.py:180`)
- on the first Ask AI question if the startup build failed
- from `/ready` after an outage (`main.py:342-369`)

`rebuild` in Databricks mode does the following:

1. Inspects PostgreSQL tables and their foreign keys (`context_graph.py:41-52`).
2. Reads the PostgreSQL `glossary` table (`:54-57`) and swaps in the code glossaries for banking and finance (`:58-64`).
3. If Databricks is configured, **clears the graph** (`:67`).
4. Reads each domain's Databricks `information_schema.columns` (`_load_domain_schemas`, `:179-188`, then `warehouse.warehouse_schema_columns`).
5. **Adds the tables as nodes only** (`:71`).
6. Rebuilds the schema RAG index (`:72`).

**What a `/context` request does not run:**

- No Databricks query; it reads the cached graph.
- No business data.
- No language model, LangGraph or RAG retrieval. RAG is built during warm-up for Ask AI and is not used by `/context`.

The PostgreSQL work is the authorization reads and, for non-banking and non-finance domains, one glossary `SELECT`.

## 5. Where each displayed field comes from

| Field | Source | Kind |
|---|---|---|
| `user.username`, `role`, `domain` | JWT, re-checked against PostgreSQL `app_users` | Application data |
| `user.permitted_domains` | PostgreSQL `app_users` and `user_domain_grants` | Application data |
| `user.domain_label` | `DOMAIN_LABELS`, Python constant (`domains.py`) | Hard-coded |
| `user.organization` | always `None` (`main.py:1278`) | Hard-coded |
| `source.warehouse`, `schema` | `.env` settings | Configuration |
| `source.catalog` | `DATABRICKS_DOMAIN_CATALOGS` mapping | Configuration |
| `source.configured` | `warehouse_status()`: settings present, no connection test (`warehouse.py:31-45`) | Configuration |
| `source.metadata_available` | whether any table survived filtering | Derived |
| `tables[].name`, `columns` | Databricks Unity Catalog `information_schema.columns`, read at warm-up, sensitive columns removed | **Schema metadata**, not business records |
| `relationships` | edges of the in-memory graph; **always empty in Databricks mode** | Would be configured from foreign keys; none exist |
| `glossary`, banking | `banking_context.py` (uncommitted Phase 2 change; before it, the PostgreSQL rows) | Python definitions |
| `glossary`, finance | `domains.finance_gold_glossary()` | Python definitions |
| `glossary`, other domains | PostgreSQL `glossary` table. These rows describe the local demo tables, so they show "source mapping unavailable" against gold | Application table |
| `metrics`, `rules` | always `[]` (`main.py:1290-1291`) | Hard-coded empty |
| `synchronized_at` | always `None` (`main.py:1292`) | Hard-coded |
| "Context coverage" card | the text "Not measured" (`page.tsx:30`) | Hard-coded in the frontend |
| Architecture diagram labels | `LEVELS`; and `SAMPLE_CONCEPTS` only when no context is passed, as on the home page (`ContextArchitecture.tsx:39-54`) | Hard-coded in the frontend |

**Observed result** for an administrator, from the read-only run on 2026-11-12:

| Domain | Tables | Relationships | Glossary | Metrics | Rules |
|---|---|---|---|---|---|
| Banking | 44 | **0** | 30, all mapped | `[]` | `[]` |
| Retail | 47 | **0** | 5, all "source mapping unavailable" | `[]` | `[]` |

The graph held 171 nodes and **0 edges** across five domains.

## 6. Why the sections look the same

The root cause has three parts, all in the code.

1. **Overview and "Entities & relationships" render the same block.** One condition draws both: `{(section === "overview" || section === "entities") && (...)}` at `page.tsx:212`. The tab buttons change only `section` and the highlighted button (`page.tsx:206`); the content does not change. There is no Overview-specific content in that area.
2. **The relationships list is always empty in Databricks mode.**
   - `ContextGraphService.rebuild` clears the graph and adds Databricks tables as nodes without edges (`context_graph.py:67-71`).
   - `refresh_warehouse_schema` does the same (`:166-168`).
   - Unity Catalog declares no foreign keys in these catalogs anyway.
   - So the loop at `main.py:1227` finds nothing.

   Every relationship view therefore shows its empty state, and the relationship part of "Entities & relationships" adds nothing.
3. **Both relationship-related overview cards open the same tab.** "Source tables" and "Verified relationships" both set `section: "entities"` (`page.tsx:25-26`). Only the pop-up dialog differs.

Nothing is mocked on this page. The `SAMPLE_CONCEPTS` sample data is used only by the home page (`app/page.tsx:431`, `sampleMode`).

## 7. Missing or incomplete functionality

- **No relationships for any Databricks domain.** Banking's 7 verified joins exist in `banking_context.RELATIONSHIPS` (Phase 2) but are not served.
- **No metrics or rules.** `metrics` and `rules` are fixed empty lists, although the banking registry has 19 metrics and the banking context has 19 rules.
- **No filtering of tables.** All 44 banking tables are listed, including pipeline metadata and `snapshot_`/`summary_` copies that the registry excludes.
- **Stale glossary for three domains.** Insurance, retail and healthcare glossaries describe local demo tables.
- **"Refresh metadata" does not refresh.** It re-fetches the cached response; the graph is rebuilt only at startup or after a warm-up failure.
- **No sync time.** `synchronized_at` is never recorded.
- **Unscoped "Verified" wording.** "Database foreign-key metadata" is the only relationship evidence type, and the "Verified relationships" label is not backed by a check in Databricks mode.

## 8. Recommended separation, not implemented

- **Overview:** summary counts, source, status, last sync and approval statuses. No table list.
- **Entities:** approved tables only (registry tables for banking), each with columns, meaning, key, nullability and sensitivity from the data dictionary.
- **Relationships:** a join list or diagram from a declared relationship registry. Each relationship has an evidence type (declared foreign key, or verified against the data with its unmatched count), and "inferred" relationships are never shown as "verified".
- **API:** either keep one `GET /context` and add `capabilities`, `dictionary`, `relationships` and `metrics`/`rules` objects for banking, or split it into `/context/overview`, `/context/entities` and `/context/relationships`. Keeping one call avoids extra requests; splitting allows lazy loading. Either way, existing fields stay so the dashboard keeps working.
- **Frontend:** give each tab its own render branch, and make "Refresh metadata" a real rebuild (administrators only) or rename it.

This is the work planned as Phase 4, waiting for your approval.
