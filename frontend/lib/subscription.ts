import { api } from "@/lib/api";

export type UsageNotice = {
  id: number;
  threshold: number;
  level: "info" | "warning" | "critical" | "exhausted";
  message: string;
  created_at: string;
};

/** The plan allowance. It is this application's own limit, never a provider's quota. */
export type PlanUsage = {
  plan: string;
  plan_name: string;
  exempt: boolean;
  percent_used: number | null;
  credits_used: number;
  credits_allowance: number;
  credits_remaining: number;
  resets_at: string;
  notices: UsageNotice[];
  credits_charged?: number;
  feature?: string | null;
  limited_by_plan?: string;
};

export type PlanFeatureRow = {
  feature: string;
  label: string;
  available: boolean;
  enabled: boolean;
  credit_cost: number;
  period_limit: number | null;
};

export type Plan = {
  code: string;
  name: string;
  tagline: string;
  monthly_price_minor: number | null;
  yearly_price_minor: number | null;
  currency: string;
  monthly_credits: number;
  allowance_confirmed: boolean;
  max_context_messages: number;
  allowed_providers: string | null;
  fair_use_note: string;
  sort_order: number;
  active: boolean;
  features: PlanFeatureRow[];
};

export type Payments = { available: boolean; name: string | null; test_mode: boolean };

export type MySubscription =
  | { enabled: false }
  | {
      enabled: true;
      usage: PlanUsage;
      plan: Plan;
      subscription: {
        status: string;
        billing_cycle: string;
        current_period_start: string;
        current_period_end: string;
        auto_renew: boolean;
        cancel_at_period_end: boolean;
        scheduled_plan_code: string | null;
        source: string;
      };
      feature_usage: { feature: string; label: string; used: number; limit: number | null }[];
      own_tokens: { attempts: number; input: number; output: number; attempts_without_token_data: number };
      payments: Payments;
    };

export function loadMySubscription(): Promise<MySubscription> {
  return api<MySubscription>("/v1/subscription/me");
}

export function dismissNotice(id: number): Promise<unknown> {
  return api(`/v1/subscription/notices/${id}/seen`, { method: "POST" });
}

export function formatMoney(minor: number | null, currency: string): string {
  if (minor === null) return "Price not set";
  return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(minor / 100);
}

export function formatDay(iso: string): string {
  const parsed = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export function usageTone(percent: number): "normal" | "warning" | "critical" {
  if (percent >= 90) return "critical";
  return percent >= 80 ? "warning" : "normal";
}
