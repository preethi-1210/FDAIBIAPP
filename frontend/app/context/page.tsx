"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  AlertCircle,
  ArrowRight,
  BookOpen,
  Database,
  GitBranch,
  LoaderCircle,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  TableProperties,
  X,
} from "lucide-react";
import { Shell } from "@/components/Shell";
import { BusinessContext, ContextArchitecture } from "@/components/ContextArchitecture";
import { api } from "@/lib/api";

type Section = "overview" | "entities" | "glossary" | "governance" | "access";

const OVERVIEW_CARDS: { title: string; section: Section; value: (context: BusinessContext) => string; icon: typeof Database }[] = [
  { title: "Source tables", section: "entities", value: (context) => String(context.tables.length), icon: TableProperties },
  { title: "Verified relationships", section: "entities", value: (context) => String(context.relationships.length), icon: GitBranch },
  { title: "Glossary terms", section: "glossary", value: (context) => String(context.glossary.length), icon: BookOpen },
  { title: "Configured metrics", section: "governance", value: (context) => String(context.metrics.length), icon: Sparkles },
  { title: "Configured rules", section: "governance", value: (context) => String(context.rules.length), icon: ShieldCheck },
  { title: "Context coverage", section: "overview", value: () => "Not measured", icon: Database },
];

export default function ContextIntelligencePage() {
  const [context, setContext] = useState<BusinessContext | null>(null);
  const [domain, setDomain] = useState("");
  const [section, setSection] = useState<Section>("overview");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedTable, setSelectedTable] = useState<string | null>(null);
  const [inspectingCard, setInspectingCard] = useState<string | null>(null);

  async function loadContext(selectedDomain?: string) {
    setLoading(true);
    setError("");
    try {
      const query = selectedDomain ? `?domain=${encodeURIComponent(selectedDomain)}` : "";
      const result = await api<BusinessContext>(`/context${query}`);
      setContext(result);
      setDomain(result.user.domain);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load authorized business context.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadContext();
  }, []);

  useEffect(() => {
    if (!inspectingCard) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setInspectingCard(null);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [inspectingCard]);

  const filteredTables = useMemo(
    () => context?.tables.filter((table) => table.name.toLowerCase().includes(search.toLowerCase())) ?? [],
    [context, search],
  );
  const activeTable = context?.tables.find((table) => table.name === selectedTable);

  return (
    <Shell>
      <div className="dashboard-page space-y-6">
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="m-0 text-3xl font-semibold tracking-tight text-slate-900">Insight Intelligence</h1>
            <p className="mt-2 max-w-3xl text-sm text-slate-600">
              The business knowledge layer powering trusted AI analytics across domains.
            </p>
          </div>
          <button
            type="button"
            disabled={loading}
            onClick={() => void loadContext(domain || undefined)}
            className="inline-flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-60"
          >
            <RefreshCw size={15} className={loading ? "animate-spin" : ""} /> Refresh metadata
          </button>
        </header>

        {context && (
          <section className="flex flex-wrap items-end justify-between gap-4 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex flex-wrap gap-x-8 gap-y-3">
              <div>
                <p className="text-[11px] font-medium uppercase tracking-wide text-slate-500">Signed in as</p>
                <p className="mt-1 text-sm font-semibold text-slate-800">{context.user.username} · {context.user.role}</p>
              </div>
              <div>
                <p className="text-[11px] font-medium uppercase tracking-wide text-slate-500">
                  Active source
                </p>
                <p className="mt-1 text-sm font-semibold text-slate-800">
                  {context.source.warehouse}{context.source.catalog ? ` · ${context.source.catalog}` : ""}
                  {context.source.schema ? ` · ${context.source.schema}` : ""}
                </p>
              </div>
              <div>
                <p className="text-[11px] font-medium uppercase tracking-wide text-slate-500">Metadata status</p>
                <p className="mt-1 text-sm font-semibold text-slate-800">
                  {context.source.metadata_available ? "Available" : "Unavailable"}
                  {context.synchronized_at ? ` · ${context.synchronized_at}` : " · No sync timestamp recorded"}
                </p>
              </div>
            </div>
            {context.user.permitted_domains.length > 1 ? (
              <label className="block text-xs font-medium text-slate-600">
                Authorized domain
                <select
                  value={domain}
                  onChange={(event) => {
                    setSelectedTable(null);
                    void loadContext(event.target.value);
                  }}
                  className="mt-1 block min-w-40 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800"
                >
                  {context.user.permitted_domains.map((item) => (
                    <option key={item} value={item}>{item.charAt(0).toUpperCase() + item.slice(1)}</option>
                  ))}
                </select>
              </label>
            ) : (
              <div>
                <p className="text-[11px] font-medium uppercase tracking-wide text-slate-500">Authorized domain</p>
                <p className="mt-1 text-sm font-semibold text-slate-800">{context.user.domain_label}</p>
              </div>
            )}
          </section>
        )}

        {error && (
          <div role="alert" className="flex items-start gap-3 rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">
            <AlertCircle className="mt-0.5 shrink-0" size={17} />
            <div className="flex-1">{error}</div>
            <button type="button" onClick={() => void loadContext(domain || undefined)} className="font-semibold underline">Retry</button>
          </div>
        )}

        {loading && !context ? (
          <div className="dashboard-loading" role="status">
            <div><LoaderCircle className="chat-spin" size={24} /><strong>Loading authorized context</strong><span>Checking your domain and source metadata…</span></div>
          </div>
        ) : context ? (
          <>
            <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" aria-label="Context overview">
              {OVERVIEW_CARDS.map(({ title, section: target, value, icon: Icon }) => (
                <button
                  type="button"
                  key={title}
                  onClick={() => {
                    setSection(target);
                    setInspectingCard(title);
                  }}
                  className="group rounded-xl border border-slate-200 bg-white p-4 text-left shadow-sm transition hover:-translate-y-0.5 hover:border-indigo-200 hover:shadow-md"
                >
                  <span className="flex items-center justify-between gap-3 text-xs font-medium text-slate-500">
                    {title}<Icon size={16} className="text-indigo-500" />
                  </span>
                  <span className="mt-2 block text-xl font-semibold text-slate-800">{value(context)}</span>
                  <span className="mt-1 inline-flex items-center gap-1 text-[11px] text-slate-500 group-hover:text-indigo-700">
                    Inspect <ArrowRight size={12} />
                  </span>
                </button>
              ))}
            </section>

            <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-6">
              <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
                <div>
                  <h2 className="text-base font-semibold text-slate-800">Context architecture</h2>
                  <p className="mt-1 text-xs text-slate-500">Interactive map of the layers applied to {context.user.domain_label} analytics.</p>
                </div>
                <span className="rounded-full border border-slate-200 bg-slate-50 px-3 py-1.5 text-[10px] text-slate-600">
                  {context.source.configured ? "Source configured" : "Source not configured"}
                </span>
              </div>
              <div className="rounded-2xl bg-slate-950 p-4 sm:p-5">
                <ContextArchitecture context={context} domainLabel={context.user.domain_label} />
              </div>
            </section>

            <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-6">
              <nav className="mb-5 flex flex-wrap gap-2" aria-label="Context sections">
                {([
                  ["overview", "Overview"],
                  ["entities", "Entities & relationships"],
                  ["glossary", "Business glossary"],
                  ["governance", "Metrics & rules"],
                  ["access", "User & sources"],
                ] as [Section, string][]).map(([id, label]) => (
                  <button key={id} type="button" onClick={() => setSection(id)} aria-pressed={section === id} className={`rounded-lg px-3 py-2 text-xs font-medium capitalize ${section === id ? "bg-indigo-600 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`}>
                    {label}
                  </button>
                ))}
              </nav>

              {(section === "overview" || section === "entities") && (
                <div>
                  <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
                    <div>
                      <h3 className="text-sm font-semibold text-slate-800">Source entities and verified relationships</h3>
                      <p className="mt-1 text-xs text-slate-500">Only source tables and database foreign keys returned by the backend are shown.</p>
                    </div>
                    <input value={search} onChange={(event) => setSearch(event.target.value)} aria-label="Search source tables" placeholder="Search tables" className="rounded-lg border border-slate-300 px-3 py-2 text-xs text-slate-800 outline-none focus:border-indigo-400" />
                  </div>
                  <div className="grid gap-4 lg:grid-cols-2">
                    <div className="space-y-2">
                      {filteredTables.map((table) => (
                        <button key={table.name} type="button" onClick={() => setSelectedTable(table.name)} className={`flex w-full items-center justify-between gap-3 rounded-lg border px-3 py-2.5 text-left text-xs ${selectedTable === table.name ? "border-indigo-300 bg-indigo-50" : "border-slate-200 hover:bg-slate-50"}`}>
                          <span className="font-medium text-slate-800">{table.name}</span><span className="text-slate-500">{table.columns.length} visible columns</span>
                        </button>
                      ))}
                      {!filteredTables.length && <p className="rounded-lg bg-slate-50 p-3 text-xs text-slate-500">No source table metadata is available for this domain.</p>}
                    </div>
                    <div className="rounded-xl bg-slate-50 p-4">
                      {activeTable ? (
                        <>
                          <h4 className="text-sm font-semibold text-slate-800">{activeTable.name}</h4>
                          <p className="mt-1 text-[11px] text-slate-500">Columns with sensitive identifiers are omitted.</p>
                          <div className="mt-3 flex flex-wrap gap-1.5">
                            {activeTable.columns.map((column) => <span key={column} className="rounded-md border border-slate-200 bg-white px-2 py-1 text-[10px] text-slate-600">{column}</span>)}
                            {!activeTable.columns.length && <span className="text-xs text-slate-500">No non-sensitive columns available.</span>}
                          </div>
                          <h5 className="mb-2 mt-5 text-xs font-semibold text-slate-700">Verified relationships</h5>
                          {context.relationships.filter((relationship) => relationship.source === activeTable.name || relationship.target === activeTable.name).map((relationship) => (
                            <p key={`${relationship.source}-${relationship.target}`} className="mb-1 text-xs text-slate-600">
                              {relationship.source} → {relationship.target}{relationship.keys.length ? ` · ${relationship.keys.join(", ")}` : ""}
                            </p>
                          ))}
                          {!context.relationships.some((relationship) => relationship.source === activeTable.name || relationship.target === activeTable.name) && <p className="text-xs text-slate-500">No verified relationship metadata for this table.</p>}
                        </>
                      ) : <p className="text-xs text-slate-500">Select a table to inspect its available metadata.</p>}
                    </div>
                  </div>
                </div>
              )}

              {section === "glossary" && (
                <div>
                  <h3 className="mb-1 text-sm font-semibold text-slate-800">Business glossary</h3>
                  <p className="mb-4 text-xs text-slate-500">Definitions are sourced from this application&apos;s domain glossary.</p>
                  {context.glossary.length ? (
                    <div className="grid gap-3 md:grid-cols-2">
                      {context.glossary.map((term) => (
                        <article key={`${term.term}-${term.table}`} className="rounded-xl border border-slate-200 p-4">
                          <h4 className="text-sm font-semibold text-slate-800">{term.term}</h4>
                          <p className="mt-2 text-xs leading-5 text-slate-600">{term.definition}</p>
                          <p className="mt-3 text-[10px] text-slate-500">Source: {term.table}{term.column ? ` · ${term.column}` : ""} · {term.mapping_status}</p>
                        </article>
                      ))}
                    </div>
                  ) : <EmptyState text="No glossary definitions are configured for this domain yet." />}
                </div>
              )}

              {section === "governance" && (
                <div className="grid gap-5 md:grid-cols-2">
                  <div>
                    <h3 className="text-sm font-semibold text-slate-800">Metrics & KPIs</h3>
                    {context.metrics.length ? <ul className="mt-3 list-inside list-disc text-xs text-slate-600">{context.metrics.map((item) => <li key={item}>{item}</li>)}</ul> : <EmptyState text="No approved metric definitions are stored in the backend yet." />}
                  </div>
                  <div>
                    <h3 className="text-sm font-semibold text-slate-800">Business rules</h3>
                    {context.rules.length ? <ul className="mt-3 list-inside list-disc text-xs text-slate-600">{context.rules.map((item) => <li key={item}>{item}</li>)}</ul> : <EmptyState text="No configurable business rules are stored in the backend yet." />}
                  </div>
                </div>
              )}

              {section === "access" && (
                <div className="grid gap-5 md:grid-cols-2">
                  <div>
                    <h3 className="text-sm font-semibold text-slate-800">Authenticated user context</h3>
                    <dl className="mt-3 space-y-2 text-xs">
                      <ContextFact label="Identity" value={context.user.username} />
                      <ContextFact label="Role" value={context.user.role} />
                      <ContextFact label="Permitted domains" value={context.user.permitted_domains.join(", ")} />
                      <ContextFact label="Organization" value={context.user.organization ?? "Not configured"} />
                    </dl>
                  </div>
                  <div>
                    <h3 className="text-sm font-semibold text-slate-800">Data source metadata</h3>
                    <dl className="mt-3 space-y-2 text-xs">
                      <ContextFact label="Warehouse" value={context.source.warehouse} />
                      <ContextFact label="Configuration" value={context.source.configured ? "Configured" : "Not configured"} />
                      <ContextFact label="Catalog" value={context.source.catalog ?? "Not mapped"} />
                      <ContextFact label="Schema" value={context.source.schema ?? "Not available"} />
                    </dl>
                  </div>
                </div>
              )}
            </section>
            <p className="text-xs text-slate-500">
              Management, organization-specific definitions, and synchronization history are not configured in the current backend.
              <Link href="/dashboard" className="ml-1 inline-flex items-center gap-1 font-medium text-indigo-700 hover:underline">Return to Analyze <ArrowRight size={12} /></Link>
            </p>
          </>
        ) : !error ? (
          <div className="dashboard-loading" role="status"><div><LoaderCircle className="chat-spin" size={24} /><strong>Loading business context</strong></div></div>
        ) : null}
      </div>
      {context && inspectingCard && (
        <div
          className="fixed inset-0 z-[100] flex items-center justify-center bg-slate-950/35 p-4 backdrop-blur-[2px]"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setInspectingCard(null);
          }}
        >
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="context-inspect-title"
            className="max-h-[min(80vh,36rem)] w-full max-w-lg overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl"
          >
            <header className="flex items-start justify-between gap-4 border-b border-slate-100 px-5 py-4">
              <div>
                <p className="text-[10px] font-semibold uppercase tracking-wider text-indigo-600">{context.user.domain_label} context</p>
                <h2 id="context-inspect-title" className="mt-1 text-base font-semibold text-slate-900">{inspectingCard}</h2>
              </div>
              <button
                type="button"
                onClick={() => setInspectingCard(null)}
                aria-label="Close details"
                className="grid size-8 shrink-0 place-items-center rounded-lg text-slate-500 hover:bg-slate-100 hover:text-slate-800"
              >
                <X size={16} />
              </button>
            </header>
            <div className="max-h-[calc(min(80vh,36rem)-5rem)] space-y-2 overflow-y-auto p-4">
              {getOverviewDetails(inspectingCard, context).map(({ heading, body }) => (
                <article key={heading} className="rounded-xl border border-slate-100 bg-slate-50/70 p-3">
                  <h3 className="break-words text-xs font-semibold text-slate-800">{heading}</h3>
                  <p className="mt-1 whitespace-pre-wrap break-words text-xs leading-5 text-slate-600">{body}</p>
                </article>
              ))}
            </div>
          </section>
        </div>
      )}
    </Shell>
  );
}

