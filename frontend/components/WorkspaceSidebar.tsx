"use client";

import Link from "next/link";
import {
  Activity,
  BarChart3,
  ClipboardList,
  Grid2X2,
  LayoutDashboard,
  Percent,
} from "lucide-react";

export type WorkspaceView = "overview" | "heatmap" | "loss" | "audit" | "report";

const views: { id: Exclude<WorkspaceView, "report">; label: string; icon: typeof LayoutDashboard }[] = [
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "heatmap", label: "Heatmap", icon: Grid2X2 },
  { id: "loss", label: "Loss ratio", icon: Percent },
  { id: "audit", label: "Audit trails", icon: ClipboardList },
];

export function WorkspaceSidebar({
  activeView,
  domain,
  source,
  refreshNote,
  onSelectView,
}: {
  activeView: WorkspaceView;
  domain?: string;
  source?: string;
  refreshNote?: string;
  onSelectView?: (view: Exclude<WorkspaceView, "report">) => void;
}) {
  return (
    <aside className="dashboard-sidebar" aria-label="Dashboard views">
      <div className="dashboard-sidebar__brand">
        <span className="dashboard-sidebar__mark"><Activity size={16} /></span>
        <span>Workspace</span>
      </div>
      {domain ? <p className="dashboard-sidebar__section">{domain.toUpperCase()}</p> : <p className="dashboard-sidebar__section">ANALYTICS</p>}
      <nav className="dashboard-tabs" aria-label="Analytics views">
        {views.map(({ id, label, icon: Icon }) => {
          const className = `dashboard-tab${activeView === id ? " dashboard-tab--active" : ""}`;
          const content = (
            <>
              <Icon size={16} aria-hidden="true" />
              <span>{id === "loss" && domain?.toLowerCase() === "healthcare" ? "Claim approval" : label}</span>
            </>
          );
          // Outside the dashboard (e.g. on Report analysis) there is no in-page handler,
          // so the views link back to the dashboard with the chosen view selected.
          return onSelectView ? (
            <button
              key={id}
              type="button"
              onClick={() => onSelectView(id)}
              className={className}
              aria-current={activeView === id ? "page" : undefined}
            >
              {content}
            </button>
          ) : (
            <Link key={id} href={`/dashboard?view=${id}`} className={`${className} dashboard-tab--link`}>
              {content}
            </Link>
          );
        })}
        <Link
          href="/reports"
          className={`dashboard-tab dashboard-tab--link${activeView === "report" ? " dashboard-tab--active" : ""}`}
          aria-current={activeView === "report" ? "page" : undefined}
        >
          <BarChart3 size={16} aria-hidden="true" />
          <span>Report analysis</span>
        </Link>
      </nav>
      <div className="dashboard-sidebar__footer">
        <span className="dashboard-sidebar__status" aria-hidden="true" />
        <span>{source || "Live"} data</span>
        {refreshNote ? <small>{refreshNote}</small> : null}
      </div>
    </aside>
  );
}
