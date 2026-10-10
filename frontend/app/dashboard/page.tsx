"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { LoaderCircle } from "lucide-react";
import { Shell } from "@/components/Shell";
import { BusinessContext } from "@/components/ContextArchitecture";
import { BarList, LineChart, PALETTES, PalettePicker, PieChart } from "@/components/Charts";
import { RiskMatrixHeatMap } from "@/components/RiskMatrixHeatMap";
import { api } from "@/lib/api";

type Series = { label: string; value: number; events?: number };
type HeatCell = { region: string; category: string; value: number };
type Audit = { audit_id: string; question: string; sql?: string; rows: number; guardrail_ok: boolean; user: string; created_at: string };
type LossRegion = { region: string; numerator: number; denominator: number; loss_ratio: number };
type DashDomain = "banking" | "finance" | "insurance" | "retail";
type Dash = {
  domain: DashDomain;
  domain_label: string;
  metric_name: string;
  volume: number;
  events: number;
  customers: number;
  churn_rate: number;
  churn_label?: string;
  claims: number;
  loss_ratio: number;
  loss_ratio_label: string;
  loss_ratio_description: string;
  loss_numerator_label: string;
  loss_denominator_label: string;
  guardrail_pass_rate: number;
  queries: number;
  by_region: Series[];
  by_category: Series[];
  by_month: Series[];
  heatmap: HeatCell[];
  loss_by_region: LossRegion[];
  audits: Audit[];
  live: { label: string; value: number; created_at: string }[];
  updated_at: string;
};

