"use client";

import { useMemo, useState } from "react";
import { HiArrowRight, HiOutlineInformationCircle } from "react-icons/hi2";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { StatusBadge } from "@/components/foresight/StatusBadge";
import { CapabilityDialog } from "@/components/foresight/CapabilityDialog";
import { BANKING_CAPABILITIES, type BankingCapability } from "@/lib/bankingCapabilities";
import { total, type ForesightContext, type ForesightTerm } from "@/lib/foresight";

type LegacyTerm = { term: string; definition: string; table: string; column: string | null; mapping_status: string };

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-wrap justify-between gap-2 border-b border-slate-100 py-2 text-xs last:border-0">
      <dt className="text-slate-500">{label}</dt>
      <dd className="m-0 text-right font-medium text-slate-700">{value}</dd>
    </div>
  );
}

function breakdown(counts: Record<string, number>, labels: Record<string, string>): string {
  const parts = Object.entries(counts).map(([key, value]) => `${value} ${labels[key] ?? key}`);
  return parts.length ? `${total(counts)} (${parts.join(", ")})` : "0";
}

export function OverviewView({ data, legacyGlossary }: { data: ForesightContext; legacyGlossary: LegacyTerm[] }) {
  const [open, setOpen] = useState<BankingCapability | null>(null);
  const [search, setSearch] = useState("");
  const o = data.overview;
  const terms: ForesightTerm[] = useMemo(
    () =>
      data.glossary ??
      legacyGlossary.map((term) => ({
        term: term.term,
        definition: term.definition,
        status: term.mapping_status === "mapped" ? "VERIFIED" : "PROPOSED",
        maps_to: null,
        source: term.column ? `${term.table}.${term.column}` : term.table,
      })),
    [data.glossary, legacyGlossary],
  );
  const shownTerms = terms.filter((term) => `${term.term} ${term.definition}`.toLowerCase().includes(search.toLowerCase()));

  return (
    <div className="space-y-6">
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Domain summary</CardTitle></CardHeader>
          <CardContent>
            <dl>
              <Fact label="Approved business entities" value={`${o.entities}${o.aggregates ? ` · ${o.aggregates} approved aggregates` : ""}`} />
              <Fact label="Verified relationships" value={breakdown(o.relationships, { configured: "used by registry", verified: "verified in data" })} />
              <Fact label="Metrics" value={breakdown(o.metrics, { registry: "approved registry", secondary: "secondary" })} />
              <Fact label="Business rules" value={String(total(o.rules))} />
              <Fact label="Entities with business definitions" value={`${o.documented_entities} of ${o.entities + o.aggregates}`} />
              <Fact label="Technical tables (not business entities)" value={String(o.technical_tables)} />
            </dl>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>Sources and status</CardTitle></CardHeader>
          <CardContent>
            <dl>
              <Fact label="Analytical source" value={o.analytical_source} />
              <Fact label="Metric registry" value={`${o.registry_source} · version ${data.registry_version}`} />
              <Fact label="Application data" value={o.application_source} />
              <Fact label="Metadata" value={o.metadata_available ? "Loaded from Unity Catalog" : "Unavailable"} />
              <Fact label="Last metadata load" value={o.synchronized_at ? new Date(o.synchronized_at).toLocaleString() : "Not loaded since the API started"} />
              <Fact label="Relationships and checks verified on" value={o.verified_on} />
            </dl>
          </CardContent>
        </Card>
      </div>

      {data.domain === "banking" && data.capabilities && (
        <section aria-labelledby="banking-capabilities">
          <h3 id="banking-capabilities" className="text-sm font-semibold text-slate-800">Banking business context</h3>
          <p className="mt-1 text-xs text-slate-500">Business functions, what they cover, and which approved data supports them.</p>
          <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {BANKING_CAPABILITIES.map((capability) => {
              const Icon = capability.icon;
              const statuses = data.capabilities!.filter((item) => capability.contextIds.includes(item.id)).map((item) => item.status);
              const status = statuses.includes("unavailable") && statuses.every((item) => item === "unavailable") ? "unavailable" : statuses.length ? "partial" : "available";
              return (
                <button
                  key={capability.key}
                  type="button"
                  onClick={() => setOpen(capability)}
                  className="group flex h-full flex-col rounded-xl border border-slate-200 bg-white p-4 text-left shadow-sm transition hover:-translate-y-0.5 hover:border-indigo-200 hover:shadow-md focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400"
                >
                  <span className="flex items-start justify-between gap-3">
                    <span className="grid size-9 place-items-center rounded-lg bg-indigo-50 text-indigo-600"><Icon size={19} /></span>
                    <StatusBadge status={status} />
                  </span>
                  <span className="mt-3 text-sm font-semibold text-slate-800">{capability.title}</span>
                  <span className="mt-1 flex-1 text-xs leading-5 text-slate-500">{capability.summary}</span>
                  <span className="mt-3 inline-flex items-center gap-1 text-[11px] font-medium text-indigo-700">Explore details <HiArrowRight size={12} /></span>
                </button>
              );
            })}
          </div>
        </section>
      )}

      <section aria-labelledby="glossary-heading">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h3 id="glossary-heading" className="text-sm font-semibold text-slate-800">Business glossary</h3>
            <p className="mt-1 text-xs text-slate-500">
              {data.glossary ? "Approved meanings, with terms that need approval or have no data marked." : "Definitions from this domain's glossary; mapping shows whether the term points at a column in the current source."}
            </p>
          </div>
          <Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search terms" aria-label="Search glossary terms" />
        </div>
        {shownTerms.length ? (
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            {shownTerms.map((term) => (
              <article key={term.term} className="rounded-xl border border-slate-200 p-3">
                <div className="flex items-start justify-between gap-2">
                  <h4 className="text-xs font-semibold text-slate-800">{term.term}</h4>
                  <StatusBadge status={term.status} />
                </div>
                <p className="mt-1.5 text-xs leading-5 text-slate-600">{term.definition}</p>
                {term.source && <p className="mt-2 text-[10px] text-slate-500">Source: {term.source}</p>}
              </article>
            ))}
          </div>
        ) : (
          <p className="mt-3 flex items-center gap-2 rounded-lg bg-slate-50 p-3 text-xs text-slate-500"><HiOutlineInformationCircle size={15} /> No glossary terms match.</p>
        )}
      </section>

      {open && <CapabilityDialog capability={open} data={data} onClose={() => setOpen(null)} />}
    </div>
  );
}
