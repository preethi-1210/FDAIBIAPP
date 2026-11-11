"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  Building2,
  CircleDollarSign,
  Clock3,
  Database,
  MoreHorizontal,
  RefreshCw,
  ShieldCheck,
  LoaderCircle,
  Percent,
  UsersRound,
} from "lucide-react";
import { Shell } from "@/components/Shell";
import { BusinessContext } from "@/components/ContextArchitecture";
import {
  ActivityBarChart,
  BarList,
  CHART_COLORS,
  REGION_CHART_COLORS,
  LineChart,
  PieChart,
} from "@/components/Charts";
import { RiskMatrixHeatMap } from "@/components/RiskMatrixHeatMap";
import { WorkspaceSidebar, type WorkspaceView } from "@/components/WorkspaceSidebar";
import { api } from "@/lib/api";

type Series = { label: string; value: number | null; events?: number | null };
type CategoryTrend = { label: string; category: string; value: number };
type MonthSeries = Series;
type HeatCell = { region: string; category: string; value: number };
type Audit = { audit_id: string; question: string; sql?: string; rows: number; guardrail_ok: boolean; user: string; created_at: string };
type LossRegion = { region: string; numerator: number; denominator: number; loss_ratio: number };
type DashDomain = "banking" | "finance" | "healthcare" | "insurance" | "retail";
type DashboardTab = Exclude<WorkspaceView, "report">;
type Dash = {
  domain: DashDomain;
  domain_label: string;
  metric_name: string;
  metric_description?: string;
  volume: number;
  active_cash_balance?: number | null;
  active_cash_balance_description?: string;
  events: number;
  event_label?: string;
  event_description?: string;
  trend_title?: string;
  trend_description?: string;
  trend_value_label?: string;
  trend_event_label?: string;
  live_title?: string;
  live_description?: string;
  live_section_title?: string;
  customers: number | null;
  customer_label?: string;
  churn_rate: number | null;
  churn_label?: string;
  claims: number;
  loss_ratio: number | null;
  loss_ratio_label: string;
  loss_ratio_description: string;
  loss_numerator_label: string;
  loss_denominator_label: string;
  guardrail_pass_rate: number;
  queries: number;
  by_region: Series[];
  by_category: Series[];
  category_trends?: CategoryTrend[];
  by_month: Series[];
  quality_metrics?: Series[];
  heatmap: HeatCell[];
  heatmap_metric_name?: string;
  loss_by_region: LossRegion[];
  audits: Audit[];
  live: { label: string; value: number; created_at: string; status?: string | null; description?: string | null }[];
  updated_at: string;
};

type DateRange = 1 | 3 | 6 | 12;
// Matches the API's dashboard cache lifetime; the refresh button bypasses the cache.
const AUTO_REFRESH_MS = 120_000;
type MixChartType = "donut" | "bar";

