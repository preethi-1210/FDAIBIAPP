# Data Governance and AI Security Assessment

**Assessment date:** 2026-10-05  
**Scope:** FD AIBI FastAPI/Next.js application source, existing backend unit tests, running local demo-mode service, and a limited set of safe policy probes.  
**Overall disposition:** **Not ready for production use with live regulated or sensitive records.** Resolve the Critical and High findings, then run the test catalog against a synthetic, isolated warehouse before enabling live data.

## Executive summary

The application has useful baseline safeguards: roles are recomputed from the current account record, common CVV/CVC requests and recognized CVV column names are rejected, known card-number result columns are masked to the last four digits, analyst history is user/domain scoped, and successful queries retain a result/audit record. These controls are useful but rely heavily on a short list of English phrases and SQL/result column names.

There is no comprehensive data classification inventory for Banking, Insurance, Finance, and Retail data. On a 31-prompt representative domain probe, the request detector recognized **3/31** categories; 28 were not classified. A separate safe code-level probe showed that analyst SQL checks accept columns such as `aadhaar_number`, `employee_salary`, and `diagnosis`, and accept a query with no domain predicate. This demonstrates gaps in the policy gate, not extraction of actual records. The running service reports **local/demo mode**, so this assessment did not validate a live Databricks workspace or real customer data.

The recent chat UI warning is client-side and local to the browser; it is not an authorization boundary or an audit event. Direct API requests still follow backend rules. Treat the UI as explanatory only.

## Security scorecard

Provisional control-maturity score, **0–5 per area**; this is not a compliance certification or a statistical efficacy score.

| Control area | Score | Evidence-based assessment |
|---|---:|---|
| Sensitive-data detection | 1/5 | A few regex patterns; 3 of 31 representative domain prompts matched in the probe. |
| PII recognition | 1/5 | Some account, policy, SSN/tax/passport names are recognized; Aadhaar, contact details, medical records, and many synonyms are not covered. |
| Confidential business-data detection | 0/5 | No general classification/detection policy for payroll, budgets, fraud cases, statements, or transaction details. |
| Data masking | 2/5 | Known card-number keys mask to last four; recognized CVV keys are dropped. Admin account/policy values are returned without masking; other PII has no systematic mask. |
| RBAC | 3/5 | Backend derives admin from the current database account; access is otherwise a broad admin/analyst split rather than per-field, purpose-bound authorization. |
| Row-level security | 1/5 | Built-in SQL templates and special sensitive queries include domain predicates, but arbitrary LLM-generated SQL is not structurally required to do so. |
| Column-level security | 1/5 | Regex checks catch selected names; there is no default-deny column allowlist or warehouse-enforced masking policy. |
| Prompt-injection resistance | 1/5 | Read-only SQL checks reduce write risk; user instructions are still sent to the SQL-generation model, and generated SQL is not tenant-policy validated. |
| Data-leakage prevention | 2/5 | History redacts recognized result columns and scopes rows to username/domain; unrecognized aliases/fields and stored SQL/answer paths remain risks. |
| AI governance and audit | 2/5 | Successful queries retain audit metadata and some cases enter HITL; blocked requests are not logged here, audit IDs can collide, and HITL actions are not role-gated. |
| **Total** | **14/50 (28%)** | **Material gaps remain; no production readiness claim.** |

## Audit findings

### F-01 — Critical: tenant row scope is not enforced on generated SQL

