import { Badge } from "@/components/ui/badge";

type Tone = "neutral" | "success" | "info" | "warning" | "danger";

const STATUS: Record<string, { label: string; tone: Tone }> = {
  registry: { label: "Approved registry", tone: "success" },
  secondary: { label: "Secondary definition", tone: "warning" },
  configured: { label: "Used by registry", tone: "success" },
  verified: { label: "Verified in data", tone: "info" },
  enforced: { label: "Enforced", tone: "success" },
  shadow: { label: "Shadow mode", tone: "warning" },
  definition: { label: "Definition", tone: "info" },
  pending: { label: "Pending approval", tone: "warning" },
  "data note": { label: "Data note", tone: "neutral" },
  check: { label: "Check · not enforced", tone: "warning" },
  VERIFIED: { label: "Verified", tone: "success" },
  PROPOSED: { label: "Proposed", tone: "info" },
  PENDING_BUSINESS_APPROVAL: { label: "Pending approval", tone: "warning" },
  UNAVAILABLE: { label: "Not in data", tone: "danger" },
  available: { label: "Available", tone: "success" },
  partial: { label: "Partly available", tone: "info" },
  unavailable: { label: "Not in data", tone: "danger" },
  entity: { label: "Business entity", tone: "info" },
  aggregate: { label: "Aggregate", tone: "neutral" },
};

export function StatusBadge({ status }: { status: string }) {
  const item = STATUS[status] ?? { label: status, tone: "neutral" as Tone };
  return <Badge tone={item.tone}>{item.label}</Badge>;
}
