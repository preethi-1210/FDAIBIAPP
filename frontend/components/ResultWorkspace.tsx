"use client";

import { useEffect, useState } from "react";
import { Check, CircleAlert, LoaderCircle } from "lucide-react";
import { AutoChart, PALETTES, PalettePicker } from "@/components/Charts";
import { api } from "@/lib/api";

type Result = {
  answer: string;
  sql?: string;
  table: Record<string, unknown>[];
  prediction?: {
    type: string;
    labels: (string | number | boolean | null)[];
    probabilities: number[];
    values: number[];
    probability_label?: string | number | boolean | null;
  } | null;
  plan?: { subtasks?: string[] };
  uncertainty?: { confidence_range: number[] };
  error?: string;
  llm_provider?: string;
  audit_id?: string;
  chart?: { type: string; x: string; y: string; data: { label: string; value: number }[] };
  lineage?: {
    engine: string;
    catalog: string;
    flow: string[];
    medallion: { layer: string; fqn: string; blurb: string; stats: string }[];
    views: { id: string; fqn: string; label: string; state: string; queries: number }[];
    trace: { step: number; title: string; detail: string; by?: string; columns?: string[]; status?: string }[];
    note: string;
  };
  guardrails?: { ok: boolean; error?: string; checks: { name: string; pass: boolean }[] };
  hitl?: { status: string; audit_id: string };
  model_gov?: Record<string, string | boolean>;
};

const TABS = ["Insight", "Lineage", "Guardrails", "HITL", "Model gov", "Audit"] as const;

export function ResultWorkspace({ result }: { result: Result }) {
  const [tab, setTab] = useState<(typeof TABS)[number]>("Insight");
  const [palette, setPalette] = useState("slate");
  const [filterView, setFilterView] = useState<string | null>(null);
  const colors = PALETTES[palette];

  useEffect(() => {
    setTab("Insight");
    setFilterView(null);
  }, [result]);

  return (
    <section className="result-workspace" aria-label="Analysis details">
      <div className="result-workspace__tabs" role="tablist" aria-label="Analysis detail views">
        {TABS.map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            className={`result-workspace__tab${tab === t ? " result-workspace__tab--active" : ""}`}
            role="tab"
            aria-selected={tab === t}
            aria-controls="result-workspace-panel"
          >
            {t}
          </button>
        ))}
      </div>
      <div id="result-workspace-panel" className="result-workspace__panel" role="tabpanel" aria-label={`${tab} results`}>
        {tab === "Insight" ? <Insight result={result} colors={colors} palette={palette} onPalette={setPalette} /> : null}
        {tab === "Lineage" ? result.lineage ? (
          <Lineage lineage={result.lineage} filterView={filterView} onFilter={setFilterView} />
        ) : <UnavailablePanel title="Lineage was not included in this result." /> : null}
        {tab === "Guardrails" ? result.guardrails ? (
          <Guardrails data={result.guardrails} />
        ) : <UnavailablePanel title="No guardrail report was returned for this result." /> : null}
        {tab === "HITL" ? <HitlPanel seed={result.hitl} /> : null}
        {tab === "Model gov" ? result.model_gov ? (
          <ModelGov data={result.model_gov} />
        ) : <UnavailablePanel title="No model-governance details were returned for this result." /> : null}
        {tab === "Audit" ? <AuditMini result={result} /> : null}
      </div>
    </section>
  );
}

