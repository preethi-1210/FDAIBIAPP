"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Check, Clock3, LoaderCircle, Lock, Sparkles, X } from "lucide-react";
import { UsageBar } from "@/components/PlanUsage";
import { Shell } from "@/components/Shell";
import { api } from "@/lib/api";
import { formatDay, formatMoney, loadMySubscription, type MySubscription, type Payments, type Plan } from "@/lib/subscription";

type Cycle = "monthly" | "yearly";
type Order = { order_id: string; amount_minor: number; currency: string; plan_code: string; billing_cycle: Cycle; test_mode: boolean };

export default function PlansPage() {
  const [plans, setPlans] = useState<Plan[]>([]);
  const [payments, setPayments] = useState<Payments | null>(null);
  const [mine, setMine] = useState<MySubscription | null>(null);
  const [cycle, setCycle] = useState<Cycle>("monthly");
  const [confirming, setConfirming] = useState<Plan | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const [catalog, me] = await Promise.all([
        api<{ plans: Plan[]; payments: Payments }>("/v1/subscription/plans"),
        loadMySubscription(),
      ]);
      setPlans(catalog.plans);
      setPayments(catalog.payments);
      setMine(me);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Plans could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const active = mine && mine.enabled ? mine : null;
  const currentOrder = plans.find((plan) => plan.code === active?.plan.code)?.sort_order ?? 0;

  async function pay(plan: Plan, outcome: "paid" | "failed") {
    setBusy(true);
    setError("");
    try {
      const order = await api<Order>("/v1/billing/checkout", {
        method: "POST",
        body: JSON.stringify({ plan_code: plan.code, billing_cycle: cycle }),
      });
      const payment = await api<Record<string, string>>("/v1/billing/mock/pay", {
        method: "POST",
        body: JSON.stringify({ order_id: order.order_id, outcome }),
      });
      // The plan changes only if the server accepts this. Nothing here decides it.
      const verified = await api<{ receipt_number: string }>("/v1/billing/verify", {
        method: "POST",
        body: JSON.stringify({ order_id: order.order_id, payment }),
      });
      setConfirming(null);
      setDone(`You are now on ${plan.name}. Receipt ${verified.receipt_number}.`);
      await load();
    } catch (cause) {
      setConfirming(null);
      setError(cause instanceof Error ? cause.message : "The payment could not be completed.");
    } finally {
      setBusy(false);
    }
  }

  async function downgrade(plan: Plan) {
    setBusy(true);
    setError("");
    try {
      const result = await api<{ effective_at: string }>("/v1/subscription/downgrade", {
        method: "POST",
        body: JSON.stringify({ plan_code: plan.code }),
      });
      setDone(`You will move to ${plan.name} on ${formatDay(result.effective_at)}. Until then, nothing changes.`);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The plan could not be changed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell>
      <div className="dashboard-page access-page plans-page">
        <div className="access-heading">
          <p className="access-eyebrow"><Sparkles size={14} /> RYZE AI PLANS</p>
          <h1>Choose your plan</h1>
          <p>A plan sets how much AI you can use. It does not change which business domains you can access.</p>
        </div>

        {error ? <div className="platform-alert" role="alert">{error}</div> : null}
        {done ? <div className="plan-done" role="status"><Check size={15} /> {done}</div> : null}

        {loading ? (
          <div className="platform-loading" role="status"><LoaderCircle className="chat-spin" size={20} /> Loading plans</div>
        ) : (
          <>
            {active ? (
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
                <Link href="/billing" className="access-button access-button--ghost">Manage subscription</Link>
              </section>
            ) : null}

            <div className="plans-cycle" role="group" aria-label="Billing period">
              {(["monthly", "yearly"] as Cycle[]).map((item) => (
                <button key={item} type="button" aria-pressed={cycle === item} className={cycle === item ? "plans-cycle__on" : ""} onClick={() => setCycle(item)}>
                  {item === "monthly" ? "Monthly" : "Yearly"}
                </button>
              ))}
            </div>

            <div className="plans-grid">
              {plans.map((plan) => {
                const isCurrent = plan.code === active?.plan.code;
                const price = cycle === "monthly" ? plan.monthly_price_minor : plan.yearly_price_minor;
                const free = plan.monthly_price_minor === 0 && plan.yearly_price_minor === 0;
                const lower = plan.sort_order < currentOrder;
                const reason = !payments?.available
                  ? "Online payment is not available yet. Ask an administrator to change your plan."
                  : price === null
                    ? "An administrator has not set this price yet."
                    : "";
                return (
                  <article key={plan.code} className={`plans-card${isCurrent ? " plans-card--current" : ""}`}>
                    <h2>{plan.name}</h2>
                    <p className="plans-card__tagline">{plan.tagline}</p>
                    <p className="plans-card__price">
                      {free ? "No charge" : formatMoney(price, plan.currency)}
                      {!free && price !== null ? <small> / {cycle === "monthly" ? "month" : "year"}</small> : null}
                    </p>
                    <p className="plans-card__credits">
                      {plan.monthly_credits.toLocaleString()} AI credits each month
                      {!plan.allowance_confirmed ? <em> (provisional)</em> : null}
                    </p>
                    {isCurrent ? (
                      <button type="button" className="access-button plans-card__cta" disabled>Current plan</button>
                    ) : lower ? (
                      <button type="button" className="access-button access-button--ghost plans-card__cta" disabled={busy} onClick={() => void downgrade(plan)}>Move to {plan.name}</button>
                    ) : (
                      <button type="button" className="access-button plans-card__cta" disabled={Boolean(reason) || busy} onClick={() => { setDone(""); setConfirming(plan); }}>Upgrade to {plan.name}</button>
                    )}
                    {!isCurrent && !lower && reason ? <small className="plans-card__why">{reason}</small> : null}
                    <ul className="plans-card__features">
                      {plan.features.map((item) => (
                        <FeatureLine key={item.feature} item={item} />
                      ))}
                    </ul>
                    {plan.fair_use_note ? <small className="plans-card__fair">{plan.fair_use_note}</small> : null}
                  </article>
                );
              })}
            </div>
          </>
        )}

        {confirming ? (
          <div className="plans-modal" role="dialog" aria-modal="true" aria-labelledby="confirm-plan">
            <div className="plans-modal__box">
              <button type="button" className="plans-modal__close" aria-label="Close" disabled={busy} onClick={() => setConfirming(null)}><X size={15} /></button>
              <h2 id="confirm-plan">Upgrade to {confirming.name}</h2>
              <p>
                {formatMoney(cycle === "monthly" ? confirming.monthly_price_minor : confirming.yearly_price_minor, confirming.currency)} per {cycle === "monthly" ? "month" : "year"}.
                {" "}Your new allowance starts now, and any unused credits on your current plan are not carried over.
              </p>
              {payments?.test_mode ? (
                <p className="plans-modal__test" role="note"><b>Test mode.</b> No money moves and no card is needed.</p>
              ) : null}
              <div className="plans-modal__actions">
                <button type="button" className="access-button" disabled={busy} onClick={() => void pay(confirming, "paid")}>
                  {busy ? <LoaderCircle className="chat-spin" size={14} /> : null} {payments?.test_mode ? "Complete test payment" : "Pay"}
                </button>
                {payments?.test_mode ? (
                  <button type="button" className="access-button access-button--ghost" disabled={busy} onClick={() => void pay(confirming, "failed")}>Simulate a failed payment</button>
                ) : null}
              </div>
            </div>
          </div>
        ) : null}
      </div>
    </Shell>
  );
}

function FeatureLine({ item }: { item: Plan["features"][number] }) {
  if (!item.available) {
    return (
      <li className="plans-feature plans-feature--planned">
        <Clock3 size={13} aria-hidden="true" /> <span>{item.label}</span> <em>Planned, not available yet</em>
      </li>
    );
  }
  if (!item.enabled) {
    return <li className="plans-feature plans-feature--locked"><Lock size={13} aria-hidden="true" /> <span>{item.label}</span> <em>Not included</em></li>;
  }
  return (
    <li className="plans-feature">
      <Check size={13} aria-hidden="true" /> <span>{item.label}</span>
      {item.period_limit !== null ? <em>{item.period_limit} per period</em> : null}
    </li>
  );
}
