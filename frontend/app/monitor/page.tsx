"use client";

import { useCallback, useEffect, useState } from "react";
import { Activity, AlertCircle, CheckCircle2, Clock3, LoaderCircle, RefreshCw, ShieldAlert } from "lucide-react";
import { Shell } from "@/components/Shell";
import { api } from "@/lib/api";

type ActivityRow = {
  audit_id: string;
  question: string;
  status: "passed" | "blocked";
  rows: number;
  created_at: string;
};

type MonitorData = {
  domain: string;
  days: number;
  total_questions: number;
  unique_users: number;
  guardrail_blocks: number;
  pending_reviews: number;
  activities: ActivityRow[];
};

export default function MonitorPage() {
  const [period, setPeriod] = useState(30);
  const [data, setData] = useState<MonitorData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setData(await api<MonitorData>(`/monitor?days=${period}`));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load activity.");
    } finally {
      setLoading(false);
    }
  }, [period]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <Shell>
      <div className="workspace-dark platform-page">
        <header className="platform-heading">
          <div>
            <span className="platform-eyebrow"><Activity size={14} /> ACTIVITY & GOVERNANCE</span>
            <h1>Monitor</h1>
            <p>Your recent questions, guardrail outcomes, and pending human reviews for {data?.domain || "your authorized domain"}.</p>
          </div>
          <div className="platform-actions">
            <select aria-label="Activity period" value={period} onChange={(event) => setPeriod(Number(event.target.value))}>
              <option value={7}>Last 7 days</option>
              <option value={30}>Last 30 days</option>
              <option value={90}>Last 90 days</option>
            </select>
            <button type="button" className="platform-button" disabled={loading} onClick={() => void load()}>
              {loading ? <LoaderCircle className="chat-spin" size={15} /> : <RefreshCw size={15} />} Refresh
            </button>
          </div>
        </header>

        {error ? <div className="platform-alert" role="alert"><AlertCircle size={16} />{error}</div> : null}
        {loading && !data ? (
          <div className="platform-loading" role="status"><LoaderCircle className="chat-spin" size={22} /> Loading your authorized activity…</div>
        ) : null}
        {data ? (
          <>
            <section className="platform-kpis" aria-label="Activity summary">
              <Metric label="Total questions" value={data.total_questions} caption={`Last ${data.days} days`} icon={<Activity size={16} />} />
              <Metric label="Unique users" value={data.unique_users} caption="Your account only" icon={<Clock3 size={16} />} />
              <Metric label="Guardrail blocks" value={data.guardrail_blocks} caption="Queries stopped before source execution" icon={<ShieldAlert size={16} />} />
              <Metric label="Pending reviews" value={data.pending_reviews} caption="Human-in-the-loop queue" icon={<CheckCircle2 size={16} />} />
            </section>

            <section className="platform-panel">
              <div className="platform-panel__heading">
                <div><h2>Activity digest</h2><p>Audit history is restricted to your signed-in user and business domain.</p></div>
                <span className="platform-count">{data.activities.length} recent</span>
              </div>
              {data.activities.length ? (
                <div className="platform-table-wrap">
                  <table className="platform-table">
                    <thead><tr><th>Question</th><th>Audit ID</th><th>Status</th><th>Rows</th><th>Created</th></tr></thead>
                    <tbody>
                      {data.activities.map((item) => (
                        <tr key={item.audit_id}>
                          <td className="platform-question">{item.question}</td>
                          <td>{item.audit_id}</td>
                          <td><span className={`platform-status platform-status--${item.status}`}>{item.status === "passed" ? <CheckCircle2 size={13} /> : <ShieldAlert size={13} />}{item.status}</span></td>
                          <td>{item.rows}</td>
                          <td>{new Date(item.created_at).toLocaleString()}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : <div className="platform-empty">No questions are recorded in this period.</div>}
            </section>
          </>
        ) : null}
      </div>
    </Shell>
  );
}

function Metric({ label, value, caption, icon }: { label: string; value: number; caption: string; icon: React.ReactNode }) {
  return (
    <article className="platform-kpi">
      <span className="platform-kpi__icon">{icon}</span>
      <span className="platform-kpi__label">{label}</span>
      <strong>{value.toLocaleString()}</strong>
      <small>{caption}</small>
    </article>
  );
}
