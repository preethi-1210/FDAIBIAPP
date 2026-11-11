"use client";

import { useCallback, useEffect, useState } from "react";
import { LoaderCircle } from "lucide-react";
import { formatWhen } from "@/components/AccessUi";
import { api } from "@/lib/api";
import { formatMoney, type Plan } from "@/lib/subscription";

type UserRow = {
  username: string;
  administrator: boolean;
  plan_code: string;
  plan_name: string;
  status: string;
  source: string;
  credits_used: number;
  credits_allowance: number;
};
type HistoryRow = { username: string; action: string; actor: string; from_plan: string | null; to_plan: string | null; detail: string | null; created_at: string };
type PaymentRow = { order_id: string; username: string; plan_code: string; amount_minor: number; currency: string; gateway: string; status: string; receipt_number: string | null; created_at: string };
type UsageRow = { key: string; attempts: number; retries: number; failed: number; input_tokens: number; output_tokens: number; total_tokens: number; without_token_data: number; latency_ms: number; cost: number | null };
type UsageReport = {
  note: string;
  by_model: UsageRow[];
  by_domain: UsageRow[];
  by_user: UsageRow[];
  by_day: UsageRow[];
  by_month: UsageRow[];
  quota_events: { provider: string; model: string; kind: string; retry_after_seconds: number | null; domain: string; username: string; created_at: string }[];
  plan_credits_by_feature: { feature: string; charges: number; credits: number }[];
};

function toMajor(minor: number | null): string {
  return minor === null ? "" : String(minor / 100);
}

function toMinor(major: string): number | null {
  const value = major.trim();
  return value === "" ? null : Math.round(Number(value) * 100);
}