export default function DashboardPage() {
  const [data, setData] = useState<Dash | null>(null);
  const [businessContext, setBusinessContext] = useState<BusinessContext | null>(null);
  const [contextError, setContextError] = useState("");
  const [error, setError] = useState("");
  const refreshTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const [palette, setPalette] = useState("slate");
  const [tab, setTab] = useState<"overview" | "heatmap" | "loss" | "audit">("overview");
  const colors = PALETTES[palette];
  const lossRegions = data?.loss_by_region ?? [];

  async function load() {
    try {
      setData(await api<Dash>("/dashboard"));
      setError("");
      if (!refreshTimer.current) {
        refreshTimer.current = setInterval(() => void load(), 30000);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Dashboard failed");
      if (refreshTimer.current) {
        clearInterval(refreshTimer.current);
        refreshTimer.current = null;
      }
    }
  }

  async function loadBusinessContext() {
    try {
      setBusinessContext(await api<BusinessContext>("/context"));
      setContextError("");
    } catch (err) {
      setContextError(err instanceof Error ? err.message : "Could not load your business context.");
    }
  }

  useEffect(() => {
    if (!localStorage.getItem("ryze_token")) {
      window.location.href = "/login";
      return;
    }
    void load();
    void loadBusinessContext();
    return () => {
      if (refreshTimer.current) clearInterval(refreshTimer.current);
    };
  }, []);

  return (
    <Shell>
      <div className="dashboard-page workspace-dark">
      <div className="dashboard-heading">
        <h1 style={{ marginTop: 0, marginBottom: 0 }}>{data?.domain_label || "Dashboard"}</h1>
        <PalettePicker value={palette} onChange={setPalette} />
      </div>
      <p className="dashboard-subtitle">
        {data
          ? `Live refresh · charts update every 30 seconds · ${data.updated_at}`
          : "Waiting for the configured live warehouse."}
      </p>
      {businessContext ? (
        <section className="mb-5 flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-indigo-100 bg-gradient-to-r from-white to-indigo-50/60 p-4 shadow-sm">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-wide text-indigo-600">Your authorized business context</p>
            <p className="mt-1 text-sm font-semibold text-slate-800">
              {businessContext.user.username} · {businessContext.user.role} · {businessContext.user.domain_label}
            </p>
            <p className="mt-1 text-xs text-slate-500">
              {businessContext.source.warehouse}
              {businessContext.source.catalog ? ` · ${businessContext.source.catalog}` : ""}
              {businessContext.source.schema ? ` · ${businessContext.source.schema}` : ""}
              {" · "}
              {businessContext.source.metadata_available ? "Source metadata available" : "Source metadata unavailable"}
            </p>
          </div>
          <Link href="/context" className="inline-flex items-center gap-2 rounded-lg border border-indigo-200 bg-white px-3 py-2 text-xs font-semibold text-indigo-700 transition hover:bg-indigo-50">
            Explore context <span aria-hidden="true">→</span>
          </Link>
        </section>
      ) : contextError ? (
        <div role="alert" className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
          <span>Business context is unavailable: {contextError}</span>
          <button type="button" onClick={() => void loadBusinessContext()} className="font-semibold underline">Retry</button>
        </div>
      ) : null}
      {error ? (
        <div
          role="alert"
          className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-rose-300/25 bg-rose-300/10 p-4 text-sm text-rose-100"
        >
          <p>{error}</p>
          <button
            type="button"
            onClick={() => void load()}
            className="rounded-lg border border-rose-200/30 px-3 py-2 font-medium hover:bg-rose-200/10"
          >
            Retry connection
          </button>
        </div>
      ) : null}
      {data ? (
        <>
          <div className="dashboard-kpis">
            <Kpi label={data.metric_name} value={data.volume.toLocaleString(undefined, { maximumFractionDigits: 0 })} color={colors[0]} />
            <Kpi label="Events" value={String(data.events)} color={colors[1]} />
            <Kpi label="Customers" value={String(data.customers)} color={colors[2]} />
            <Kpi label={data.churn_label || "Churn"} value={`${(data.churn_rate * 100).toFixed(1)}%`} color={colors[3]} />
            <Kpi label={data.loss_ratio_label} value={`${(data.loss_ratio * 100).toFixed(1)}%`} color={colors[4] || colors[0]} />
            <Kpi label="Guardrail pass" value={`${(data.guardrail_pass_rate * 100).toFixed(0)}%`} color={colors[1]} />
          </div>
          <div className="dashboard-tabs">
            {(["overview", "heatmap", "loss", "audit"] as const).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`dashboard-tab${tab === t ? " dashboard-tab--active" : ""}`}
                aria-pressed={tab === t}
              >
                {t === "loss" ? "Loss ratio" : t === "audit" ? "Audit trails" : t}
              </button>
            ))}
          </div>
          <div className="fade-content" key={tab}>
          {tab === "overview" ? (
            <>
              <div className="dashboard-grid">
                <Card title="Trend">
                  <LineChart data={data.by_month} colors={colors} />
                </Card>
                <Card title="Mix">
                  <PieChart data={data.by_category} colors={colors} />
                </Card>
              </div>
              <div className="dashboard-grid dashboard-grid--equal">
                <Card title="By region">
                  <BarList data={data.by_region} colors={colors} />
                </Card>
                <Card title="Live ticks">
                  {data.live.map((e, i) => (
                    <div className="live-row" key={i}>
                      <span>
                        {e.label} · {e.created_at}
                      </span>
                      <strong>{e.value.toFixed(2)}</strong>
                    </div>
                  ))}
                </Card>
              </div>
            </>
          ) : null}
          {tab === "heatmap" ? (
            <RiskMatrixHeatMap data={data.heatmap} metricName={data.metric_name} />
          ) : null}
          {tab === "loss" ? (
            <>
              <Card title={`${data.loss_ratio_label} by region`}>
                <p className="dashboard-card__description">
                  {data.loss_ratio_description} Portfolio ratio:{" "}
                  <strong>{(data.loss_ratio * 100).toFixed(1)}%</strong>
                </p>
                <DataTable
                  columns={["Region", data.loss_denominator_label, data.loss_numerator_label, data.loss_ratio_label]}
                  rows={lossRegions.map((row) => [
                    row.region,
                    formatAmount(row.denominator),
                    formatAmount(row.numerator),
                    `${(row.loss_ratio * 100).toFixed(1)}%`,
                  ])}
                  emptyMessage={`No regional ${data.loss_denominator_label.toLowerCase()} or ${data.loss_numerator_label.toLowerCase()} data is available yet.`}
                />
              </Card>
              <div className="dashboard-grid dashboard-grid--insights">
                <Card title={`${data.loss_ratio_label} by region`}>
                  <BarList
                    data={lossRegions.map((row) => ({ label: row.region, value: row.loss_ratio * 100 }))}
                    colors={colors}
                    formatValue={(value) => `${value.toFixed(1)}%`}
                  />
                </Card>
                <Card title={`${data.loss_numerator_label} by region`}>
                  <BarList
                    data={lossRegions.map((row) => ({ label: row.region, value: row.numerator }))}
                    colors={colors}
                  />
                </Card>
                <Card title={`${data.loss_denominator_label} by region`}>
                  <BarList
                    data={lossRegions.map((row) => ({ label: row.region, value: row.denominator }))}
                    colors={colors}
                  />
                </Card>
              </div>
            </>
          ) : null}
          {tab === "audit" ? (
            <Card title="Audit trails">
              {data.audits.map((a) => (
                <div key={a.audit_id + a.created_at} style={{ marginBottom: 12, borderBottom: "1px solid #eef2f4", paddingBottom: 10 }}>
                  <b>{a.audit_id}</b> · {a.user} · {a.created_at} · {a.guardrail_ok ? "guardrail pass" : "blocked"} · {a.rows} rows
                  <div>{a.question}</div>
                  <pre style={{ fontSize: 11, whiteSpace: "pre-wrap" }}>{a.sql}</pre>
                </div>
              ))}
              {!data.audits.length ? <p>No queries logged yet. Run a prompt on Ask.</p> : null}
            </Card>
          ) : null}
          </div>
        </>
      ) : (
        <div className="dashboard-loading" role="status">
          <div><LoaderCircle className="chat-spin" size={24} /><strong>Loading business metrics</strong><span>Connecting to your selected data source…</span></div>
        </div>
      )}
      </div>
    </Shell>
  );
}

function Kpi({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="dashboard-kpi" style={{ "--accent": color } as React.CSSProperties}>
      <div className="dashboard-kpi__label">{label}</div>
      <div className="dashboard-kpi__value">{value}</div>
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="dashboard-card">
      <h3>{title}</h3>
      {children}
    </div>
  );
}

function DataTable({
  columns,
  rows,
  emptyMessage,
}: {
  columns: string[];
  rows: string[][];
  emptyMessage: string;
}) {
  if (!rows.length) return <div className="dashboard-table-empty">{emptyMessage}</div>;
  return (
    <div className="dashboard-table-wrap">
      <table className="dashboard-table">
        <thead>
          <tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${row[0]}-${row[1] ?? index}`}>
              {row.map((value, columnIndex) => (
                <td className={columnIndex === 0 ? "dashboard-table__primary" : ""} key={`${columnIndex}-${value}`}>
                  {value}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatAmount(value: number): string {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(value);
}