**Evidence:** The model receives an instruction to filter `domain`, but that is a natural-language instruction in [sql_generator.py](backend/app/services/sql_generator.py#L24). [validate_sql](backend/app/services/guardrail.py#L20) checks statement shape and a few keywords, not tenant predicates. [orchestrator.py](backend/app/services/orchestrator.py#L82) calls sensitive-column checks but has no mandatory domain-scope validator for ordinary generated queries.

**Impact:** If an LLM returns a syntactically valid `SELECT` without the authenticated user's domain constraint, rows from other business domains/tenants may be queried. The code probe confirmed that SQL with no `domain` predicate passes the current SQL checks. Built-in fallback templates do filter by domain; they do not close the arbitrary generated-SQL path.

**Remediation:** Enforce row-level policies in the warehouse/database using separate tenant views or row filters and a least-privilege service principal. Additionally parse/validate every query with a SQL AST and reject queries that do not constrain every tenant-scoped table to the authenticated domain. Add adversarial cross-domain integration tests.

### F-02 — High: sensitive-data coverage is incomplete and not default-deny

**Evidence:** [sensitive_data.py](backend/app/sensitive_data.py#L6) uses finite request and column-name regexes; [sanitize_sensitive_rows](backend/app/sensitive_data.py#L99) only masks/drops recognized result keys. The 31-prompt probe classified 3/31 sample domain categories. `aadhaar_number`, `employee_salary`, and `diagnosis` SQL probes passed analyst SQL checks.

**Impact:** PII/SPII, health data, employee compensation, KYC documents, fraud cases, and confidential business records can pass through when their names/prompts are not in the regex list. Column aliases and schema naming variation make name-only detection brittle.

**Remediation:** Establish a reviewed data inventory with sensitivity labels and owner/purpose rules. Use schema tags and a default-deny projection allowlist as the primary control; use DLP/classifiers as a secondary detection layer. Add exact and alias-based test coverage for all domain categories.

### F-03 — High: column protection is not consistently applied at source, query, and output

**Evidence:** [require_sql_sensitive_access](backend/app/sensitive_data.py#L81) and result sanitation use regex names rather than a policy registry. [history](backend/app/main.py#L127) returns the saved SQL alongside the result, and [replay_history](backend/app/main.py#L160) sanitizes recognized result columns but returns the remaining response. Admin-sensitive SQL intentionally selects full account/policy identifier fields in [sql_generator.py](backend/app/services/sql_generator.py#L153).

**Impact:** Admin access to full account/policy identifiers is broad and not purpose-bound; analyst protection depends on names being recognized. A forgotten field, alternate alias, or PII embedded in a generated answer can bypass output masking. Raw SQL also reveals schema and analytical logic.

**Remediation:** Enforce column controls in the warehouse and in a centralized server-side response serializer. Mask account, policy, PAN, Aadhaar, and contact identifiers consistently for every role unless an approved, logged purpose justifies a minimum necessary exception. Redact sensitive values from prose, history, chart labels, exports, and SQL disclosures.

### F-04 — High: CVV/CVC denial is pattern-based rather than a guaranteed schema-level prohibition

**Evidence:** [sensitive_data.py](backend/app/sensitive_data.py#L23) blocks known CVV/CVC/security-code names and [sanitize_sensitive_rows](backend/app/sensitive_data.py#L99) removes recognized matching keys. There is no schema-wide deny policy for all possible aliases or encodings.

**Impact:** A source field with an unrecognized name (for example, a verification-value variant) or a transformed output alias may not match the regex. CVV/CVC must never be stored after authorization and must not be returned to any role.

**Remediation:** Prohibit storage of CVV/CVC at ingestion and warehouse policy level; exclude payment-security columns from the service principal's accessible views. Fail closed if any classified CVV-like field is detected. Test aliases, casing, nested values, serialized results, history, logs, and exports.

### F-05 — High: admin authorization is broad; the UI and API have different behavior

**Evidence:** [auth.py](backend/app/auth.py#L70) correctly recomputes admin from the current user row, but the policy gives that role a general exception for recognized identifiers in [sensitive_data.py](backend/app/sensitive_data.py#L67). The frontend intercepts recognized prompts locally in [page.tsx](frontend/app/chat/page.tsx#L264), while the backend `/query` endpoint remains independently callable.

**Impact:** A browser warning is not step-up authentication, approval, or an audit control. Direct API use can request identifiers as admin without the UI warning; the UI, conversely, blocks even authorized admins from running prompts it recognizes. This is both a governance gap and inconsistent product behavior.

**Remediation:** Make backend authorization authoritative and consistent. Define separate entitlements for account, policy, and card fields; require purpose and, where risk warrants, step-up authentication or approval. Log the decision and return only masked/minimum-necessary results.

### F-06 — Medium: HITL decisions are not restricted to reviewers

**Evidence:** [hitl_queue](backend/app/main.py#L195) returns pending items to any authenticated user in the domain. [hitl_action](backend/app/main.py#L218) checks domain and action but not reviewer/admin permission.

**Impact:** Any authenticated domain user can approve/reject another user's review item and see their question/username. This weakens separation of duties and may expose confidential prompt content.

**Remediation:** Add a distinct reviewer permission, limit queue visibility to assigned reviewers, log decision identity/time/reason, and test analyst denial plus reviewer approval/rejection.

### F-07 — Medium: blocked sensitive attempts and audit identifiers are not reliable audit evidence

**Evidence:** The frontend sensitive notice is created only in local React state in [page.tsx](frontend/app/chat/page.tsx#L264); the backend never receives that attempt. Persisted query history is created only after a query runs in [orchestrator.py](backend/app/services/orchestrator.py#L182). The audit identifier is based on the current second modulo 100000 at [orchestrator.py](backend/app/services/orchestrator.py#L83).

**Impact:** Blocked attempts are absent from server audit records; identifiers can collide; local UI history is lost on refresh. This is insufficient for incident investigation or an immutable regulated audit trail.

**Remediation:** Record every allow/deny decision server-side with a UUID/monotonic identifier, authenticated subject, domain, purpose, policy/version, fields requested, result count, masking outcome, and reviewer approval. Send append-only audit events to a protected central log with retention and access monitoring.

### F-08 — Medium: SQL guardrails are lexical and do not enforce source/tenant policy

**Evidence:** [validate_sql](backend/app/services/guardrail.py#L20) uses regex/string checks for read-only SQL, semicolons, keywords, and the presence of the word `limit`. It does not parse the SQL AST, enforce allowed tables/columns, enforce a domain predicate, or distinguish a real `LIMIT` clause from that word appearing elsewhere.

**Impact:** Query intent, join scope, table access, expensive scans, and policy semantics are not proven by these checks. The 200-row fetch cap limits returned rows, but does not establish safe warehouse-level access or bounded query cost.

**Remediation:** Use a parser/AST and a strict SELECT allowlist, parameterized domain predicates, query time/scan budgets, and warehouse permissions. Reject unknown sources, sensitive expressions, unbounded scans, and multi-tenant joins that omit domain equality on every relevant table.

## PII and confidential-data coverage report

The baseline regex probe exercised one representative phrase for each of the 31 requested domain categories. It matched **account numbers**, **credit-card details**, and **employee bank-account details**; the remaining **28/31 were not recognized as sensitive request types**. This is a focused coverage probe, not a statistically representative recall measurement.

| Data type | Current code-level protection | Assessment |
|---|---|---|
| Account numbers | Recognized aliases are admin-only; analyst output can be redacted. Admin sees full values. | Partial; not masked for admin; naming dependent. |
| Policy/member identifiers | Some policy/member field names are admin-only. | Partial; no purpose or minimum-necessary mask. |
| Card PAN | Known field names are recognized; card-number result keys of known names are masked to last four. | Partial; aliases/naming variants and storage controls not proven. |
| CVV/CVC | Common prompt/column names are rejected or removed. | Partial; no schema/warehouse-wide prohibition demonstrated. |
| Indian PAN/tax ID | `pan`/`tax_id` forms have limited SQL/name handling; “PAN number” prompt is not classified by request detector. | Incomplete; no prescribed `ABCXX1234X` mask. |
| Aadhaar | No Aadhaar-specific prompt detector or mask identified. | Not covered; no `XXXX-XXXX-1234` mask. |
| SSN/passport | Some names are in restricted-name patterns. | Partial; no tokenization/format-aware masking. |
| Email, phone, address, DOB | No comprehensive recognition or mask policy identified. | Not covered; exposed if selected by generated SQL. |
| Medical/diagnosis/health reports | No dedicated classification or output suppression identified. | Not covered; highly restricted data risk. |
| Salary/payroll/tax/employee banking | No business-data classification or role-specific entitlement identified. | Not covered. |
| IFSC, transaction and fraud data | No general classification or purpose-based control identified. | Not covered or incomplete depending on source schema. |
| Loyalty and customer analytics | No individual-level profiling or inference governance identified. | Not covered. |

## Compliance checklist

This is a technical control-gap checklist, **not a legal opinion or certification**. Applicability depends on the business, jurisdiction, data, and processing role.

| Framework / obligation area | Status | Evidence / gap |
|---|---|---|
| PCI DSS payment account data | **Partial / blocker** | Known card number keys are masked; no evidence of source-level tokenization, least-privilege views, full PAN display controls, or a schema-wide CVV ban. Never store CVV/CVC after authorization. |
| GLBA / financial privacy safeguards | **Gap** | No comprehensive customer financial-data inventory, purpose limitation, or enforceable tenant/field policy demonstrated. |
| India DPDP / Aadhaar obligations (if applicable) | **Gap** | No Aadhaar-specific minimization, masking, consent/purpose, retention, or access-report workflow demonstrated. |
| GDPR / UK GDPR (if applicable) | **Gap** | No demonstrated data-subject minimization, retention/deletion process, lawful-purpose tagging, or comprehensive access logging. |
| HIPAA (only if the service handles regulated PHI) | **Not evidenced** | No PHI classification, minimum-necessary enforcement, audit/BAA controls, or protected health output policy demonstrated. |
| SOX / financial reporting controls (if applicable) | **Partial / gap** | Some query history/HITL records exist, but immutable audit, reviewer separation, and role-gated decisions are not demonstrated. |
| NIST AI RMF / AI governance | **Partial / gap** | Model status and some model-governance response metadata exist; no prompt-injection test program, data-use register, model risk approval, or release evidence is shown. |
| OWASP LLM application risks | **Gap** | Prompt injection and sensitive data exposure are not addressed with a layered, source-enforced policy. |

## Governance validation report

**Positive evidence**

- Admin role is derived from the current database account, not merely trusted from a client-supplied role claim.
- History reads are scoped to username and domain; history replay rechecks the saved prompt's sensitive category.
- Query responses record username, domain, question, SQL, result JSON, audit ID, tables, row count, and guardrail status for successful runs.
- Common CVV/CVC requests are rejected by the backend; recognized card-number output names are masked.
- SQL mutation keywords, multi-statement SQL, and non-SELECT statements are rejected by the lexical guardrail.

**Not validated or failing**

- No live Databricks source was connected during this assessment; actual schema, grants, row filters, and source classifications remain unverified.
- The existing backend suite passed **22/22 tests**, but it does not cover the requested 31 domain cases or 100 adversarial prompts.
- A safe policy-gate probe confirmed that analyst SQL referencing `aadhaar_number`, `employee_salary`, and `diagnosis`, and a query without a domain predicate, pass current checks. No records were read by these probes.
- The chat's red in-conversation warning was verified in the browser; this is UI behavior only and was not treated as backend enforcement.
- No enforceable tenant predicate exists for arbitrary model-generated SQL.
- No classifier/policy exists for employee, medical, KYC, fraud, address/contact, or most transaction data.
- The chat's local warning is not an API control and creates no server-side security event.
- Reviewer authorization and immutable audit properties are not demonstrated.

## Recommended security controls

1. **Block production rollout on F-01 through F-05.** Connect only a synthetic, isolated warehouse until the tenant and column policies are enforced in the data plane.
2. Create a data inventory for every domain/table/column with owner, classification, purpose, retention, allowed roles, masking rule, and export rule.
3. Use least-privilege service principals and curated views. Enforce row filters by tenant/domain and column masks in Databricks/SQL, not only in the LLM prompt or API regex.
4. Make query generation default-deny: parse SQL into an AST; allow only approved tables/columns/functions; inject and verify the authenticated domain scope for each tenant-scoped table; bound cost and rows.
5. Centralize masking/redaction for table rows, narrative answers, chart data, SQL, exports, history, logs, and error paths. Prefer tokenization; expose last-four-only for payment PAN/account identifiers unless separately approved.
6. Make CVV/CVC unqueryable at ingestion, schema, view, SQL, API, and storage layers. Alert and fail closed on unknown payment-security-code-like fields.
7. Replace keyword-only detection with an explicit policy catalog plus DLP/classification as defense in depth. Add normalization for abbreviations, multilingual prompts, Unicode confusables, encoded terms, aliases, and nested outputs.
8. Keep UI messaging consistent with API policy. Client checks may improve UX, but every allow/deny decision must be made and audited on the server.
9. Separate analyst, domain admin, sensitive-field approver, HITL reviewer, and platform admin permissions. Gate HITL actions; add just-in-time purpose/approval and step-up for especially sensitive access.
10. Write append-only audit events for allowed, denied, masked, exported, replayed, and approved requests. Use collision-resistant IDs; protect logs from the application service account; set retention and alerting.
11. Run the accompanying test catalog in CI against a synthetic multi-domain warehouse and adversarially configured fake LLM. Require zero cross-domain rows, zero unmasked forbidden fields, and full audit evidence before release.

## Risk assessment summary

**Current risk: High, with Critical tenant-isolation exposure if this service is connected to shared live multi-domain data.** The strongest current controls are narrow and name-dependent. Do not use this assessment to assert compliance or to authorize processing PAN, Aadhaar, PHI, KYC documents, employee payroll, or CVV/CVC. Begin with the Critical row-scope fix, then default-deny column policy and data-plane masking.

## Assessment limitations

- Inspected the local source tree and current unit tests; did not conduct a penetration test, review cloud IAM/network controls, inspect production logs, or validate legal applicability.
- The running API reported `warehouse.mode=local` and `demo_mode=true`; no real source data was queried or included in this report.
- Probe results describe representative phrase detection and static policy-gate behavior. They do not prove that a specific live LLM will generate a particular unsafe query.
- The catalog's expected responses are **acceptance criteria**, not assertions that the current application passes them.
