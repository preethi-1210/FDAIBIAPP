"use client";

import { useState } from "react";
import {
  Activity,
  ArrowDown,
  BrainCircuit,
  Database,
  GitBranch,
  LockKeyhole,
  Network,
  Sparkles,
} from "lucide-react";

export type BusinessContext = {
  user: {
    username: string;
    role: string;
    domain: string;
    domain_label: string;
    permitted_domains: string[];
    organization: string | null;
  };
  source: {
    warehouse: string;
    configured: boolean;
    catalog: string | null;
    schema: string | null;
    metadata_available: boolean;
  };
  tables: { name: string; columns: string[] }[];
  relationships: { source: string; target: string; keys: string[]; evidence: string }[];
  glossary: { term: string; definition: string; table: string; column: string | null; mapping_status: string }[];
  metrics: string[];
  rules: string[];
  synchronized_at: string | null;
};

const LEVELS = [
  { title: "Data sources", icon: Database },
  { title: "Business semantics", icon: Network },
  { title: "Foresight", icon: BrainCircuit },
  { title: "User & organization", icon: LockKeyhole },
  { title: "AI analytics engine", icon: Activity },
  { title: "Insights & evidence", icon: Sparkles },
];

const SAMPLE_CONCEPTS: Record<string, string[]> = {
  banking: ["Accounts", "Transactions", "Branches"],
  finance: ["Financial accounts", "Budgets", "Revenue"],
  insurance: ["Policies", "Claims", "Premiums"],
  healthcare: ["Patients", "Encounters", "Claims", "Quality metrics"],
  retail: ["Orders", "Products", "Stores"],
};

export function ContextArchitecture({
  context,
  sampleMode = false,
  domainLabel,
}: {
  context?: BusinessContext;
  sampleMode?: boolean;
  domainLabel: string;
}) {
  const [selectedLevel, setSelectedLevel] = useState(2);
  const sampleDomain = context?.user.domain ?? domainLabel.toLowerCase();
  const sampleConcepts = SAMPLE_CONCEPTS[sampleDomain] ?? SAMPLE_CONCEPTS.retail;
  const sourceNames = context?.tables.map((table) => table.name) ?? [];
  const glossaryTerms = context?.glossary.map((term) => term.term) ?? [];
  const relationshipNames = context?.relationships.map(
    (relationship) => `${relationship.source} → ${relationship.target}`,
  ) ?? [];
  const detailItems = [
    context
      ? sourceNames.length
        ? [`${context.source.warehouse}${context.source.catalog ? ` · ${context.source.catalog}` : ""}${context.source.schema ? ` · ${context.source.schema}` : ""}`, ...sourceNames]
        : ["No source schema metadata is currently available."]
      : ["Sample source metadata", ...sampleConcepts],
    context
      ? glossaryTerms.length
        ? glossaryTerms
        : ["No domain glossary terms are configured."]
      : ["Illustrative business concepts", ...sampleConcepts],
    context
      ? relationshipNames.length
        ? relationshipNames
        : ["No verified relationships are available in the current metadata."]
      : ["Business definitions", "Relationship evidence", "Access-aware context retrieval"],
    context
      ? [
          `${context.user.username} · ${context.user.role}`,
          `${context.user.domain_label} domain access`,
          context.user.organization ? `Organization: ${context.user.organization}` : "Organization scope is not configured.",
        ]
      : [`Selected demo domain: ${domainLabel}`, "Sample preview only; sign in to see your authorized context."],
    ["Intent interpretation", "Context retrieval", "Guarded query planning", "Validated execution"],
    ["Answers and supporting evidence", "Source lineage where available", "No query is run by this diagram"],
  ];

  return (
    <div className="grid gap-5 lg:grid-cols-[1.1fr_.9fr]">
      <div className="space-y-2.5" aria-label="Interactive context architecture">
        {LEVELS.map(({ title, icon: Icon }, index) => (
          <div key={title} className="relative pl-6">
            {index < LEVELS.length - 1 && (
              <span aria-hidden="true" className="absolute bottom-[-11px] left-[9px] top-7 w-px bg-gradient-to-b from-indigo-400/55 to-cyan-300/20" />
            )}
            <button
              type="button"
              onClick={() => setSelectedLevel(index)}
              aria-pressed={selectedLevel === index}
              className={`relative flex w-full items-center gap-3 rounded-xl border p-3 text-left transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-cyan-300 ${
                selectedLevel === index
                  ? "border-indigo-300/40 bg-indigo-300/[0.11]"
                  : "border-white/[0.08] bg-white/[0.025] hover:border-white/20"
              }`}
            >
              <span className={`grid size-8 shrink-0 place-items-center rounded-lg ${index === 2 ? "bg-indigo-300/15 text-indigo-200" : "bg-white/[0.06] text-slate-400"}`}>
                <Icon size={16} />
              </span>
              <span className="min-w-0 flex-1">
                <span className={`block text-xs font-semibold ${index === 2 ? "text-indigo-100" : "text-slate-200"}`}>
                  {index + 1}. {title}
                </span>
                <span className="mt-0.5 block truncate text-[10px] text-slate-500">
                  {index === 2 ? `${domainLabel} definitions, rules & relationships` : index === 3 && context ? `${context.user.role} · ${domainLabel}` : "Select to inspect this layer"}
                </span>
              </span>
              {selectedLevel === index && <ArrowDown size={14} className="shrink-0 rotate-[-90deg] text-cyan-200" />}
            </button>
          </div>
        ))}
      </div>

      <section
        className="min-h-64 rounded-2xl border border-indigo-200/15 bg-gradient-to-br from-indigo-300/[0.075] to-cyan-300/[0.025] p-4 sm:p-5"
        aria-live="polite"
        aria-labelledby="context-layer-detail"
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-[10px] font-medium uppercase tracking-[0.17em] text-indigo-200/70">
              Layer {selectedLevel + 1}
            </p>
            <h3 id="context-layer-detail" className="mt-1 text-sm font-semibold text-white">
              {LEVELS[selectedLevel].title}
            </h3>
          </div>
          {selectedLevel === 2 && <GitBranch size={18} className="text-indigo-200" />}
        </div>
        <p className="mt-2 text-xs leading-5 text-slate-400">
          {selectedLevel === 2
            ? "Connects business meaning to verified source metadata, relationships, and rules before analytics use them."
            : `The ${LEVELS[selectedLevel].title.toLowerCase()} layer contributes to the same governed analytics flow.`}
        </p>
        {sampleMode && (
          <p className="mt-3 rounded-lg border border-amber-200/15 bg-amber-200/[0.06] px-3 py-2 text-[10px] leading-4 text-amber-100/80">
            Illustrative demo context. These examples are not live business data or a completed query.
          </p>
        )}
        <ul className="mt-4 space-y-2">
          {detailItems[selectedLevel].map((item) => (
            <li key={item} className="flex gap-2 text-xs leading-5 text-slate-300">
              <span aria-hidden="true" className="mt-2 size-1 shrink-0 rounded-full bg-cyan-300" />
              <span className="break-words">{item}</span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
