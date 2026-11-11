"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Sparkles, X } from "lucide-react";
import {
  dismissNotice,
  formatDay,
  loadMySubscription,
  type MySubscription,
  type PlanUsage,
  type UsageNotice,
  usageTone,
} from "@/lib/subscription";

export function UsageBar({ percent }: { percent: number }) {
  return (
    <div
      className={`plan-bar plan-bar--${usageTone(percent)}`}
      role="progressbar"
      aria-label="AI allowance used"
      aria-valuenow={percent}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <span style={{ width: `${percent}%` }} />
    </div>
  );
}

/** The plan and allowance in the Ask AI sidebar. Hidden when subscriptions are switched off. */
export function PlanUsageCard({ usage }: { usage: PlanUsage | null }) {
  if (!usage) return null;
  return (
    <section className="plan-card" aria-label="Your plan and AI usage">
      <div className="plan-card__top">
        <b>RYZE AI</b>
        <span className={`plan-pill plan-pill--${usage.plan}`}>{usage.plan_name} plan</span>
      </div>
      {usage.exempt ? (
        <p className="plan-card__note">Administrator account: usage is recorded but not limited.</p>
      ) : (
        <>
          <div className="plan-card__usage">
            <span>Monthly AI usage</span>
            <b>{usage.percent_used}% used</b>
          </div>
          <UsageBar percent={usage.percent_used ?? 0} />
          <p className="plan-card__note">
            {usage.credits_remaining.toLocaleString()} of {usage.credits_allowance.toLocaleString()} credits left. Resets {formatDay(usage.resets_at)}.
          </p>
        </>
      )}
      <Link href="/plans" className="plan-card__upgrade"><Sparkles size={13} aria-hidden="true" /> {usage.plan === "free" ? "Upgrade plan" : "View plans"}</Link>
      <Link href="/billing" className="plan-card__manage">Manage subscription</Link>
    </section>
  );
}

/** Threshold notices. Each appears until dismissed and is never raised again in that period. */
export function PlanNotices({ notices, onDismiss }: { notices: UsageNotice[]; onDismiss: (id: number) => void }) {
  if (!notices.length) return null;
  return (
    <div className="plan-notices">
      {notices.map((notice) => (
        <div key={notice.id} className={`plan-notice plan-notice--${notice.level}`} role={notice.level === "info" ? "status" : "alert"}>
          <span>{notice.message}</span>
          {notice.threshold >= 80 ? <Link href="/plans">Upgrade</Link> : null}
          <button type="button" aria-label="Dismiss notice" onClick={() => onDismiss(notice.id)}><X size={13} /></button>
        </div>
      ))}
    </div>
  );
}

/** Shown when a plan stopped a request. */
export function UpgradePrompt({ message, onClose }: { message: string; onClose: () => void }) {
  return (
    <div className="plan-notice plan-notice--upgrade" role="alert">
      <span>{message}</span>
      <Link href="/plans">View plans</Link>
      <button type="button" aria-label="Dismiss" onClick={onClose}><X size={13} /></button>
    </div>
  );
}

/** The current plan in an account menu, linking to where it is managed. */
export function PlanMenuLink({ usage }: { usage?: PlanUsage | null }) {
  const own = usePlanUsage(usage === undefined);
  const shown = usage === undefined ? own.usage : usage;
  if (!shown) return null;
  return (
    <>
      <small>Plan: {shown.plan_name}{shown.percent_used !== null ? ` · ${shown.percent_used}% used` : ""}</small>
      <Link href="/billing" className="app-account-menu__link">Manage subscription</Link>
    </>
  );
}

export function usePlanUsage(enabled = true) {
  const [usage, setUsage] = useState<PlanUsage | null>(null);
  const refresh = useCallback(async () => {
    try {
      const mine: MySubscription = await loadMySubscription();
      setUsage(mine.enabled ? mine.usage : null);
    } catch {
      // The plan card is informational: Ask AI keeps working without it.
      setUsage(null);
    }
  }, []);
  useEffect(() => { if (enabled) void refresh(); }, [enabled, refresh]);
  const update = useCallback((next: PlanUsage | null | undefined) => {
    if (next) setUsage(next);
  }, []);
  const dismiss = useCallback((id: number) => {
    setUsage((current) => (current ? { ...current, notices: current.notices.filter((item) => item.id !== id) } : current));
    void dismissNotice(id).catch(() => undefined);
  }, []);
  return { usage, update, dismiss, refresh };
}
