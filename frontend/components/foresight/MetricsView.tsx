"use client";

import { useState } from "react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { StatusBadge } from "@/components/foresight/StatusBadge";
import type { ForesightContext, ForesightMetric } from "@/lib/foresight";

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-1 border-b border-slate-100 py-2.5 text-xs sm:grid-cols-[10rem_1fr]">
      <dt className="text-slate-500">{label}</dt>
      <dd className="m-0 text-slate-700">{children}</dd>
    </div>
  );
}

export function MetricsView({ data }: { data: ForesightContext }) {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<"all" | "registry" | "secondary">("all");
  const [open, setOpen] = useState<ForesightMetric | null>(null);
  const query = search.toLowerCase();
  const metrics = data.metrics.filter(
    (metric) => (status === "all" || metric.status === status) && `${metric.name} ${metric.id} ${metric.description}`.toLowerCase().includes(query),
  );
  const hasSecondary = data.metrics.some((metric) => metric.status === "secondary");

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-2xl text-xs text-slate-500">
          Formulas are read from the {data.domain_label} metric registry that Ask AI uses. Secondary definitions answer Ask AI questions the registry does not, and have not been reconciled with it yet.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          {hasSecondary && (
            <select value={status} onChange={(event) => setStatus(event.target.value as typeof status)} aria-label="Filter by status" className="rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-xs text-slate-700">
              <option value="all">All statuses</option>
              <option value="registry">Approved registry</option>
              <option value="secondary">Secondary</option>
            </select>
          )}
          <Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search metrics" aria-label="Search metrics" />
        </div>
      </div>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {metrics.map((metric) => (
          <button
            key={`${metric.status}-${metric.id}`}
            type="button"
            onClick={() => setOpen(metric)}
            className="flex flex-col rounded-xl border border-slate-200 bg-white p-4 text-left shadow-sm transition hover:border-indigo-200 hover:shadow-md focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
          >
            <span className="flex items-start justify-between gap-2">
              <span className="text-sm font-semibold text-slate-800">{metric.name}</span>
              <StatusBadge status={metric.status} />
            </span>
            <span className="mt-1.5 line-clamp-2 text-xs leading-5 text-slate-600">{metric.description}</span>
            <span className="mt-2 break-all font-mono text-[10px] text-slate-500">{metric.formula}</span>
            {metric.pending_rules.length > 0 && <span className="mt-2 text-[10px] font-medium text-amber-700">Business approval pending: {metric.pending_rules.join(", ")}</span>}
          </button>
        ))}
        {!metrics.length && <p className="rounded-lg bg-slate-50 p-3 text-xs text-slate-500">No metrics match.</p>}
      </div>

      {open && (
        <Dialog open onOpenChange={(value) => !value && setOpen(null)}>
          <DialogContent className="max-w-2xl">
            <DialogHeader>
              <DialogTitle className="flex flex-wrap items-center gap-2">{open.name} <StatusBadge status={open.status} /></DialogTitle>
              <DialogDescription>{open.description}</DialogDescription>
            </DialogHeader>
            <dl className="overflow-y-auto px-6 pb-6 pt-2">
              <Row label="Metric ID"><span className="font-mono">{open.id}</span></Row>
              <Row label="Formula"><span className="break-all font-mono">{open.formula}</span></Row>
              <Row label="Aggregation">{open.aggregation}</Row>
              <Row label="Source">{open.source_catalog ? `${open.source_catalog}.${open.source_schema} · ` : ""}{open.source_tables.join(", ")}</Row>
              {open.source_columns.length > 0 && <Row label="Columns">{open.source_columns.join(", ")}</Row>}
              <Row label="Date and grain">{open.supports_time ? open.date_semantics : `Current state; no period applies. ${open.date_semantics}`}</Row>
              {open.dimensions.length > 0 && <Row label="Breakdowns">{open.dimensions.map((d) => d.label).join(", ")}</Row>}
              <Row label="Unit">{open.units.join(", ")}</Row>
              <Row label="Used by">{open.used_by.join(", ")}</Row>
              <Row label="Owner">{open.business_owner ?? "Not assigned"}</Row>
              <Row label="Approval">{open.approval}</Row>
              <Row label="Validation">{open.validation_note}</Row>
              {open.version && <Row label="Version">{open.version}</Row>}
              {open.data_quality.length > 0 && <Row label="Limitations"><ul className="list-inside list-disc space-y-1">{open.data_quality.map((note) => <li key={note}>{note}</li>)}</ul></Row>}
            </dl>
          </DialogContent>
        </Dialog>
      )}
    </div>
  );
}
