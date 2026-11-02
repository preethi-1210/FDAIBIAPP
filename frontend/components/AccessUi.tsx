import { CheckCircle2, Clock3, XCircle } from "lucide-react";

export type AccessRequest = {
  id: number;
  username: string;
  display_name: string;
  current_domain: string;
  current_domain_label: string;
  requested_domain: string;
  requested_domain_label: string;
  reason: string;
  status: "pending" | "approved" | "rejected";
  created_at: string;
  decided_at: string | null;
  decided_by: string | null;
  decision_note: string | null;
};

export function formatWhen(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export function StatusPill({ status }: { status: AccessRequest["status"] }) {
  const Icon = status === "approved" ? CheckCircle2 : status === "rejected" ? XCircle : Clock3;
  return (
    <span className={`access-status access-status--${status}`}>
      <Icon size={12} aria-hidden="true" /> {status[0].toUpperCase() + status.slice(1)}
    </span>
  );
}