export function AdminSubscriptions() {
  const [plans, setPlans] = useState<Plan[]>([]);
  const [users, setUsers] = useState<UserRow[]>([]);
  const [history, setHistory] = useState<HistoryRow[]>([]);
  const [payments, setPayments] = useState<PaymentRow[]>([]);
  const [usage, setUsage] = useState<UsageReport | null>(null);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const [planData, subs, report] = await Promise.all([
        api<{ plans: Plan[] }>("/v1/admin/plans"),
        api<{ users: UserRow[]; history: HistoryRow[]; payments: PaymentRow[] }>("/v1/admin/subscriptions"),
        api<UsageReport>("/v1/admin/usage?days=90"),
      ]);
      setPlans(planData.plans);
      setUsers(subs.users);
      setHistory(subs.history);
      setPayments(subs.payments);
      setUsage(report);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Subscriptions could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function run(action: () => Promise<unknown>, message: string) {
    setError("");
    setSaved("");
    try {
      await action();
      setSaved(message);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "That could not be saved.");
    }
  }

  if (loading) return <div className="platform-loading" role="status"><LoaderCircle className="chat-spin" size={20} /> Loading subscriptions</div>;

  return (
    <div className="admin-subs">
      {error ? <div className="platform-alert" role="alert">{error}</div> : null}
      {saved ? <div className="plan-done" role="status">{saved}</div> : null}

      <section className="access-card" aria-labelledby="plans-heading">
        <h2 id="plans-heading">Plans</h2>
        <p className="access-muted">Prices are entered in major units. A plan with no price cannot be bought. Plan allowances are this application&apos;s own limits and are separate from any provider quota.</p>
        <div className="admin-plans">
          {plans.map((plan) => (
            <PlanEditor key={plan.code} plan={plan} onSave={(body) => run(() => api(`/v1/admin/plans/${plan.code}`, { method: "PUT", body: JSON.stringify(body) }), `${plan.name} saved.`)} />
          ))}
        </div>
      </section>

      <section className="access-card" aria-labelledby="subs-heading">
        <h2 id="subs-heading">Users</h2>
        <p className="access-muted">Changing a plan does not change a user&apos;s domain access. Administrators are recorded but not limited.</p>
        <div className="access-table-wrap">
          <table className="access-table">
            <thead><tr><th>User</th><th>Plan</th><th>Credits used</th><th>Change plan</th><th>Adjust credits</th></tr></thead>
            <tbody>
              {users.map((row) => (
                <UserLine key={row.username} row={row} plans={plans} run={run} />
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {usage ? (
        <section className="access-card" aria-labelledby="usage-heading">
          <h2 id="usage-heading">AI usage, last 90 days</h2>
          <p className="access-muted">{usage.note}</p>
          <UsageTable title="By provider and model" rows={usage.by_model} />
          <UsageTable title="By domain" rows={usage.by_domain} />
          <UsageTable title="By user" rows={usage.by_user} />
          <UsageTable title="By month" rows={usage.by_month} />
          <UsageTable title="By day" rows={usage.by_day} />
          <h3 className="admin-subs__title">Provider quota and rate-limit errors</h3>
          {usage.quota_events.length === 0 ? <p className="access-muted">None recorded.</p> : (
            <div className="access-table-wrap">
              <table className="access-table">
                <thead><tr><th>When</th><th>Model</th><th>Kind</th><th>Retry after</th><th>Domain</th><th>User</th></tr></thead>
                <tbody>
                  {usage.quota_events.map((row, index) => (
                    <tr key={`${row.created_at}-${index}`}>
                      <td>{formatWhen(row.created_at)}</td>
                      <td>{row.provider}:{row.model}</td>
                      <td>{row.kind === "quota_exhausted" ? "Quota exhausted" : "Rate limited"}</td>
                      <td>{row.retry_after_seconds === null ? "Not reported" : `${row.retry_after_seconds} s`}</td>
                      <td>{row.domain}</td>
                      <td>{row.username}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <h3 className="admin-subs__title">Plan credits by feature</h3>
          {usage.plan_credits_by_feature.length === 0 ? <p className="access-muted">No credits charged yet.</p> : (
            <div className="access-table-wrap">
              <table className="access-table">
                <thead><tr><th>Feature</th><th>Charges</th><th>Credits</th></tr></thead>
                <tbody>{usage.plan_credits_by_feature.map((row) => <tr key={row.feature}><td>{row.feature.replaceAll("_", " ")}</td><td>{row.charges}</td><td>{row.credits}</td></tr>)}</tbody>
              </table>
            </div>
          )}
        </section>
      ) : null}

      <section className="access-card" aria-labelledby="payments-admin">
        <h2 id="payments-admin">Payments</h2>
        {payments.length === 0 ? <p className="access-muted">No payments yet.</p> : (
          <div className="access-table-wrap">
            <table className="access-table">
              <thead><tr><th>When</th><th>User</th><th>Plan</th><th>Amount</th><th>Status</th><th>Receipt</th></tr></thead>
              <tbody>
                {payments.map((row) => (
                  <tr key={row.order_id}>
                    <td>{formatWhen(row.created_at)}</td><td>{row.username}</td><td>{row.plan_code}</td>
                    <td>{formatMoney(row.amount_minor, row.currency)}{row.gateway === "mock" ? " (test)" : ""}</td>
                    <td>{row.status}</td><td>{row.receipt_number ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="access-card" aria-labelledby="sub-history">
        <h2 id="sub-history">Subscription history</h2>
        <div className="access-table-wrap">
          <table className="access-table">
            <thead><tr><th>When</th><th>User</th><th>Change</th><th>By</th><th>Detail</th></tr></thead>
            <tbody>
              {history.map((row, index) => (
                <tr key={`${row.created_at}-${index}`}>
                  <td>{formatWhen(row.created_at)}</td>
                  <td>{row.username === "*" ? "All users" : row.username}</td>
                  <td>{row.action.replaceAll("_", " ")}{row.to_plan ? `: ${row.from_plan ? `${row.from_plan} to ` : ""}${row.to_plan}` : ""}</td>
                  <td>{row.actor}</td>
                  <td>{row.detail ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function UsageTable({ title, rows }: { title: string; rows: UsageRow[] }) {
  return (
    <>
      <h3 className="admin-subs__title">{title}</h3>
      {rows.length === 0 ? <p className="access-muted">No model calls recorded.</p> : (
        <div className="access-table-wrap">
          <table className="access-table">
            <thead><tr><th>{title.replace("By ", "")}</th><th>Attempts</th><th>Retries</th><th>Failed</th><th>Input tokens</th><th>Output tokens</th><th>Total tokens</th><th>No token data</th><th>Model time</th><th>Cost</th></tr></thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.key}>
                  <td>{row.key}</td><td>{row.attempts}</td><td>{row.retries}</td><td>{row.failed}</td>
                  <td>{row.input_tokens.toLocaleString()}</td><td>{row.output_tokens.toLocaleString()}</td><td>{row.total_tokens.toLocaleString()}</td><td>{row.without_token_data}</td>
                  <td>{Math.round(row.latency_ms / 1000).toLocaleString()} s</td>
                  <td>{row.cost === null ? "No rate set" : row.cost.toFixed(4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

function UserLine({ row, plans, run }: { row: UserRow; plans: Plan[]; run: (action: () => Promise<unknown>, message: string) => Promise<void> }) {
  const [plan, setPlan] = useState(row.plan_code);
  const [credits, setCredits] = useState("");
  const [reason, setReason] = useState("");
  const noReason = reason.trim().length < 3;
  return (
    <tr>
      <td>{row.username}{row.administrator ? " (admin)" : ""}</td>
      <td>{row.plan_name}</td>
      <td>{row.credits_used.toLocaleString()} of {row.credits_allowance.toLocaleString()}</td>
      <td>
        <div className="admin-subs__inline">
          <select aria-label={`Plan for ${row.username}`} value={plan} onChange={(event) => setPlan(event.target.value)}>
            {plans.map((item) => <option key={item.code} value={item.code}>{item.name}</option>)}
          </select>
          <input aria-label={`Reason for ${row.username}`} placeholder="Reason (required)" value={reason} onChange={(event) => setReason(event.target.value)} />
          <button type="button" className="access-button access-button--ghost" disabled={plan === row.plan_code || noReason} onClick={() => void run(() => api(`/v1/admin/users/${encodeURIComponent(row.username)}/subscription`, { method: "POST", body: JSON.stringify({ plan_code: plan, reason }) }), `${row.username} moved to ${plan}.`)}>Change</button>
        </div>
      </td>
      <td>
        <div className="admin-subs__inline">
          <input aria-label={`Credit change for ${row.username}`} type="number" placeholder="+/- credits" value={credits} onChange={(event) => setCredits(event.target.value)} />
          <button type="button" className="access-button access-button--ghost" disabled={!credits || Number(credits) === 0 || noReason} onClick={() => void run(() => api(`/v1/admin/users/${encodeURIComponent(row.username)}/credits`, { method: "POST", body: JSON.stringify({ credits: Number(credits), reason }) }), `Credits adjusted for ${row.username}.`)}>Adjust</button>
        </div>
      </td>
    </tr>
  );
}

function PlanEditor({ plan, onSave }: { plan: Plan; onSave: (body: unknown) => Promise<void> }) {
  const [draft, setDraft] = useState({
    name: plan.name,
    tagline: plan.tagline,
    monthly: toMajor(plan.monthly_price_minor),
    yearly: toMajor(plan.yearly_price_minor),
    currency: plan.currency,
    credits: String(plan.monthly_credits),
    context: String(plan.max_context_messages),
    providers: plan.allowed_providers ?? "",
    fair: plan.fair_use_note,
    active: plan.active,
  });
  const [features, setFeatures] = useState(plan.features);
  const set = (key: keyof typeof draft) => (event: React.ChangeEvent<HTMLInputElement>) =>
    setDraft((current) => ({ ...current, [key]: key === "active" ? event.target.checked : event.target.value }));

  return (
    <details className="admin-plan">
      <summary>
        <b>{plan.name}</b>
        <span>{formatMoney(plan.monthly_price_minor, plan.currency)} monthly · {plan.monthly_credits.toLocaleString()} credits{plan.allowance_confirmed ? "" : " (placeholder)"}{plan.active ? "" : " · disabled"}</span>
      </summary>
      <div className="admin-plan__fields">
        <label>Name<input value={draft.name} onChange={set("name")} /></label>
        <label>Description<input value={draft.tagline} onChange={set("tagline")} /></label>
        <label>Monthly price<input inputMode="decimal" value={draft.monthly} placeholder="Not set" onChange={set("monthly")} /></label>
        <label>Yearly price<input inputMode="decimal" value={draft.yearly} placeholder="Not set" onChange={set("yearly")} /></label>
        <label>Currency<input maxLength={3} value={draft.currency} onChange={set("currency")} /></label>
        <label>Credits per month<input type="number" min={0} value={draft.credits} onChange={set("credits")} /></label>
        <label>Context messages (max 24)<input type="number" min={0} max={24} value={draft.context} onChange={set("context")} /></label>
        <label>Allowed providers (empty: all)<input value={draft.providers} placeholder="google,ollama" onChange={set("providers")} /></label>
        <label className="admin-plan__wide">Fair-use note<input value={draft.fair} onChange={set("fair")} /></label>
        <label className="admin-plan__check"><input type="checkbox" checked={draft.active} onChange={set("active")} /> Offered to users</label>
      </div>
      <div className="access-table-wrap">
        <table className="access-table">
          <thead><tr><th>Feature</th><th>Included</th><th>Credits per use</th><th>Limit per period</th></tr></thead>
          <tbody>
            {features.map((item, index) => {
              const change = (patch: Partial<Plan["features"][number]>) => setFeatures((current) => current.map((row, position) => (position === index ? { ...row, ...patch } : row)));
              return (
                <tr key={item.feature}>
                  <td>{item.label}{item.available ? "" : " (not built yet: shown as planned)"}</td>
                  <td><input type="checkbox" aria-label={`${item.label} included`} checked={item.enabled} onChange={(event) => change({ enabled: event.target.checked })} /></td>
                  <td><input type="number" min={0} aria-label={`${item.label} credits`} value={item.credit_cost} onChange={(event) => change({ credit_cost: Number(event.target.value) })} /></td>
                  <td><input type="number" min={0} aria-label={`${item.label} limit`} value={item.period_limit ?? ""} placeholder="None" onChange={(event) => change({ period_limit: event.target.value === "" ? null : Number(event.target.value) })} /></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <button
        type="button"
        className="access-button"
        onClick={() => void onSave({
          name: draft.name,
          tagline: draft.tagline,
          monthly_price_minor: toMinor(draft.monthly),
          yearly_price_minor: toMinor(draft.yearly),
          currency: draft.currency,
          monthly_credits: Number(draft.credits),
          max_context_messages: Number(draft.context),
          allowed_providers: draft.providers.trim() || null,
          fair_use_note: draft.fair,
          sort_order: plan.sort_order,
          active: draft.active,
          features: features.map(({ feature, enabled, credit_cost, period_limit }) => ({ feature, enabled, credit_cost, period_limit })),
        })}
      >
        Save {plan.name}
      </button>
    </details>
  );
}
