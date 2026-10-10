"use client";

import { useEffect, useState } from "react";
import { AlertCircle, CheckCircle2, Database, LoaderCircle, Play, ShieldCheck } from "lucide-react";
import { Shell } from "@/components/Shell";
import { api } from "@/lib/api";

type WarehouseVerification = {
  status: "passed";
  warehouse: string;
  domain: string;
  catalog: string;
  schema: string;
  tables_checked: string[];
  source_table: string;
  sql: string[];
  results: Record<string, string | number | null>[][];
  note: string;
};

export default function BenchmarksPage() {
  const [verification, setVerification] = useState<WarehouseVerification | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!localStorage.getItem("ryze_token")) window.location.href = "/login";
  }, []);

  async function runVerification() {
    setLoading(true);
    setError("");
    setVerification(null);
    try {
      setVerification(await api<WarehouseVerification>("/warehouse/verify", { method: "POST" }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Live source verification failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Shell>
      <div className="workspace-dark platform-page">
        <header className="platform-heading">
          <div>
            <span className="platform-eyebrow"><ShieldCheck size={14} /> LIVE SOURCE VALIDATION</span>
            <h1>Benchmarks</h1>
            <p>Run a read-only verification against your authenticated domain catalog. Results are actual source output, not a model accuracy estimate.</p>
          </div>
          <button type="button" className="platform-button platform-button--primary" disabled={loading} onClick={() => void runVerification()}>
            {loading ? <LoaderCircle className="chat-spin" size={15} /> : <Play size={15} />}
            {loading ? "Running live checks…" : "Run live verification"}
          </button>
        </header>

        {error ? <div className="platform-alert" role="alert"><AlertCircle size={16} />{error}</div> : null}
        {loading ? (
          <div className="platform-loading" role="status"><LoaderCircle className="chat-spin" size={22} /> Checking catalog metadata and executing a representative aggregate…</div>
        ) : null}
        {!verification && !loading && !error ? (
          <section className="platform-panel platform-empty-state">
            <Database size={23} />
            <h2>Validate the live data path</h2>
            <p>The check inspects the signed-in user&apos;s catalog and schema, confirms expected tables, then runs a domain-specific aggregate query.</p>
          </section>
        ) : null}
        {verification ? (
          <>
            <section className="platform-panel benchmark-result">
              <div className="platform-panel__heading">
                <div className="benchmark-result__title"><CheckCircle2 size={19} /><div><h2>Live source verification passed</h2><p>{verification.note}</p></div></div>
                <span className="platform-status platform-status--passed"><CheckCircle2 size={13} /> PASSED</span>
              </div>
              <div className="platform-kpis benchmark-kpis">
                <Metric label="Warehouse" value={verification.warehouse} />
                <Metric label="Authorized domain" value={verification.domain} />
                <Metric label="Catalog / schema" value={`${verification.catalog}.${verification.schema}`} />
                <Metric label="Tables confirmed" value={String(verification.tables_checked.length)} />
              </div>
              <p className="benchmark-source"><Database size={14} /> Representative query source: <code>{verification.source_table}</code></p>
              <div className="benchmark-table-wrap">
                <table className="platform-table">
                  <thead><tr><th>Source table</th><th>Representative query result</th></tr></thead>
                  <tbody>
                    {verification.results.map((result, index) => (
                      <tr key={`${verification.source_table}-${index}`}>
                        <td className="platform-question">{verification.source_table}</td>
                        <td><code>{JSON.stringify(result[0] ?? {})}</code></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <details className="benchmark-sql">
                <summary>Show verified SQL ({verification.sql.length})</summary>
                <ol>{verification.sql.map((query, index) => <li key={index}><pre>{query}</pre></li>)}</ol>
              </details>
              <div className="platform-source-list"><strong>Confirmed source tables</strong>{verification.tables_checked.map((table) => <code key={table}>{table}</code>)}</div>
            </section>
          </>
        ) : null}
      </div>
    </Shell>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return <article className="platform-kpi benchmark-kpi"><span className="platform-kpi__label">{label}</span><strong>{value}</strong></article>;
}
