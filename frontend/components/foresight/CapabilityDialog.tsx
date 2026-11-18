"use client";

import { HiCheckCircle, HiMinusCircle, HiOutlineExclamationTriangle } from "react-icons/hi2";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { StatusBadge } from "@/components/foresight/StatusBadge";
import type { BankingCapability } from "@/lib/bankingCapabilities";
import type { ForesightContext } from "@/lib/foresight";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</h4>
      {children}
    </section>
  );
}

function Assumption() {
  return (
    <p className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 p-2.5 text-[11px] leading-5 text-amber-800">
      <HiOutlineExclamationTriangle className="mt-0.5 shrink-0" size={14} />
      Illustrative business description, not an approved business definition. Data support, metrics, rules and terms below come from the approved banking context.
    </p>
  );
}

export function CapabilityDialog({ capability, data, onClose }: { capability: BankingCapability; data: ForesightContext; onClose: () => void }) {
  const Icon = capability.icon;
  const context = (data.capabilities ?? []).filter((item) => capability.contextIds.includes(item.id));
  const entities = data.entities.filter((entity) => capability.tables.includes(entity.table));
  const metrics = data.metrics.filter((metric) => metric.status === "registry" && capability.metrics.includes(metric.id));
  const pendingIds = new Set(context.flatMap((item) => item.pending_rules));
  const rules = data.rules.filter(
    (rule) => pendingIds.has(rule.id) || (rule.applies_to ?? []).some((id) => capability.metrics.includes(id)),
  );
  const unavailable = context.flatMap((item) => item.unavailable);
  const terms = (data.glossary ?? []).filter((term) => capability.terms.includes(term.term));

  return (
    <Dialog open onOpenChange={(value) => !value && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2.5">
            <span className="grid size-9 place-items-center rounded-lg bg-indigo-50 text-indigo-600"><Icon size={19} /></span>
            {capability.title}
          </DialogTitle>
          <DialogDescription>{capability.overview}</DialogDescription>
        </DialogHeader>
        <Tabs defaultValue="overview" className="flex min-h-0 flex-1 flex-col px-6 pb-6 pt-4">
          <TabsList>
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="activities">Activities</TabsTrigger>
            <TabsTrigger value="entities">Business entities</TabsTrigger>
            <TabsTrigger value="rules">Business rules</TabsTrigger>
            <TabsTrigger value="glossary">Glossary</TabsTrigger>
          </TabsList>
          <div className="min-h-0 flex-1 overflow-y-auto pr-1">
            <TabsContent value="overview" className="space-y-5">
              <Assumption />
              <Section title="Business objectives">
                <ul className="list-inside list-disc space-y-1 text-sm text-slate-700">{capability.objectives.map((item) => <li key={item}>{item}</li>)}</ul>
              </Section>
              <Section title="Business responsibilities">
                <ul className="list-inside list-disc space-y-1 text-sm text-slate-700">{capability.responsibilities.map((item) => <li key={item}>{item}</li>)}</ul>
              </Section>
              <Section title="Approved metrics">
                {metrics.length ? (
                  <ul className="space-y-1.5">{metrics.map((metric) => <li key={metric.id} className="text-sm text-slate-700"><span className="font-medium">{metric.name}</span> <span className="text-xs text-slate-500">· {metric.description}</span></li>)}</ul>
                ) : <p className="text-sm text-slate-500">No approved metric covers this capability.</p>}
              </Section>
              <Section title="Related data sources">
                <div className="flex flex-wrap gap-1.5">{entities.map((entity) => <span key={entity.table} className="rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-[11px] text-slate-600">{entity.source}</span>)}</div>
              </Section>
              {unavailable.length > 0 && (
                <Section title="Not available in the data">
                  <ul className="space-y-1.5">{unavailable.map((item) => <li key={item.topic} className="text-sm text-slate-700"><span className="font-medium">{item.topic}:</span> <span className="text-slate-600">{item.reason}</span></li>)}</ul>
                </Section>
              )}
            </TabsContent>

            <TabsContent value="activities" className="space-y-3">
              <Assumption />
              <ul className="space-y-2">
                {capability.activities.map((activity) => (
                  <li key={activity.text} className="flex items-start gap-2.5 rounded-lg border border-slate-200 p-3">
                    {activity.evidence ? <HiCheckCircle className="mt-0.5 shrink-0 text-emerald-600" size={16} /> : <HiMinusCircle className="mt-0.5 shrink-0 text-slate-400" size={16} />}
                    <div>
                      <p className="text-sm font-medium text-slate-800">{activity.text}</p>
                      <p className="mt-0.5 text-xs text-slate-500">{activity.evidence ? `Recorded in: ${activity.evidence}` : "Not recorded in banking.gold"}</p>
                    </div>
                  </li>
                ))}
              </ul>
            </TabsContent>

            <TabsContent value="entities" className="space-y-3">
              {entities.map((entity) => (
                <article key={entity.table} className="rounded-xl border border-slate-200 p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <h5 className="text-sm font-semibold text-slate-800">{entity.name}</h5>
                    <StatusBadge status={entity.kind} />
                  </div>
                  <p className="mt-1 text-xs text-slate-600">{entity.description ?? "No business definition recorded."}{entity.coverage ? ` · ${entity.coverage}` : ""}</p>
                  <p className="mt-1 text-[11px] text-slate-500">{entity.source}{entity.rows !== null ? ` · ${entity.rows.toLocaleString()} rows` : ""}</p>
                  {entity.relationships.length > 0 && <p className="mt-2 text-[11px] text-slate-600">Joins: {entity.relationships.join("; ")}</p>}
                </article>
              ))}
            </TabsContent>

            <TabsContent value="rules" className="space-y-2.5">
              {rules.length ? rules.map((rule) => (
                <article key={rule.id} className="rounded-xl border border-slate-200 p-3">
                  <div className="flex items-start justify-between gap-2">
                    <h5 className="text-sm font-medium text-slate-800">{rule.name}</h5>
                    <StatusBadge status={rule.kind} />
                  </div>
                  <p className="mt-1 text-xs leading-5 text-slate-600">{rule.description}</p>
                  {rule.while_pending && <p className="mt-1 text-xs text-amber-800">Until approved: {rule.while_pending}</p>}
                </article>
              )) : <p className="text-sm text-slate-500">No business rule is recorded for this capability.</p>}
            </TabsContent>

            <TabsContent value="glossary" className="grid gap-2.5 sm:grid-cols-2">
              {terms.map((term) => (
                <article key={term.term} className="rounded-xl border border-slate-200 p-3">
                  <div className="flex items-start justify-between gap-2">
                    <h5 className="text-sm font-medium text-slate-800">{term.term}</h5>
                    <StatusBadge status={term.status} />
                  </div>
                  <p className="mt-1 text-xs leading-5 text-slate-600">{term.definition}</p>
                </article>
              ))}
            </TabsContent>
          </div>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}