function Insight({
  result,
  colors,
  palette,
  onPalette,
}: {
  result: Result;
  colors: string[];
  palette: string;
  onPalette: (n: string) => void;
}) {
  return (
    <>
      <Card title="Natural language insight">
        <p>{result.answer}</p>
        <p style={{ fontSize: 14, color: "#4d6480" }}>
          Plan: {(result.plan?.subtasks || []).join(" → ") || "retrieval"}
          {result.llm_provider ? ` · LLM ${result.llm_provider}` : " · heuristic"}
          {result.audit_id ? ` · ${result.audit_id}` : ""}
        </p>
      </Card>
      {result.prediction ? <PredictionOutput prediction={result.prediction} uncertainty={result.uncertainty} /> : null}
      {result.sql ? (
        <Card title="SQL query">
          <pre style={{ whiteSpace: "pre-wrap" }}>{result.sql}</pre>
        </Card>
      ) : null}
      {result.chart ? (
        <Card title={`Chart · ${result.chart.type} · ${result.chart.y} by ${result.chart.x}`}>
          <PalettePicker value={palette} onChange={onPalette} />
          <div style={{ marginTop: 12 }}>
            <AutoChart spec={result.chart} colors={colors} />
          </div>
        </Card>
      ) : null}
      {result.table?.length ? (
        <Card title={`Table · ${result.table.length} rows`}>
          <div className="result-workspace__table-wrap">
            <table className="result-workspace__table">
              <thead>
                <tr>
                  {Object.keys(result.table[0]).map((k) => (
                    <th key={k}>{k}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.table.slice(0, 40).map((row, i) => (
                  <tr key={i}>
                    {Object.keys(result.table[0]).map((k) => (
                      <td key={k}>{String(row[k])}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      ) : null}
    </>
  );
}

function PredictionOutput({
  prediction,
  uncertainty,
}: {
  prediction: NonNullable<Result["prediction"]>;
  uncertainty: Result["uncertainty"];
}) {
  const classification = prediction.type === "classification";
  const rowCount = classification ? prediction.labels.length : prediction.values.length;
  const probabilityTitle = prediction.probability_label == null
    ? "Predicted-class confidence"
    : `Probability of ${String(prediction.probability_label)}`;

  return (
    <Card title={`Self-hosted TabPFN · ${prediction.type} · ${rowCount} cross-validated rows`}>
      {classification ? (
        <>
          <p style={{ fontSize: 13, color: "#4d6480" }}>
            Rows are displayed in warehouse result order after unlabeled targets are excluded; row numbers are not warehouse record IDs.{" "}
            {prediction.probability_label == null
              ? "Probability is the confidence of each predicted class."
              : `${probabilityTitle} is shown alongside each predicted label.`}
            {uncertainty ? ` Confidence range: ${uncertainty.confidence_range.map((n) => `${(n * 100).toFixed(1)}%`).join("–")}.` : ""}
          </p>
          <div style={{ overflowX: "auto" }}>
            <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 13 }}>
              <thead>
                <tr><th style={th}>Scored row</th><th style={th}>Predicted label</th><th style={th}>{probabilityTitle}</th></tr>
              </thead>
              <tbody>
                {prediction.labels.slice(0, 12).map((label, index) => (
                  <tr key={index}>
                    <td style={td}>{index + 1}</td>
                    <td style={td}>{String(label)}</td>
                    <td style={td}>{prediction.probabilities[index] == null ? "—" : `${(prediction.probabilities[index] * 100).toFixed(1)}%`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : (
        <>
          <p style={{ fontSize: 13, color: "#4d6480" }}>
            {uncertainty ? `Output interquartile range: ${uncertainty.confidence_range.map((n) => n.toFixed(2)).join("–")}.` : ""}
          </p>
          <div style={{ overflowX: "auto" }}>
            <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 13 }}>
              <thead><tr><th style={th}>Scored row</th><th style={th}>Predicted value</th></tr></thead>
              <tbody>
                {prediction.values.slice(0, 12).map((value, index) => (
                  <tr key={index}><td style={td}>{index + 1}</td><td style={td}>{value.toFixed(2)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Card>
  );
}

function Lineage({
  lineage,
  filterView,
  onFilter,
}: {
  lineage: NonNullable<Result["lineage"]>;
  filterView: string | null;
  onFilter: (id: string | null) => void;
}) {
  return (
    <>
      <Card title="Lineage · medallion architecture">
        <p style={{ color: "#4d6480" }}>
          Every figure traces to a governed gold view. Catalog <b>{lineage.catalog}</b>. Engine: {lineage.engine}.
        </p>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(240px,1fr))", gap: 12 }}>
          {lineage.medallion.map((m) => (
            <div key={m.layer} style={{ border: "1px solid #dbe4f0", borderRadius: 12, padding: 12 }}>
              <div style={{ fontWeight: 700 }}>{m.layer}</div>
              <div style={{ fontSize: 12, color: "#1f7aef", margin: "6px 0" }}>{m.fqn}</div>
              <div style={{ fontSize: 13 }}>{m.blurb}</div>
              <div style={{ fontSize: 12, color: "#4d6480", marginTop: 8 }}>{m.stats}</div>
            </div>
          ))}
        </div>
      </Card>
      <Card title="Gold views this session">
        <p style={{ fontSize: 12, color: "#4d6480" }}>Click a view to filter the trace. ● queried · write dest · ○ idle</p>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(220px,1fr))", gap: 8 }}>
          {lineage.views.map((v) => (
            <button
              key={v.id}
              onClick={() => onFilter(filterView === v.id ? null : v.id)}
              style={{
                textAlign: "left",
                border: filterView === v.id ? "2px solid #1f7aef" : "1px solid #dbe4f0",
                borderRadius: 12,
                padding: 10,
                background: v.state === "idle" ? "#f8fafc" : "white",
                cursor: "pointer",
              }}
            >
              <div>
                {v.state === "idle" ? "○" : "●"} {v.id}
              </div>
              <div style={{ fontSize: 12, color: "#4d6480" }}>{v.label}</div>
              <div style={{ fontSize: 11 }}>{v.fqn}</div>
            </button>
          ))}
        </div>
        <p style={{ fontSize: 12, marginTop: 12 }}>{lineage.flow.join(" → ")}</p>
        <p style={{ fontSize: 12, color: "#4d6480" }}>{lineage.note}</p>
      </Card>
      <Card title="Query lineage">
        {(filterView ? lineage.trace.filter((t) => t.detail.includes(filterView) || t.step !== 3) : lineage.trace).map(
          (t) => (
            <div key={t.step} style={{ marginBottom: 10, paddingBottom: 10, borderBottom: "1px solid #eef3f8" }}>
              <b>
                {t.step} · {t.title}
              </b>
              {t.by ? <span style={{ color: "#4d6480" }}> · {t.by}</span> : null}
              <div style={{ whiteSpace: "pre-wrap", fontSize: 13 }}>{t.detail}</div>
              {t.columns?.length ? <div style={{ fontSize: 12 }}>Columns: {t.columns.join(", ")}</div> : null}
              {t.status ? <div style={{ fontSize: 12 }}>{t.status}</div> : null}
            </div>
          )
        )}
      </Card>
    </>
  );
}

function Guardrails({ data }: { data: NonNullable<Result["guardrails"]> }) {
  return (
    <Card title="Guardrails">
      <p>{data.ok ? "Query passed the SQL guardrail." : data.error}</p>
      {data.checks.map((c) => (
        <div key={c.name} style={{ marginBottom: 6 }}>
          {c.pass ? "✓" : "✗"} {c.name}
        </div>
      ))}
    </Card>
  );
}

function HitlPanel({ seed }: { seed?: Result["hitl"] }) {
  type ReviewRow = {
    id: number;
    audit_id: string;
    question: string;
    status: "pending" | "approved" | "rejected";
    reason: string;
    user: string;
    created_at: string;
  };
  const [rows, setRows] = useState<ReviewRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    api<ReviewRow[]>("/hitl")
      .then((data) => {
        if (!cancelled) setRows(data);
      })
      .catch((cause: unknown) => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : "Could not load the review queue.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function act(id: number, action: "approve" | "reject") {
    setBusyId(id);
    setError("");
    try {
      await api(`/hitl/${id}/${action}`, { method: "POST" });
      setRows(await api<ReviewRow[]>("/hitl"));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The review decision could not be saved.");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <Card title="HITL review queue">
      <p style={{ fontSize: 14, color: "#4d6480" }}>
        Prediction and failed-guardrail runs wait here. Current prompt: {seed?.status || "n/a"} ({seed?.audit_id})
      </p>
      {error ? <div className="hitl-state hitl-state--error" role="alert"><CircleAlert size={17} /> {error}</div> : null}
      {loading ? <div className="hitl-state"><LoaderCircle className="chat-spin" size={18} /> Loading review requests…</div> : null}
      {!loading && !error && rows.length === 0 ? (
        <div className="hitl-state">No review requests are waiting for this domain.</div>
      ) : null}
      {!loading && rows.length > 0 ? (
        <div className="hitl-table-wrap">
          <table className="hitl-table">
            <thead>
              <tr><th>Audit</th><th>Analysis request</th><th>Reason</th><th>Requested by</th><th>Status</th><th>Decision</th></tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td className="hitl-table__audit">{row.audit_id}<small>{new Date(row.created_at).toLocaleString()}</small></td>
                  <td className="hitl-table__question">{row.question}</td>
                  <td>{row.reason.replaceAll("_", " ")}</td>
                  <td>{row.user}</td>
                  <td><span className={`hitl-status hitl-status--${row.status}`}>{row.status}</span></td>
                  <td>
                    {row.status === "pending" ? (
                      <div className="hitl-actions">
                        <button type="button" disabled={busyId === row.id} onClick={() => void act(row.id, "approve")}>
                          {busyId === row.id ? "Saving…" : "Approve"}
                        </button>
                        <button type="button" disabled={busyId === row.id} onClick={() => void act(row.id, "reject")}>Reject</button>
                      </div>
                    ) : <span className="hitl-decision-complete"><Check size={14} /> Recorded</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </Card>
  );
}

function UnavailablePanel({ title }: { title: string }) {
  return (
    <Card title="Details unavailable">
      <p style={{ fontSize: 14, color: "#4d6480" }}>{title}</p>
    </Card>
  );
}

function ModelGov({ data }: { data: Record<string, string | boolean> }) {
  return (
    <Card title="Model governance">
      {Object.entries(data).map(([k, v]) => (
        <div key={k} style={{ marginBottom: 6 }}>
          <b>{k}</b>: {String(v)}
        </div>
      ))}
    </Card>
  );
}

function AuditMini({ result }: { result: Result }) {
  return (
    <Card title="Audit trail">
      <div>Audit ID: {result.audit_id}</div>
      <div>Rows: {result.table?.length || 0}</div>
      <div>SQL logged · immutable session write to fdryze_audit_log</div>
      <pre style={{ whiteSpace: "pre-wrap", fontSize: 12 }}>{result.sql}</pre>
    </Card>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ background: "white", borderRadius: 16, padding: 16, marginBottom: 12, boxShadow: "0 8px 24px rgba(15,39,68,0.06)" }}>
      <h3 style={{ marginTop: 0 }}>{title}</h3>
      {children}
    </div>
  );
}

const th: React.CSSProperties = { textAlign: "left", borderBottom: "1px solid #dbe4f0", padding: "8px 6px" };
const td: React.CSSProperties = { borderBottom: "1px solid #eef3f8", padding: "8px 6px" };
