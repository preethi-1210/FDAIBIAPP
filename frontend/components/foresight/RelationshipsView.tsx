import { HiArrowLongRight, HiOutlineInformationCircle } from "react-icons/hi2";
import { StatusBadge } from "@/components/foresight/StatusBadge";
import type { ForesightContext } from "@/lib/foresight";

export function RelationshipsView({ data }: { data: ForesightContext }) {
  if (!data.relationships.length) {
    return <p className="flex items-center gap-2 rounded-lg bg-slate-50 p-3 text-xs text-slate-500"><HiOutlineInformationCircle size={15} /> No verified relationships are recorded for this domain.</p>;
  }
  return (
    <div className="space-y-3">
      <p className="text-xs text-slate-500">
        Each relationship was checked against the data: the parent key is unique, and child rows without a parent are counted. Unity Catalog declares no keys, so none of these is a database constraint.
      </p>
      <div className="grid gap-3 md:grid-cols-2">
        {data.relationships.map((rel) => (
          <article key={rel.join} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex items-start justify-between gap-2">
              <h4 className="flex flex-wrap items-center gap-1.5 text-sm font-semibold text-slate-800">
                {rel.child_label} <HiArrowLongRight className="text-indigo-500" size={18} /> {rel.parent_label}
              </h4>
              <StatusBadge status={rel.status} />
            </div>
            <p className="mt-1.5 text-xs leading-5 text-slate-600">{rel.explanation}</p>
            <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[11px]">
              <dt className="text-slate-500">Join</dt><dd className="m-0 break-all font-mono text-slate-700">{rel.join}</dd>
              <dt className="text-slate-500">Cardinality</dt><dd className="m-0 text-slate-700">{rel.cardinality}</dd>
              <dt className="text-slate-500">Unmatched</dt><dd className="m-0 text-slate-700">{rel.unmatched_children === 0 ? "None: every row has a match" : `${rel.unmatched_children.toLocaleString()} ${rel.child_label.toLowerCase()} rows have no match`}</dd>
              <dt className="text-slate-500">Verified</dt><dd className="m-0 text-slate-700">{rel.verified_on}</dd>
            </dl>
          </article>
        ))}
      </div>
    </div>
  );
}
