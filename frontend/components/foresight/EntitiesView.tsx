"use client";

import { useState } from "react";
import { HiOutlineKey, HiOutlineLink, HiOutlineTableCells } from "react-icons/hi2";
import { Input } from "@/components/ui/input";
import { StatusBadge } from "@/components/foresight/StatusBadge";
import type { ForesightContext } from "@/lib/foresight";

export function EntitiesView({ data }: { data: ForesightContext }) {
  const [search, setSearch] = useState("");
  const [showAggregates, setShowAggregates] = useState(false);
  const [showTechnical, setShowTechnical] = useState(false);
  const [selected, setSelected] = useState<string | null>(data.entities.find((e) => e.kind === "entity")?.table ?? null);
  const query = search.toLowerCase();
  const entities = data.entities.filter(
    (entity) => (showAggregates || entity.kind === "entity") && `${entity.name} ${entity.table}`.toLowerCase().includes(query),
  );
  const active = data.entities.find((entity) => entity.table === selected);
  const metricNames = new Map(data.metrics.map((metric) => [metric.id, metric.name]));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-slate-500">Approved business entities from the {data.domain_label} metric registry, with columns from Unity Catalog. Sensitive columns are not listed.</p>
        <div className="flex flex-wrap items-center gap-3">
          {data.overview.aggregates > 0 && (
            <label className="flex items-center gap-1.5 text-xs text-slate-600"><input type="checkbox" checked={showAggregates} onChange={(e) => setShowAggregates(e.target.checked)} /> Approved aggregates</label>
          )}
          <Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search entities" aria-label="Search entities" />
        </div>
      </div>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <div className="space-y-2">
          {entities.map((entity) => (
            <button
              key={entity.table}
              type="button"
              onClick={() => setSelected(entity.table)}
              className={`w-full rounded-lg border px-3 py-2.5 text-left text-xs transition ${selected === entity.table ? "border-indigo-300 bg-indigo-50" : "border-slate-200 hover:bg-slate-50"}`}
            >
              <span className="flex items-center justify-between gap-2">
                <span className="font-semibold text-slate-800">{entity.name}</span>
                <StatusBadge status={entity.kind} />
              </span>
              <span className="mt-0.5 block text-slate-500">{entity.table} · {entity.columns.filter((c) => !c.technical).length} business columns · {entity.metrics.length} metrics</span>
            </button>
          ))}
          {!entities.length && <p className="rounded-lg bg-slate-50 p-3 text-xs text-slate-500">No entities match.</p>}
        </div>

        <div className="rounded-xl border border-slate-200 bg-slate-50/60 p-4">
          {active ? (
            <div className="space-y-4">
              <div>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h4 className="text-sm font-semibold text-slate-900">{active.name}</h4>
                  <StatusBadge status={active.kind} />
                </div>
                <p className="mt-1 text-xs text-slate-600">{active.description ?? "No business definition recorded yet."}</p>
                <p className="mt-1 text-[11px] text-slate-500">
                  {active.source}{active.rows !== null ? ` · ${active.rows.toLocaleString()} rows` : ""}{active.coverage ? ` · ${active.coverage}` : ""}
                </p>
                <p className="mt-1 text-[11px] text-slate-500">{active.validation}</p>
              </div>
              {!active.metadata_available && <p className="rounded-lg bg-amber-50 p-2.5 text-xs text-amber-800">Column metadata is not loaded for this table.</p>}
              <div>
                <h5 className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-slate-700"><HiOutlineTableCells size={14} /> Columns</h5>
                <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
                  <table className="w-full text-left text-[11px]">
                    <thead className="bg-slate-50 text-slate-500"><tr><th className="px-2.5 py-1.5 font-medium">Column</th><th className="px-2.5 py-1.5 font-medium">Type</th><th className="px-2.5 py-1.5 font-medium">Key</th><th className="px-2.5 py-1.5 font-medium">Meaning</th></tr></thead>
                    <tbody>
                      {active.columns.filter((c) => !c.technical).map((column) => (
                        <tr key={column.name} className="border-t border-slate-100 align-top">
                          <td className="px-2.5 py-1.5 font-medium text-slate-800">{column.name}</td>
                          <td className="px-2.5 py-1.5 text-slate-500">{column.type}</td>
                          <td className="px-2.5 py-1.5">{column.key ? <span className="inline-flex items-center gap-1 text-indigo-700"><HiOutlineKey size={11} />{column.key}</span> : ""}</td>
                          <td className="px-2.5 py-1.5 text-slate-600">{column.meaning ?? ""}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {active.hidden_sensitive_columns > 0 && <p className="mt-1.5 text-[11px] text-slate-500">{active.hidden_sensitive_columns} sensitive columns are not shown.</p>}
              </div>
              {active.relationships.length > 0 && (
                <div>
                  <h5 className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-slate-700"><HiOutlineLink size={14} /> Relationships</h5>
                  <ul className="space-y-1 text-[11px] text-slate-600">{active.relationships.map((join) => <li key={join}>{join}</li>)}</ul>
                </div>
              )}
              <div>
                <h5 className="mb-1.5 text-xs font-semibold text-slate-700">Approved metrics on this entity</h5>
                {active.metrics.length ? (
                  <div className="flex flex-wrap gap-1.5">{active.metrics.map((id) => <span key={id} className="rounded-md border border-slate-200 bg-white px-2 py-1 text-[11px] text-slate-600">{metricNames.get(id) ?? id}</span>)}</div>
                ) : <p className="text-[11px] text-slate-500">None.</p>}
              </div>
              {active.notes.length > 0 && (
                <div>
                  <h5 className="mb-1.5 text-xs font-semibold text-slate-700">Data notes</h5>
                  <ul className="list-inside list-disc space-y-1 text-[11px] text-slate-600">{active.notes.map((note) => <li key={note}>{note}</li>)}</ul>
                </div>
              )}
            </div>
          ) : <p className="text-xs text-slate-500">Select an entity to see its definition.</p>}
        </div>
      </div>

      <div className="rounded-xl border border-slate-200 p-3">
        <button type="button" onClick={() => setShowTechnical((value) => !value)} className="text-xs font-medium text-indigo-700 hover:underline">
          {showTechnical ? "Hide" : "Show"} {data.technical_tables.length} technical tables (pipeline, profiling, snapshots, summaries, modelled copies)
        </button>
        {showTechnical && (
          <div className="mt-3 flex flex-wrap gap-1.5">
            {data.technical_tables.map((table) => <span key={table.table} className="rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-[10px] text-slate-600">{table.table} · {table.kind}</span>)}
          </div>
        )}
      </div>
    </div>
  );
}
