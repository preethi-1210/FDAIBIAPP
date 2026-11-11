"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { CreditCard, LoaderCircle } from "lucide-react";
import { formatWhen } from "@/components/AccessUi";
import { UsageBar } from "@/components/PlanUsage";
import { Shell } from "@/components/Shell";
import { api } from "@/lib/api";
import { formatDay, formatMoney, loadMySubscription, type MySubscription } from "@/lib/subscription";

type Payment = {
  order_id: string;
  plan_code: string;
  billing_cycle: string;
  amount_minor: number;
  currency: string;
  gateway: string;
  status: string;
  receipt_number: string | null;
  failure_reason: string | null;
  created_at: string;
};
type HistoryRow = { action: string; actor: string; from_plan: string | null; to_plan: string | null; detail: string | null; created_at: string };

export default function BillingPage() {
  const [mine, setMine] = useState<MySubscription | null>(null);
  const [payments, setPayments] = useState<Payment[]>([]);
  const [history, setHistory] = useState<HistoryRow[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [me, records] = await Promise.all([
        loadMySubscription(),
        api<{ payments: Payment[]; history: HistoryRow[] }>("/v1/billing/history"),
      ]);
      setMine(me);
      setPayments(records.payments);
      setHistory(records.history);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Billing could not be loaded.");
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function act(path: string) {
    setBusy(true);
    setError("");
    try {
      await api(path, { method: "POST" });
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "That could not be completed.");
    } finally {
      setBusy(false);
    }
  }

  const active = mine && mine.enabled ? mine : null;
  const sub = active?.subscription;
  return (
    <Shell>
      <div className="dashboard-page access-page plans-page">
        <div className="access-heading">
          <p className="access-eyebrow"><CreditCard size={14} /> SUBSCRIPTION</p>
          <h1>Manage subscription</h1>
          <p>Your plan, your AI usage and your payment history.</p>
        </div>
        {error ? <div className="platform-alert" role="alert">{error}</div> : null}
        {!active || !sub ? (
          <div className="platform-loading" role="status"><LoaderCircle className="chat-spin" size={20} /> Loading</div>
        ) : (
          <>
            <section className="access-card plans-current" aria-label="Your current plan">
              <div>
                <span className="access-muted">Current plan</span>
                <strong>{active.plan.name}</strong>
              </div>
              {active.usage.exempt ? (
                <p className="access-muted">Administrator account: usage is recorded but not limited.</p>
              ) : (
                <div className="plans-current__usage">
                  <div className="plan-card__usage"><span>AI usage</span><b>{active.usage.percent_used}% consumed</b></div>
                  <UsageBar percent={active.usage.percent_used ?? 0} />
                  <small>{active.usage.credits_remaining.toLocaleString()} of {active.usage.credits_allowance.toLocaleString()} credits left. Resets {formatDay(active.usage.resets_at)}.</small>
                </div>
              )}
              <Link href="/plans" className="access-button">View plans</Link>
            </section>

            <section className="access-card" aria-labelledby="billing-status">
              <h2 id="billing-status">Billing</h2>
              <p className="access-muted">
                {sub.billing_cycle === "none" ? "No billing period." : `Billed ${sub.billing_cycle}.`}{" "}
                {sub.cancel_at_period_end
                  ? `Cancelled: access continues until ${formatDay(sub.current_period_end)}.`
                  : sub.scheduled_plan_code
                    ? `Moves to ${sub.scheduled_plan_code} on ${formatDay(sub.current_period_end)}.`
                    : `Current period ends ${formatDay(sub.current_period_end)}.`}
              </p>
              {active.plan.code !== "free" ? (
                sub.cancel_at_period_end || sub.scheduled_plan_code ? (
                  <button type="button" className="access-button access-button--ghost" disabled={busy} onClick={() => void act("/v1/subscription/resume")}>Keep my current plan</button>
                ) : (
                  <button type="button" className="access-button access-button--danger" disabled={busy} onClick={() => void act("/v1/subscription/cancel")}>Cancel subscription</button>
                )
              ) : null}
            </section>

            <section className="access-card" aria-labelledby="usage-features">
              <h2 id="usage-features">Usage this period</h2>
              <div className="access-table-wrap">
                <table className="access-table">
                  <thead><tr><th>Feature</th><th>Used</th><th>Limit</th></tr></thead>
                  <tbody>
                    {active.feature_usage.map((row) => (
                      <tr key={row.feature}><td>{row.label}</td><td>{row.used}</td><td>{row.limit ?? "Allowance only"}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="access-muted">
                Your model tokens this period: {active.own_tokens.input.toLocaleString()} in, {active.own_tokens.output.toLocaleString()} out, as reported by each provider.
                {active.own_tokens.attempts_without_token_data > 0 ? ` ${active.own_tokens.attempts_without_token_data} attempt(s) reported no token data, so these are minimums.` : ""}
                {" "}Credits are this application&apos;s allowance, not a provider&apos;s quota.
              </p>
            </section>

            <section className="access-card" aria-labelledby="payments-heading">
              <h2 id="payments-heading">Payments and receipts</h2>
              {payments.length === 0 ? <p className="access-muted">No payments yet.</p> : (
                <div className="access-table-wrap">
                  <table className="access-table">
                    <thead><tr><th>When</th><th>Plan</th><th>Period</th><th>Amount</th><th>Status</th><th>Receipt</th></tr></thead>
                    <tbody>
                      {payments.map((row) => (
                        <tr key={row.order_id}>
                          <td>{formatWhen(row.created_at)}</td>
                          <td>{row.plan_code}</td>
                          <td>{row.billing_cycle}</td>
                          <td>{formatMoney(row.amount_minor, row.currency)}{row.gateway === "mock" ? " (test)" : ""}</td>
                          <td>{row.status}{row.failure_reason ? `: ${row.failure_reason}` : ""}</td>
                          <td>{row.receipt_number ?? "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            <section className="access-card" aria-labelledby="history-heading">
              <h2 id="history-heading">Plan history</h2>
              <div className="access-table-wrap">
                <table className="access-table">
                  <thead><tr><th>When</th><th>Change</th><th>By</th><th>Detail</th></tr></thead>
                  <tbody>
                    {history.map((row, index) => (
                      <tr key={`${row.created_at}-${index}`}>
                        <td>{formatWhen(row.created_at)}</td>
                        <td>{row.action.replaceAll("_", " ")}{row.to_plan ? `: ${row.from_plan ? `${row.from_plan} to ` : ""}${row.to_plan}` : ""}</td>
                        <td>{row.actor}</td>
                        <td>{row.detail ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )}
      </div>
    </Shell>
  );
}