export default function DashboardPage() {
  const [data, setData] = useState<Dash | null>(null);
  const [businessContext, setBusinessContext] = useState<BusinessContext | null>(null);
  const [contextError, setContextError] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [dateRange, setDateRange] = useState<DateRange>(6);
  const [trendMetric, setTrendMetric] = useState<"value" | "events">("value");
  const [regionMetric, setRegionMetric] = useState<"value" | "events">("value");
  const [mixChartType, setMixChartType] = useState<MixChartType>("donut");
  const [showMixTrends, setShowMixTrends] = useState(true);
  const [showMixOptions, setShowMixOptions] = useState(false);
  const refreshTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const [tab, setTab] = useState<DashboardTab>("overview");
  const colors = CHART_COLORS;
  const lossRegions = data?.loss_by_region ?? [];
  const trendMonths = data ? selectMonths(data.by_month, dateRange) : [];
  const activityMonths = data ? selectMonths(data.by_month, dateRange) : [];
  const trendData: Series[] = trendMonths.map((month) => ({
    label: month.label,
    value: trendMetric === "events" ? month.events ?? null : month.value,
  }));
  const chartTrendData = data ? fillMissingMonths(trendData, dateRange) : [];
  const activityChartData: Series[] = data
    ? fillMissingMonths(activityMonths.map((month) => ({ label: month.label, value: month.events ?? null })), dateRange)
    : [];
  const categoryHistory = data ? selectMonths(data.category_trends || [], dateRange) : [];
  const mixCategories: Series[] = data && data.domain !== "banking" && categoryHistory.length
    ? sumByCategory(categoryHistory)
    : (data?.by_category || []).filter((row): row is { label: string; value: number } =>
      typeof row.value === "number" && Number.isFinite(row.value),
    );
  const recentStatuses = data ? summarizeStatuses(data.live) : [];
  const trendValueLabel = data?.trend_value_label || (data?.domain === "banking" ? "Total Deposits & Accounts" : data?.metric_name);
  const trendEventLabel = data?.trend_event_label || data?.event_label || "Events";
  const hasBusinessData = Boolean(
    data && (data.volume || data.events || data.by_month.length || data.by_region.length || data.live.length),
  );
  const latestMonth = trendMonths.length ? trendMonths[trendMonths.length - 1].label : null;

  async function load(force = false) {
    setLoading(true);
    try {
      setData(await api<Dash>(force ? "/dashboard?refresh=true" : "/dashboard"));
      setError("");
      if (!refreshTimer.current) {
        refreshTimer.current = setInterval(() => void load(), AUTO_REFRESH_MS);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Dashboard failed");
      if (refreshTimer.current) {
        clearInterval(refreshTimer.current);
        refreshTimer.current = null;
      }
    } finally {
      setLoading(false);
    }
  }

  async function loadBusinessContext() {
    try {
      setBusinessContext(await api<BusinessContext>("/context"));
      setContextError("");
    } catch (err) {
      setContextError(err instanceof Error ? err.message : "Could not load your business context.");
    }
  }

  useEffect(() => {
    const requestedView = new URLSearchParams(window.location.search).get("view");
    if (requestedView === "overview" || requestedView === "heatmap" || requestedView === "loss" || requestedView === "audit") {
      setTab(requestedView);
    }
    void load();
    void loadBusinessContext();
    return () => {
      if (refreshTimer.current) clearInterval(refreshTimer.current);
    };
  }, []);

  return (
    <Shell>
      <div className="dashboard-page workspace-dark">
        <div className="dashboard-heading">
          <div>
            <h1>{data?.domain_label || businessContext?.user.domain_label || "Dashboard"}</h1>
            <p className="dashboard-subtitle">
              {data
                ? "Business performance across the authorized source"
                : "Waiting for the configured live warehouse."}
            </p>
          </div>
          <div className="dashboard-toolbar">
            <label className="dashboard-toolbar__field">
              <span>Domain</span>
              <select value={data?.domain || businessContext?.user.domain || ""} disabled aria-label="Authorized domain">
                <option value={data?.domain || businessContext?.user.domain || ""}>
                  {data?.domain_label || businessContext?.user.domain_label || "Loading"}
                </option>
              </select>
            </label>
            <label className="dashboard-toolbar__field">
              <span>Date range</span>
              <select
                value={dateRange}
                onChange={(event) => setDateRange(Number(event.target.value) as DateRange)}
                aria-label="Dashboard date range"
              >
                <option value={1}>Last month</option>
                <option value={3}>Last 3 months</option>
                <option value={6}>Last 6 months</option>
                <option value={12}>Last 12 months</option>
              </select>
            </label>
            <div className="dashboard-refresh-status" role="status">
              <span className={`dashboard-refresh-status__indicator${error ? " dashboard-refresh-status__indicator--error" : ""}`} />
              <span>
                <strong>{error ? "Refresh failed" : loading ? "Refreshing data" : "Data refreshed"}</strong>
                <small>{data ? formatTimestamp(data.updated_at) : "Waiting for source"}</small>
              </span>
            </div>
            <button type="button" className="dashboard-refresh-button" onClick={() => void load(true)} disabled={loading} aria-label="Refresh dashboard data">
              <RefreshCw size={15} className={loading ? "chat-spin" : ""} />
            </button>
          </div>
        </div>
        <div className="dashboard-workspace">
          <WorkspaceSidebar
            activeView={tab}
            domain={data?.domain_label || businessContext?.user.domain_label}
            source={businessContext?.source.warehouse}
            refreshNote={data ? "Refreshes every 2 min" : undefined}
            onSelectView={setTab}
          />
          <section className="dashboard-content" aria-label="Dashboard content">
            {businessContext ? (
              <section className="dashboard-context">
                <div>
                  <p className="dashboard-context__eyebrow">Your authorized business context</p>
                  <p className="dashboard-context__user">
                    {businessContext.user.username} · {businessContext.user.role} · {businessContext.user.domain_label}
                  </p>
                  <p className="dashboard-context__source">
                    {businessContext.source.warehouse}
                    {businessContext.source.catalog ? ` · ${businessContext.source.catalog}` : ""}
                    {businessContext.source.schema ? ` · ${businessContext.source.schema}` : ""}
                    {" · "}
                    {businessContext.source.metadata_available ? "Source metadata available" : "Source metadata unavailable"}
                  </p>
                </div>
                <Link href="/context" className="dashboard-context__link">
                  Explore context <span aria-hidden="true">→</span>
                </Link>
              </section>
            ) : contextError ? (
              <div role="alert" className="dashboard-context-error">
                <span>Business context is unavailable: {contextError}</span>
                <button type="button" onClick={() => void loadBusinessContext()}>Retry</button>
              </div>
            ) : null}
            {error ? (
              <div role="alert" className="dashboard-error">
                <p>{error}</p>
                <button type="button" onClick={() => void load()}>Retry connection</button>
              </div>
            ) : null}
            {data && !hasBusinessData ? (
              <div className="dashboard-table-empty" role="status">
                No data available for the {data.domain_label} domain in the configured source.
              </div>
            ) : data ? (
              <>
                <div className={`dashboard-kpis${data.active_cash_balance != null ? " dashboard-kpis--seven" : ""}`}>
                  <Kpi
                    icon={CircleDollarSign}
                    label={data.domain === "banking" ? "Total Deposits & Accounts" : data.metric_name}
                    value={formatAmount(data.volume)}
                    detail={data.domain === "banking" ? "Current authorized account balance" : data.metric_description || "Total for the authorized domain"}
                    color="#7027F2"
                  />
                  {data.active_cash_balance != null ? (
                    <Kpi
                      icon={CircleDollarSign}
                      label="Active-account cash balance"
                      value={formatAmount(data.active_cash_balance)}
                      detail={data.active_cash_balance_description || ""}
                      color="#7027F2"
                    />
                  ) : null}
                  <Kpi
                    icon={Activity}
                    label={data.domain === "banking" ? "Events" : data.event_label || "Events"}
                    value={String(data.events)}
                    detail={data.domain === "banking" ? "Active accounts in the source" : data.event_description || "Recorded business events"}
                    color="#7027F2"
                  />
                  <Kpi
                    icon={UsersRound}
                    label={data.customer_label || "Customers"}
                    value={data.customers === null ? "Not available" : data.customers.toLocaleString()}
                    detail="Customers in the authorized domain"
                    color="#7027F2"
                  />
                  <Kpi
                    icon={Building2}
                    label={data.churn_label || "Churn"}
                    value={formatRatio(data.churn_rate)}
                    detail="Closed accounts / customer churn"
                    color="#7027F2"
                  />
                  <Kpi
                    icon={Percent}
                    label={data.loss_ratio_label}
                    value={formatRatio(data.loss_ratio)}
                    detail={data.loss_ratio_description}
                    color="#7027F2"
                  />
                  <Kpi
                    icon={ShieldCheck}
                    label="Guardrail pass"
                    value={data.queries ? `${(data.guardrail_pass_rate * 100).toFixed(0)}%` : "—"}
                    detail={data.queries
                      ? `${Math.round(data.guardrail_pass_rate * data.queries)} of ${data.queries} governed queries passed`
                      : "No governed queries recorded yet"}
                    color="#7027F2"
                  />
                </div>
                <div className="fade-content" key={tab}>
                  {tab === "overview" ? (
                    <>
                      <div className="dashboard-grid">
                        <Card title={data.trend_title || "Trend"} subtitle={`${data.trend_description || "Business performance"} · ${rangeLabel(dateRange)} of available data${latestMonth ? ` (through ${latestMonth})` : ""}`} actions={
                          <div className="dashboard-trend-controls">
                            <label className="dashboard-card-select">
                              <span className="sr-only">Trend metric</span>
                              <select value={trendMetric} onChange={(event) => setTrendMetric(event.target.value as "value" | "events")}>
                                <option value="value">{trendValueLabel}</option>
                                <option value="events">{trendEventLabel}</option>
                              </select>
                            </label>
                            <div className="dashboard-period-switch" role="group" aria-label="Trend period">
                              {([1, 3, 6, 12] as DateRange[]).map((months) => (
                                <button key={months} type="button" aria-pressed={dateRange === months} className={dateRange === months ? "is-active" : ""} onClick={() => setDateRange(months)}>
                                  {months}M
                                </button>
                              ))}
                            </div>
                          </div>
                        }>
                          <p className="dashboard-chart-caption">{trendMetric === "events" ? trendEventLabel : trendValueLabel}</p>
                          <LineChart data={chartTrendData} colors={colors} label={trendMetric === "events" ? trendEventLabel : data.trend_value_label || data.metric_name} />
                        </Card>
                        <Card
                          title="Mix"
                          subtitle={data.domain === "banking" ? "Current account mix · latest available snapshot" : "Category contribution in the selected period"}
                          actions={
                          <div className="dashboard-card-actions">
                            <div className="dashboard-segmented" role="group" aria-label="Mix chart type">
                              <button type="button" aria-pressed={mixChartType === "donut"} className={mixChartType === "donut" ? "is-active" : ""} onClick={() => setMixChartType("donut")}>Donut</button>
                              <button type="button" aria-pressed={mixChartType === "bar"} className={mixChartType === "bar" ? "is-active" : ""} onClick={() => setMixChartType("bar")}>Bar</button>
                            </div>
                            <div className="dashboard-options">
                              <button type="button" className="dashboard-icon-button" aria-label="Mix chart options" aria-expanded={showMixOptions} onClick={() => setShowMixOptions((visible) => !visible)}>
                                <MoreHorizontal size={17} />
                              </button>
                              {showMixOptions ? (
                                <div className="dashboard-options__menu">
                                  <label><input type="checkbox" checked={showMixTrends} onChange={(event) => setShowMixTrends(event.target.checked)} /> Show category trends</label>
                                </div>
                              ) : null}
                            </div>
                          </div>
                        }
                        >
                          {mixCategories.length ? (
                            <MixBreakdown
                              domain={data.domain}
                              categories={mixCategories}
                              history={categoryHistory}
                              colors={colors}
                              chartType={mixChartType}
                              showTrends={showMixTrends}
                            />
                          ) : (
                            <div className="dashboard-table-empty">
                              Category breakdowns are not available from this domain&apos;s verified source.
                            </div>
                          )}
                          <div className="dashboard-mix__explanation">
                            <details>
                              <summary>Why this chart?</summary>
                              <p>
                                {data.domain === "banking"
                                  ? "Shows the latest verified account mix. Historical balance-by-category data is not available."
                                  : `Shows how the selected period's verified ${data.metric_name.toLowerCase()} is distributed across the available categories.`}
                              </p>
                            </details>
                            <Link href="/context">View context <span aria-hidden="true">→</span></Link>
                          </div>
                        </Card>
                      </div>
                      {data.domain === "healthcare" && (
                        <Card title="Facility quality metrics">
                          <BarList
                            data={data.quality_metrics || []}
                            colors={colors}
                            formatValue={(value) => `${(value * 100).toFixed(1)}%`}
                          />
                        </Card>
                      )}
                      <div className="dashboard-grid dashboard-grid--equal">
                        <Card title="By region" subtitle="Regional performance ranked by value" actions={
                          <label className="dashboard-card-select">
                            <span className="sr-only">Regional metric</span>
                            <select value={regionMetric} onChange={(event) => setRegionMetric(event.target.value as "value" | "events")}>
                              <option value="value">{data.metric_name}</option>
                              <option value="events">{data.event_label || "Events"}</option>
                            </select>
                          </label>
                        }>
                          {data.by_region.length ? (
                            <BarList
                              data={[...data.by_region].map((region) => ({
                                ...region,
                                value: regionMetric === "events" ? region.events ?? null : region.value,
                              })).filter((region) => region.value !== null).sort((left, right) => Number(right.value) - Number(left.value))}
                              colors={REGION_CHART_COLORS}
                              showValues
                              formatValue={formatAmount}
                            />
                          ) : (
                            <div className="dashboard-table-empty">
                              Regional breakdowns are not available from this domain&apos;s verified source.
                            </div>
                          )}
                        </Card>
                        <Card title={data.live_title || "Live activity"} subtitle={data.live_description || "Recent events from the authorized source"} actions={
                          <div className="dashboard-live-heading-actions">
                            <span className="dashboard-live-badge"><span />Live</span>
                            {data.updated_at ? <time className="dashboard-live-updated" dateTime={data.updated_at}>Updated {formatTimestamp(data.updated_at)}</time> : null}
                            <button type="button" className="dashboard-icon-button" onClick={() => void load(true)} disabled={loading} aria-label="Refresh live activity">
                              <RefreshCw size={15} className={loading ? "chat-spin" : ""} />
                            </button>
                            <button type="button" className="dashboard-link-button" onClick={() => setTab("audit")}>View all</button>
                          </div>
                        }>
                          <div className="dashboard-live-activity">
                            <div className="dashboard-live-activity__feed" aria-label="Recent activity events">
                              <p className="dashboard-live-section-title"><Clock3 size={13} /> {data.live_section_title || "Recent events"}</p>
                              {data.live.map((event, index) => (
                                <div className="live-row" key={`${event.label}-${event.created_at}-${index}`}>
                                  <span className="live-row__icon" style={{ "--event-accent": colors[index % colors.length] } as React.CSSProperties} aria-hidden="true">
                                    <CircleDollarSign size={14} />
                                  </span>
                                  <span className="live-row__description">
                                    <b>{event.label}</b>
                                    {event.description ? <small>{event.description}</small> : null}
                                    <time dateTime={event.created_at}>{formatTimestamp(event.created_at)}</time>
                                  </span>
                                  <span className="live-row__value">
                                    <strong>{event.value.toLocaleString(undefined, { maximumFractionDigits: 2 })}</strong>
                                    {event.status ? <small className={`live-status live-status--${statusClass(event.status)}`}>{event.status}</small> : null}
                                  </span>
                                </div>
                              ))}
                              {!data.live.length ? <p className="dashboard-table-empty">No recent activity is available.</p> : null}
                            </div>
                            <div className="dashboard-live-activity__analytics">
                              <div className="dashboard-live-activity__chart">
                                <p><Database size={13} /> Activity volume · {rangeLabel(dateRange)}</p>
                                <ActivityBarChart data={activityChartData} />
                              </div>
                              <div className="dashboard-live-activity__status">
                                <p>Status distribution</p>
                                {recentStatuses.length ? (
                                  <div className="dashboard-status-chart">
                                    <PieChart data={recentStatuses} colors={["#2fa36b", "#df9a32", "#d5545e", "#8b7c9b"]} showLegend={false} />
                                    <div className="dashboard-status-legend">
                                      {recentStatuses.map((status, index) => (
                                        <span key={status.label}><i style={{ backgroundColor: ["#2fa36b", "#df9a32", "#d5545e", "#8b7c9b"][index % 4] }} />{status.label}<b>{status.value}</b></span>
                                      ))}
                                    </div>
                                  </div>
                                ) : <p className="dashboard-status-unavailable">Status data is not available from this source.</p>}
                              </div>
                            </div>
                          </div>
                        </Card>
                      </div>
                    </>
                  ) : null}
                  {tab === "heatmap" ? (
                    <RiskMatrixHeatMap data={data.heatmap} metricName={data.heatmap_metric_name || data.metric_name} />
                  ) : null}
                  {tab === "loss" ? (
                    <>
                      <Card title={`${data.loss_ratio_label} by region`}>
                        <p className="dashboard-card__description">
                          {data.loss_ratio_description} Portfolio ratio:{" "}
                          <strong>{formatRatio(data.loss_ratio)}</strong>
                        </p>
                        <DataTable
                          columns={["Region", data.loss_denominator_label, data.loss_numerator_label, data.loss_ratio_label]}
                          rows={lossRegions.map((row) => [
                            row.region,
                            formatAmount(row.denominator),
                            formatAmount(row.numerator),
                            `${(row.loss_ratio * 100).toFixed(1)}%`,
                          ])}
                          emptyMessage={`No regional ${data.loss_denominator_label.toLowerCase()} or ${data.loss_numerator_label.toLowerCase()} data is available yet.`}
                        />
                      </Card>
                      <div className="dashboard-grid dashboard-grid--insights">
                        <Card title={`${data.loss_ratio_label} by region`}>
                          <BarList
                            data={lossRegions.map((row) => ({ label: row.region, value: row.loss_ratio * 100 }))}
                            colors={REGION_CHART_COLORS}
                            formatValue={(value) => `${value.toFixed(1)}%`}
                          />
                        </Card>
                        <Card title={`${data.loss_numerator_label} by region`}>
                          <BarList
                            data={lossRegions.map((row) => ({ label: row.region, value: row.numerator }))}
                            colors={REGION_CHART_COLORS}
                          />
                        </Card>
                        <Card title={`${data.loss_denominator_label} by region`}>
                          <BarList
                            data={lossRegions.map((row) => ({ label: row.region, value: row.denominator }))}
                            colors={REGION_CHART_COLORS}
                          />
                        </Card>
                      </div>
                    </>
                  ) : null}
                  {tab === "audit" ? (
                    <Card title="Audit trails">
                      {data.audits.map((audit) => (
                        <div className="dashboard-audit-row" key={`${audit.audit_id}-${audit.created_at}`}>
                          <div className="dashboard-audit-row__meta">
                            <b>{audit.audit_id}</b>
                            <span>{audit.user}</span>
                            <time dateTime={audit.created_at}>{new Date(audit.created_at).toLocaleString()}</time>
                            <span className={audit.guardrail_ok ? "dashboard-audit-status" : "dashboard-audit-status dashboard-audit-status--blocked"}>
                              {audit.guardrail_ok ? "Guardrail pass" : "Blocked"}
                            </span>
                            <span>{audit.rows} rows</span>
                          </div>
                          <p>{audit.question}</p>
                          {audit.sql ? <details><summary>View SQL</summary><pre>{audit.sql}</pre></details> : null}
                        </div>
                      ))}
                      {!data.audits.length ? <p className="dashboard-table-empty">No queries logged yet. Run a prompt on Ask.</p> : null}
                    </Card>
                  ) : null}
                </div>
              </>
            ) : (
              <div className="dashboard-loading" role="status">
                <div><LoaderCircle className="chat-spin" size={24} /><strong>Loading business metrics</strong><span>Connecting to your selected data source…</span></div>
              </div>
            )}
          </section>
        </div>
      </div>
    </Shell>
  );
}

function Kpi({
  icon: Icon,
  label,
  value,
  detail,
  color,
}: {
  icon: typeof Activity;
  label: string;
  value: string;
  detail: string;
  color: string;
}) {
  return (
    <div className="dashboard-kpi" style={{ "--accent": color } as React.CSSProperties}>
      <span className="dashboard-kpi__icon"><Icon size={17} aria-hidden="true" /></span>
      <div className="dashboard-kpi__body">
        <div className="dashboard-kpi__label">{label}</div>
        <div className="dashboard-kpi__value">{value}</div>
        <div className="dashboard-kpi__detail" title={detail}>{detail}</div>
      </div>
    </div>
  );
}

function Card({
  title,
  subtitle,
  actions,
  children,
}: {
  title: string;
  subtitle?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="dashboard-card">
      <div className="dashboard-card__heading">
        <div>
          <h3>{title}</h3>
          {subtitle ? <p>{subtitle}</p> : null}
        </div>
        {actions ? <div className="dashboard-card__actions">{actions}</div> : null}
      </div>
      {children}
    </div>
  );
}

function MixBreakdown({
  domain,
  categories,
  history,
  colors,
  chartType,
  showTrends,
}: {
  domain: DashDomain;
  categories: Series[];
  history: CategoryTrend[];
  colors: string[];
  chartType: MixChartType;
  showTrends: boolean;
}) {
  const values = categories.filter((row): row is { label: string; value: number } => row.value !== null);
  const total = values.reduce((sum, row) => sum + row.value, 0);
  return (
    <div className="dashboard-mix">
      <div className={`dashboard-mix__visual${chartType === "bar" ? " dashboard-mix__visual--bar" : ""}`}>
        {chartType === "donut"
          ? <PieChart data={values} colors={colors} showLegend={false} centerLabel={domain === "banking" ? "Total Balance" : "Total"} />
          : <BarList data={values} colors={colors} />}
      </div>
      <div className="dashboard-mix__table-wrap">
        <table className="dashboard-table dashboard-mix__table">
          <thead>
            <tr>
              <th>{domain === "banking" ? "Account Type" : "Category"}</th>
              <th>{domain === "banking" ? "Total Balance" : "Value"}</th>
              <th>% of total</th>
              <th>Trend</th>
            </tr>
          </thead>
          <tbody>
            {values.map((category, index) => (
              <tr key={category.label}>
                <td className="dashboard-table__primary">
                  <span className="dashboard-mix__category">
                    <span style={{ backgroundColor: colors[index % colors.length] }} aria-hidden="true" />
                    {category.label}
                  </span>
                </td>
                <td>{formatAmount(category.value)}</td>
                <td>{total > 0 ? `${((category.value / total) * 100).toFixed(1)}%` : "—"}</td>
                <td>
                  {showTrends
                    ? <CategoryTrendCell category={category.label} history={history} color={colors[index % colors.length]} />
                    : <span className="dashboard-mix__trend-unavailable">—</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="dashboard-mix__note">
          {domain === "banking"
            ? "Trend compares transaction flow by account type; historical balance changes are not available."
            : history.length
              ? "Trend shows change from the previous available source period."
              : "Category history is not available from this verified source."}
        </p>
      </div>
    </div>
  );
}

function CategoryTrendCell({
  category,
  history,
  color,
}: {
  category: string;
  history: CategoryTrend[];
  color: string;
}) {
  const points = history
    .filter((row) => row.category === category && Number.isFinite(row.value))
    .sort((left, right) => left.label.localeCompare(right.label));

  if (points.length < 2) {
    return <span className="dashboard-mix__trend-unavailable" title="Not enough historical periods for a trend">—</span>;
  }

  const values = points.map((point) => point.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const coordinates = values.map((value, index) => {
    const x = (index / (values.length - 1)) * 64;
    const y = 22 - ((value - min) / span) * 18;
    return `${x},${y}`;
  }).join(" ");
  const previous = values[values.length - 2];
  const latest = values[values.length - 1];
  const change = previous === 0 ? null : ((latest - previous) / Math.abs(previous)) * 100;
  const direction = latest > previous ? "up" : latest < previous ? "down" : "steady";
  const changeLabel = change === null ? (latest > 0 ? "New" : "0.0%") : `${Math.abs(change).toFixed(1)}%`;

  return (
    <span className={`dashboard-mix__trend dashboard-mix__trend--${direction}`}>
      <svg viewBox="0 0 64 24" role="img" aria-label={`${category} trend over ${points.length} periods`}>
        <polyline points={coordinates} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      {direction === "up" ? <ArrowUpRight size={12} aria-hidden="true" /> : direction === "down" ? <ArrowDownRight size={12} aria-hidden="true" /> : null}
      <span>{changeLabel}</span>
    </span>
  );
}

function selectMonths<T extends { label: string }>(rows: T[], range: DateRange): T[] {
  if (!rows.length) return [];
  const dated = rows.map((row) => ({ row, key: monthKey(row.label) }));
  if (dated.every((entry) => entry.key)) {
    const keys = [...new Set(dated.map((entry) => entry.key as number))].sort((left, right) => left - right);
    const selected = new Set(keys.slice(-range));
    return dated.filter((entry) => selected.has(entry.key as number)).map((entry) => entry.row);
  }
  const labels = [...new Set(rows.map((row) => row.label))];
  const selected = new Set(labels.slice(-range));
  return rows.filter((row) => selected.has(row.label));
}

function fillMissingMonths(rows: Series[], range: DateRange): Series[] {
  if (!rows.length) return [];
  const keyed = rows.map((row) => ({ row, key: monthKey(row.label) }));
  if (keyed.some((entry) => entry.key === null)) return rows;
  const byMonth = new Map(keyed.map((entry) => [entry.key as number, entry.row]));
  const latest = Math.max(...byMonth.keys());
  return Array.from({ length: range }, (_, offset): Series => {
    const absoluteMonth = latest - (range - 1 - offset);
    const year = Math.floor(absoluteMonth / 12);
    const month = ((absoluteMonth % 12) + 12) % 12;
    const key = year * 12 + month;
    const actual = byMonth.get(key);
    return actual || { label: `${year}-${String(month + 1).padStart(2, "0")}`, value: null };
  });
}

function monthKey(label: string): number | null {
  const match = /^(\d{4})[-/](\d{1,2})/.exec(label.trim());
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  return month >= 1 && month <= 12 ? year * 12 + month - 1 : null;
}

function sumByCategory(rows: CategoryTrend[]): Series[] {
  const totals = new Map<string, number>();
  for (const row of rows) totals.set(row.category, (totals.get(row.category) || 0) + row.value);
  return [...totals].map(([label, value]) => ({ label, value })).sort((left, right) => Number(right.value) - Number(left.value));
}

function summarizeStatuses(events: Dash["live"]): Series[] {
  const totals = new Map<string, number>();
  for (const event of events) {
    if (!event.status?.trim()) continue;
    const normalized = event.status.trim().toLowerCase();
    const label = /fail|error|reject|declin/.test(normalized)
      ? "Failed"
      : /pending|process|review|queued/.test(normalized)
        ? "Pending"
        : /success|complete|paid|approved|closed|settled/.test(normalized)
          ? "Successful"
          : event.status.trim();
    totals.set(label, (totals.get(label) || 0) + 1);
  }
  return [...totals].map(([label, value]) => ({ label, value }));
}

function statusClass(status: string): string {
  const normalized = status.toLowerCase();
  if (/fail|error|reject|declin/.test(normalized)) return "failed";
  if (/pending|process|review|queued/.test(normalized)) return "pending";
  if (/success|complete|paid|approved|closed|settled/.test(normalized)) return "successful";
  return "other";
}

function rangeLabel(range: DateRange): string {
  return range === 1 ? "last month" : `last ${range} months`;
}

function formatTimestamp(value: string): string {
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.getTime()) ? value : timestamp.toLocaleString();
}

function DataTable({
  columns,
  rows,
  emptyMessage,
}: {
  columns: string[];
  rows: string[][];
  emptyMessage: string;
}) {
  if (!rows.length) return <div className="dashboard-table-empty">{emptyMessage}</div>;
  return (
    <div className="dashboard-table-wrap">
      <table className="dashboard-table">
        <thead>
          <tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${row[0]}-${row[1] ?? index}`}>
              {row.map((value, columnIndex) => (
                <td className={columnIndex === 0 ? "dashboard-table__primary" : ""} key={`${columnIndex}-${value}`}>
                  {value}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatRatio(value: number | null): string {
  return value === null ? "Not available" : `${(value * 100).toFixed(1)}%`;
}

function formatAmount(value: number): string {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(value);
}