function getOverviewDetails(title: string, context: BusinessContext) {
  switch (title) {
    case "Source tables":
      return context.tables.length
        ? context.tables.map((table) => ({
            heading: table.name,
            body: `${table.columns.length} visible, non-sensitive columns`,
          }))
        : [{ heading: "No source tables available", body: "No source table metadata was returned for this authorized domain." }];
    case "Verified relationships":
      return context.relationships.length
        ? context.relationships.map((relationship) => ({
            heading: `${relationship.source} → ${relationship.target}`,
            body: [
              relationship.keys.length ? `Keys: ${relationship.keys.join(", ")}` : "",
              relationship.evidence ? `Evidence: ${relationship.evidence}` : "",
            ].filter(Boolean).join("\n") || "Verified relationship metadata",
          }))
        : [{ heading: "No verified relationships", body: "No verified relationship metadata is available for this domain." }];
    case "Glossary terms":
      return context.glossary.length
        ? context.glossary.map((term) => ({
            heading: term.term,
            body: `${term.definition}\nSource: ${term.table}${term.column ? ` · ${term.column}` : ""} · ${term.mapping_status}`,
          }))
        : [{ heading: "No glossary terms", body: "No business glossary definitions are configured for this domain." }];
    case "Configured metrics":
      return context.metrics.length
        ? context.metrics.map((metric, index) => ({ heading: `Metric ${index + 1}`, body: metric }))
        : [{ heading: "No configured metrics", body: "No approved metric definitions are stored for this domain." }];
    case "Configured rules":
      return context.rules.length
        ? context.rules.map((rule, index) => ({ heading: `Rule ${index + 1}`, body: rule }))
        : [{ heading: "No configured rules", body: "No business rules are configured for this domain." }];
    default:
      return [
        { heading: "Coverage is not measured", body: "The backend does not currently calculate a context coverage percentage." },
        { heading: "Available metadata", body: `${context.tables.length} source tables · ${context.relationships.length} verified relationships · ${context.glossary.length} glossary terms · ${context.metrics.length} metrics · ${context.rules.length} rules` },
        { heading: "Metadata status", body: context.source.metadata_available ? "Source metadata is available." : "Source metadata is unavailable." },
      ];
  }
}

function ContextFact({ label, value }: { label: string; value: string }) {
  return <div className="flex flex-wrap justify-between gap-2 border-b border-slate-100 pb-2"><dt className="text-slate-500">{label}</dt><dd className="m-0 font-medium text-slate-700">{value}</dd></div>;
}

function EmptyState({ text }: { text: string }) {
  return <p className="mt-3 rounded-lg bg-slate-50 p-3 text-xs leading-5 text-slate-500">{text}</p>;
}
