"use client";

import { useCallback, useEffect, useState } from "react";
import { AlertCircle, Check, Database, ExternalLink, LoaderCircle, Play, RefreshCw, ShieldCheck, Sparkles } from "lucide-react";
import { Shell } from "@/components/Shell";
import { api } from "@/lib/api";

type Me = {
  username: string;
  domain: string;
  role: string;
  data_mode: "live" | "sample";
  demo_mode: boolean;
  warehouse: {
    mode: string;
    ready: boolean;
    connected: boolean;
    detail: string;
    warehouse_id?: string | null;
    genie: boolean;
  };
  llm: { configured: string; openai: boolean; grok: boolean; anthropic: boolean; anthropic_model: string; ollama: string; ollama_model: string; active: string | null };
  tabpfn: { installed: boolean; version: string | null; runtime: string };
};

type Diagnostic = {
  status: string;
  model: string;
  data: string;
  rows_scored: number;
  accuracy: number;
  confidence_range: number[];
  sample: { predicted_label: string; confidence: number }[];
};

type WarehouseDiagnostic = {
  mode: string;
  ready: boolean;
  connected: boolean;
  detail: string;
};

export default function SettingsPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [diagnostic, setDiagnostic] = useState<Diagnostic | null>(null);
  const [diagnosticLoading, setDiagnosticLoading] = useState(false);
  const [warehouseDiagnostic, setWarehouseDiagnostic] = useState<WarehouseDiagnostic | null>(null);
  const [warehouseLoading, setWarehouseLoading] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setMe(await api<Me>("/me"));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not check backend configuration.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function runTabPFNTest() {
    setDiagnosticLoading(true);
    setDiagnostic(null);
    setError("");
    try {
      setDiagnostic(await api<Diagnostic>("/tabpfn/diagnostics", { method: "POST" }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The TabPFN diagnostic failed.");
    } finally {
      setDiagnosticLoading(false);
    }
  }

  async function checkWarehouse() {
    setWarehouseLoading(true);
    setWarehouseDiagnostic(null);
    setError("");
    try {
      setWarehouseDiagnostic(await api<WarehouseDiagnostic>("/warehouse/check"));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The warehouse connection check failed.");
    } finally {
      setWarehouseLoading(false);
    }
  }

  return (
    <Shell>
      <div className="connect-page workspace-dark">
        <div className="connect-heading">
          <div>
            <span className="connect-eyebrow"><ShieldCheck size={14} /> PRODUCTION READINESS</span>
            <h1>Connect your data and validate AI</h1>
            <p>Connect a Databricks or Snowflake warehouse for live business data, and run a real TabPFN diagnostic before your demo.</p>
          </div>
          <button type="button" className="connect-refresh" disabled={loading} onClick={() => void refresh()}>
            {loading ? <LoaderCircle className="chat-spin" size={16} /> : <RefreshCw size={16} />}
            Refresh configuration
          </button>
        </div>

        {error ? <div className="connect-alert" role="alert"><AlertCircle size={17} /> {error}</div> : null}

        {loading && !me ? (
          <div className="connect-loading"><LoaderCircle className="chat-spin" size={25} /><span>Checking configured services…</span></div>
        ) : null}

        {me ? (
          <>
            <div className="connect-status-grid">
              <StatusCard
                icon={<Database size={19} />}
                title="Business data"
                status={["databricks", "snowflake"].includes(me.warehouse.mode)
                  ? warehouseDiagnostic
                    ? warehouseDiagnostic.connected ? `Connected · ${warehouseDiagnostic.mode}` : `${warehouseDiagnostic.mode} check failed`
                    : `${me.warehouse.mode} configured · not checked`
                  : me.demo_mode ? "Demo mode · seeded sample data" : "Local database · configured records"}
                tone={warehouseDiagnostic
                  ? warehouseDiagnostic.connected ? "success" : "error"
                  : ["databricks", "snowflake"].includes(me.warehouse.mode) ? "demo" : "demo"}
                detail={warehouseDiagnostic?.detail || me.warehouse.detail}
              />
              <StatusCard
                icon={<Sparkles size={19} />}
                title="TabPFN"
                status={me.tabpfn.installed ? `Installed · v${me.tabpfn.version}` : "Not installed in the backend environment"}
                tone={me.tabpfn.installed ? "success" : "error"}
                detail="Predictions run in this backend. No hosted prediction API or Genie is used."
              />
            </div>

            {["databricks", "snowflake"].includes(me.warehouse.mode) ? (
              <div className="connect-live-check">
                <div>
                  <strong>Verify your live data connection</strong>
                  <span>Runs a real connection and required-table check for your signed-in domain. It is not part of page loading.</span>
                </div>
                <button type="button" className="connect-run-button" disabled={warehouseLoading} onClick={() => void checkWarehouse()}>
                  {warehouseLoading ? <LoaderCircle className="chat-spin" size={16} /> : <Play size={15} />}
                  {warehouseLoading ? "Checking source…" : "Run connection check"}
                </button>
              </div>
            ) : null}

            {!["databricks", "snowflake"].includes(me.warehouse.mode) && me.demo_mode ? (
              <div className="connect-notice connect-notice--demo">
                <strong>Sample data mode is active.</strong>
                <span>The dashboard and chat use the local seeded data set. To switch to live data, configure the backend environment below and restart the API. The app does not silently mix sample and production results.</span>
              </div>
            ) : warehouseDiagnostic && !warehouseDiagnostic.connected ? (
              <div className="connect-notice connect-notice--error">
                <strong>Live data is selected, but its connection check failed.</strong>
                <span>{warehouseDiagnostic.detail} Fix the warehouse settings before presenting live results. Use <code>WAREHOUSE=local</code> to intentionally switch back to sample mode.</span>
              </div>
            ) : null}

            {me.warehouse.mode === "snowflake" ? (
              <section className="connect-card">
                <div className="connect-card__title">
                  <div><span className="connect-card__icon"><Database size={17} /></span><h2>Snowflake warehouse</h2></div>
                  {warehouseDiagnostic?.connected ? <span className="connect-pill connect-pill--success"><Check size={13} /> Ready</span> : null}
                </div>
                <p>Configure the Snowflake connection in the backend environment. The password stays on the backend and is never returned to the browser.</p>
                <pre className="connect-env">{`WAREHOUSE=snowflake
SNOWFLAKE_ACCOUNT=<account-identifier>
SNOWFLAKE_USER=<service-user>
SNOWFLAKE_PASSWORD=<backend-only-secret>
SNOWFLAKE_ROLE=<least-privilege-role>
SNOWFLAKE_WAREHOUSE=<warehouse>
SNOWFLAKE_DATABASE=<database>
SNOWFLAKE_SCHEMA=PUBLIC
LIVE_SIMULATE=false`}</pre>
                <div className="connect-inline-note">
                  <AlertCircle size={15} />
                  <span>Grant the service user only warehouse usage and SELECT access to the required application tables. Do not reuse a personal password for production.</span>
                </div>
              </section>
            ) : null}

            <div className="connect-main-grid">
              {me.warehouse.mode !== "snowflake" ? (
              <section className="connect-card">
                <div className="connect-card__title">
                  <div><span className="connect-card__icon"><Database size={17} /></span><h2>Databricks SQL Warehouse</h2></div>
                  {warehouseDiagnostic?.connected ? <span className="connect-pill connect-pill--success"><Check size={13} /> Ready</span> : null}
                </div>
                <p>Configure a SQL Warehouse connection in <code>backend/.env</code>. The access token stays on the backend and is never returned to the browser.</p>
                <ol className="connect-steps">
                  <li><b>Create or choose a Databricks SQL Warehouse.</b> Start it and copy the server hostname and HTTP path from its connection details.</li>
                  <li><b>Map each authorized domain to a catalog and schema.</b> The app checks the signed-in domain&apos;s catalog and validates its real tables before querying.</li>
                  <li><b>Grant read access.</b> Grant the backend identity use of the catalog/schema and SELECT on the required tables. Use a dedicated, least-privilege identity.</li>
                  <li><b>Set the backend environment and restart it.</b> Never put a Databricks token in frontend variables or commit it to Git.</li>
                  <li><b>Run the live source verification.</b> Benchmarks also runs a read-only aggregate against a representative domain table.</li>
                </ol>
                <pre className="connect-env">{`WAREHOUSE=databricks
DATABRICKS_SERVER_HOSTNAME=adb-<workspace-id>.<cloud>.databricks.com
DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/<warehouse-id>
# Or set DATABRICKS_WAREHOUSE_ID=<warehouse-id> instead
DATABRICKS_TOKEN=<backend-only-secret>
DATABRICKS_CATALOG=<catalog>
DATABRICKS_SCHEMA=<schema>
LIVE_SIMULATE=false`}</pre>
                <div className="connect-inline-note">
                  <AlertCircle size={15} />
                  <span>For local development, set the token in the ignored <code>backend/.env</code>. For production, use your deployment platform&apos;s secret manager, a dedicated service identity, token rotation, TLS, and a restricted network. This connector currently authenticates with a Databricks access token.</span>
                </div>
                <a className="connect-doc-link" href="https://docs.databricks.com/en/dev-tools/sql-execution-tutorial.html" target="_blank" rel="noreferrer">
                  Databricks SQL connector guide <ExternalLink size={14} />
                </a>
              </section>
              ) : null}

              <section className="connect-card connect-tabpfn-card">
                <div className="connect-card__title">
                  <div><span className="connect-card__icon connect-card__icon--violet"><Sparkles size={17} /></span><h2>Prove TabPFN is working</h2></div>
                  {me.tabpfn.installed ? <span className="connect-pill connect-pill--success"><Check size={13} /> Installed</span> : null}
                </div>
                <p>This runs the actual local TabPFN classifier with cross-validation on generated, labeled test rows. It does not query or expose customer records.</p>
                <button className="connect-run-button" type="button" disabled={!me.tabpfn.installed || diagnosticLoading} onClick={() => void runTabPFNTest()}>
                  {diagnosticLoading ? <LoaderCircle className="chat-spin" size={16} /> : <Play size={15} />}
                  {diagnosticLoading ? "Running TabPFN…" : "Run TabPFN diagnostic"}
                </button>
                {diagnosticLoading ? (
                  <div className="connect-test-loading" role="status">
                    <span className="connect-test-spinner"><LoaderCircle className="chat-spin" size={21} /></span>
                    <span>Loading the model and scoring synthetic rows…</span>
                    <small>The first run may take several minutes while model files are downloaded.</small>
                  </div>
                ) : null}
                {diagnostic ? (
                  <div className="connect-diagnostic">
                    <div className="connect-diagnostic__passed"><Check size={16} /> Real model inference passed</div>
                    <div className="connect-diagnostic__stats">
                      <span><b>{diagnostic.model}</b><small>Model</small></span>
                      <span><b>{diagnostic.rows_scored}</b><small>Cross-validated rows</small></span>
                      <span><b>{(diagnostic.accuracy * 100).toFixed(0)}%</b><small>Synthetic test accuracy</small></span>
                    </div>
                    <small className="connect-diagnostic__source">{diagnostic.data}. Example predictions: {diagnostic.sample.map((row) => `${row.predicted_label} (${(row.confidence * 100).toFixed(0)}%)`).join(", ")}</small>
                  </div>
                ) : null}
                {!me.tabpfn.installed ? <div className="connect-inline-note"><AlertCircle size={15} /><span>Install the backend dependencies from <code>backend/requirements.txt</code>, then restart the API.</span></div> : null}
                <div className="connect-inline-note">
                  <AlertCircle size={15} />
                  <span>To test TabPFN on business data, choose the Ask tab and submit <code>Predict which customers are likely to churn</code>. A real analysis needs at least eight labeled rows in its warehouse result.</span>
                </div>
              </section>
            </div>

            <section className="connect-card connect-runtime">
              <div className="connect-card__title"><div><span className="connect-card__icon"><ShieldCheck size={17} /></span><h2>Runtime and security</h2></div></div>
              <div className="connect-runtime__grid">
                <div><small>Signed in as</small><b>{me.username} · {me.domain} · {me.role}</b></div>
                <div><small>Business data mode</small><b>{me.data_mode === "live" ? `${me.warehouse.mode} selected (connection checked above)` : (me.demo_mode ? "Local seeded sample data" : "Local database source")}</b></div>
                <div><small>Language model</small><b>{me.llm.active === "ollama" ? `Ollama · ${me.llm.ollama_model}` : me.llm.active === "anthropic" ? `Anthropic · ${me.llm.anthropic_model}` : (me.llm.active || "Heuristic mode · no LLM key configured")}</b></div>
                <div><small>Warehouse SQL</small><b>Read-only connector; local authentication and audit records remain in DATABASE_URL.</b></div>
              </div>
            </section>
          </>
        ) : null}
      </div>
    </Shell>
  );
}

function StatusCard({
  icon,
  title,
  status,
  tone,
  detail,
}: {
  icon: React.ReactNode;
  title: string;
  status: string;
  tone: "success" | "error" | "demo";
  detail: string;
}) {
  return (
    <section className={`connect-status connect-status--${tone}`}>
      <span className="connect-status__icon">{icon}</span>
      <div><small>{title}</small><strong>{status}</strong><span>{detail}</span></div>
    </section>
  );
}
