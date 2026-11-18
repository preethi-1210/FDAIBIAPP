"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { HiArrowRight, HiArrowPath, HiOutlineExclamationCircle } from "react-icons/hi2";
import { LuLoaderCircle } from "react-icons/lu";
import { Shell } from "@/components/Shell";
import { BusinessContext } from "@/components/ContextArchitecture";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { OverviewView } from "@/components/foresight/OverviewView";
import { EntitiesView } from "@/components/foresight/EntitiesView";
import { RelationshipsView } from "@/components/foresight/RelationshipsView";
import { MetricsView } from "@/components/foresight/MetricsView";
import { RulesView } from "@/components/foresight/RulesView";
import { api } from "@/lib/api";
import { total, type ForesightContext } from "@/lib/foresight";

export default function ContextIntelligencePage() {
  const [context, setContext] = useState<BusinessContext | null>(null);
  const [foresight, setForesight] = useState<ForesightContext | null>(null);
  const [domain, setDomain] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function loadContext(selectedDomain?: string) {
    setLoading(true);
    setError("");
    try {
      const query = selectedDomain ? `?domain=${encodeURIComponent(selectedDomain)}` : "";
      // Same authorization for both: the user's permitted domains and Healthcare tenant.
      const [result, details] = await Promise.all([
        api<BusinessContext>(`/context${query}`),
        api<ForesightContext>(`/context/foresight${query}`),
      ]);
      setContext(result);
      setForesight(details);
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
            <HiArrowPath size={15} className={loading ? "animate-spin" : ""} /> Reload
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
                  {foresight?.overview.synchronized_at ? ` · loaded ${new Date(foresight.overview.synchronized_at).toLocaleString()}` : " · No load time recorded"}
                </p>
              </div>
            </div>
            {context.user.permitted_domains.length > 1 ? (
              <label className="block text-xs font-medium text-slate-600">
                Authorized domain
                <select
                  value={domain}
                  onChange={(event) => void loadContext(event.target.value)}
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
            <HiOutlineExclamationCircle className="mt-0.5 shrink-0" size={17} />
            <div className="flex-1">{error}</div>
            <button type="button" onClick={() => void loadContext(domain || undefined)} className="font-semibold underline">Retry</button>
          </div>
        )}

        {loading && !context ? (
          <div className="dashboard-loading" role="status">
            <div><LuLoaderCircle className="chat-spin" size={24} /><strong>Loading authorized context</strong><span>Checking your domain and source metadata…</span></div>
          </div>
        ) : context && foresight?.available ? (
          <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-6">
            <Tabs key={foresight.domain} defaultValue="overview">
              <TabsList aria-label="Foresight sections">
                <TabsTrigger value="overview">Overview</TabsTrigger>
                <TabsTrigger value="entities">Entities <Count value={foresight.overview.entities} /></TabsTrigger>
                <TabsTrigger value="relationships">Relationships <Count value={foresight.relationships.length} /></TabsTrigger>
                <TabsTrigger value="metrics">Metrics <Count value={foresight.metrics.length} /></TabsTrigger>
                <TabsTrigger value="rules">Rules <Count value={total(foresight.overview.rules)} /></TabsTrigger>
              </TabsList>
              <TabsContent value="overview"><OverviewView data={foresight} legacyGlossary={context.glossary} /></TabsContent>
              <TabsContent value="entities"><EntitiesView data={foresight} /></TabsContent>
              <TabsContent value="relationships"><RelationshipsView data={foresight} /></TabsContent>
              <TabsContent value="metrics"><MetricsView data={foresight} /></TabsContent>
              <TabsContent value="rules"><RulesView data={foresight} /></TabsContent>
            </Tabs>
          </section>
        ) : context && foresight && !foresight.available ? (
          <p className="rounded-xl bg-slate-50 p-4 text-sm text-slate-600">No governed business context is defined for this domain yet.</p>
        ) : !error ? (
          <div className="dashboard-loading" role="status"><div><LuLoaderCircle className="chat-spin" size={24} /><strong>Loading business context</strong></div></div>
        ) : null}

        <p className="text-xs text-slate-500">
          Definitions are read-only here; changes go through the reviewed registries.
          <Link href="/dashboard" className="ml-1 inline-flex items-center gap-1 font-medium text-indigo-700 hover:underline">Return to Analyze <HiArrowRight size={12} /></Link>
        </p>
      </div>
    </Shell>
  );
}

function Count({ value }: { value: number }) {
  return <span className="rounded-full bg-black/10 px-1.5 text-[10px] font-semibold">{value}</span>;
}
