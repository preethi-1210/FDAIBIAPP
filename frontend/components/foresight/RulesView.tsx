"use client";

import { useState } from "react";
import { Input } from "@/components/ui/input";
import { StatusBadge } from "@/components/foresight/StatusBadge";
import type { ForesightContext, ForesightRule } from "@/lib/foresight";

const GROUPS: { kind: ForesightRule["kind"][]; title: string; note: string }[] = [
  { kind: ["enforced", "shadow"], title: "Enforced controls", note: "Implemented in code. Shadow-mode controls log what they would refuse but refuse nothing yet." },
  { kind: ["definition"], title: "Business definitions", note: "How approved metrics are calculated." },
  { kind: ["pending"], title: "Pending business approval", note: "No approved definition yet. Questions that depend on these are refused or answered with a disclosure." },
  { kind: ["check"], title: "Data checks", note: "Measured with read-only queries and reported only. Not enforced; owner, severity and thresholds need approval." },
  { kind: ["data note"], title: "Known data limitations", note: "Facts about the source data, attached to the evidence of affected answers." },
];

export function RulesView({ data }: { data: ForesightContext }) {
  const [search, setSearch] = useState("");
  const query = search.toLowerCase();
  const rules = data.rules.filter((rule) => `${rule.name} ${rule.description} ${rule.id}`.toLowerCase().includes(query));

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-2xl text-xs text-slate-500">Every rule shows its kind, so nothing displayed here is presented as enforced unless the code enforces it.</p>
        <Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search rules" aria-label="Search rules" />
      </div>
      {GROUPS.map((group) => {
        const items = rules.filter((rule) => group.kind.includes(rule.kind));
        if (!items.length) return null;
        return (
          <section key={group.title}>
            <h3 className="text-sm font-semibold text-slate-800">{group.title} <span className="font-normal text-slate-400">({items.length})</span></h3>
            <p className="mt-0.5 text-xs text-slate-500">{group.note}</p>
            <div className="mt-3 grid gap-2.5 md:grid-cols-2">
              {items.map((rule) => (
                <article key={rule.id} className="rounded-xl border border-slate-200 bg-white p-3">
                  <div className="flex items-start justify-between gap-2">
                    <h4 className="text-xs font-semibold leading-5 text-slate-800">{rule.name}</h4>
                    <StatusBadge status={rule.kind} />
                  </div>
                  <p className="mt-1 text-xs leading-5 text-slate-600">{rule.description}</p>
                  {rule.expression && <p className="mt-1.5 break-all font-mono text-[10px] text-slate-600">{rule.expression}</p>}
                  {rule.violations !== undefined && (
                    <p className={`mt-1.5 text-[11px] font-medium ${rule.violations ? "text-amber-700" : "text-emerald-700"}`}>
                      {rule.violations ? `${rule.violations.toLocaleString()} rows break this` : "No rows break this"} · measured {rule.measured_on}
                    </p>
                  )}
                  {rule.while_pending && <p className="mt-1.5 text-[11px] text-amber-800">Until approved: {rule.while_pending}</p>}
                  {rule.implementation && <p className="mt-1.5 text-[10px] text-slate-500">Implemented in: {rule.implementation}</p>}
                </article>
              ))}
            </div>
          </section>
        );
      })}
      {!rules.length && <p className="rounded-lg bg-slate-50 p-3 text-xs text-slate-500">No rules match.</p>}
    </div>
  );
}
