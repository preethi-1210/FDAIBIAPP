"use client";

import { useCallback, useMemo, useState } from "react";
import {
  Activity,
  ArrowDown,
  ArrowUp,
  CalendarRange,
  Download,
  FilterX,
  LoaderCircle,
  Printer,
  RefreshCw,
} from "lucide-react";
import { Shell } from "@/components/Shell";
import { WorkspaceSidebar } from "@/components/WorkspaceSidebar";
import {
  BarList,
  CHART_COLORS,
  ComparisonBarChart,
  LineChart,
  PieChart,
  REGION_CHART_COLORS,
} from "@/components/Charts";
import { api } from "@/lib/api";

type Interval = "daily" | "weekly" | "monthly" | "quarterly";
type Comparison = "previous_period" | "previous_month" | "previous_quarter" | "previous_year" | "custom";
type ReportMetric = {
  key: string;
  label: string;
  current: number | null;
  comparison: number | null;
  unit: string;
  definition: string;
  change_percent: number | null;
  higher_is_better?: boolean | null;
  trend: { label: string; value: number | null; comparison_value: number | null }[];
};
type ReportRow = {
  period: string;
  category: string | null;
  region: string | null;
  metric: string;
  value: number;
};
type ReportData = {
  domain: string;
  domain_label: string;
  period: {
    start: string;
    end: string;
    comparison_start: string | null;
    comparison_end: string | null;
    label: string;
    comparison_label: string;
  };
  availability: { start: string | null; end: string | null };
  metrics: ReportMetric[];
  trend: { label: string; value: number | null; comparison_value: number | null }[];
  categories: { label: string; value: number }[];
  regions: { label: string; value: number }[];
  rows: ReportRow[];
  filter_options: { categories: string[]; regions: string[] };
  updated_at: string;
};

type RangePreset = "" | "7" | "30" | "90" | "180" | "ytd" | "custom" | "month";
type SortKey = keyof ReportRow;
const PAGE_SIZE = 10;
const chartColors = CHART_COLORS;

function localDate(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function subtractDays(value: string, days: number): string {
  const date = new Date(`${value}T00:00:00`);
  date.setDate(date.getDate() - days);
  return localDate(date);
}

function getRange(days: number): { start: string; end: string } {
  const endDate = new Date();
  const startDate = new Date(endDate);
  startDate.setDate(startDate.getDate() - (days - 1));
  return { start: localDate(startDate), end: localDate(endDate) };
}

function getYearToDate(): { start: string; end: string } {
  const now = new Date();
  return { start: `${now.getFullYear()}-01-01`, end: localDate(now) };
}

function getMonthRange(month: string): { start: string; end: string } {
  const [year, monthNumber] = month.split("-").map(Number);
  if (!year || !monthNumber) return getRange(30);
  const start = new Date(year, monthNumber - 1, 1);
  const end = new Date(year, monthNumber, 0);
  return { start: localDate(start), end: localDate(end) };
}

function initialMonth(): string {
  const date = new Date();
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
}

function formatNumber(value: number, unit: string): string {
  const formatted = new Intl.NumberFormat(undefined, { maximumFractionDigits: unit === "percent" ? 1 : 2 }).format(value);
  if (unit === "percent") return `${formatted}%`;
  if (unit === "days") return `${formatted} days`;
  return formatted;
}

function formatChange(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "No comparison";
  return `${value > 0 ? "+" : ""}${value.toFixed(1)}%`;
}

function changeClass(metric: ReportMetric): string {
  if (
    metric.change_percent === null ||
    metric.higher_is_better === null ||
    metric.higher_is_better === undefined ||
    metric.change_percent === 0
  ) return " report-kpi__change--neutral";
  return (metric.change_percent > 0) === metric.higher_is_better
    ? " report-kpi__change--up"
    : " report-kpi__change--down";
}

function escapeCsv(value: string | number | null): string {
  const rawText = value === null ? "" : String(value);
  const text = /^[\s]*[=+\-@]/.test(rawText) ? `'${rawText}` : rawText;
  return `"${text.replaceAll('"', '""')}"`;
}

export default function ReportsPage() {
  // Filters start empty; the user picks a range or dates before applying.
  const [rangePreset, setRangePreset] = useState<RangePreset>("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [month, setMonth] = useState(initialMonth());
  const [interval, setInterval] = useState<Interval>("weekly");
  const [comparison, setComparison] = useState<Comparison>("previous_period");
  const [customComparisonStart, setCustomComparisonStart] = useState("");
  const [customComparisonEnd, setCustomComparisonEnd] = useState("");
  const [category, setCategory] = useState("");
  const [region, setRegion] = useState("");
  const [metricKey, setMetricKey] = useState("");
  const [data, setData] = useState<ReportData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [availabilityNote, setAvailabilityNote] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("period");
  const [sortDirection, setSortDirection] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(0);

  const loadReport = useCallback(async (filters: Record<string, string>, useLatestAvailable = false) => {
    setLoading(true);
    setError("");
    try {
      const query = new URLSearchParams(filters);
      let report = await api<ReportData>(`/reports?${query.toString()}`);
      if (
        useLatestAvailable &&
        !report.rows.length &&
        report.availability.end &&
        filters.start_date > report.availability.end &&
        !filters.category &&
        !filters.region
      ) {
        const adjustedEnd = report.availability.end;
        const adjustedStart = report.availability.start && report.availability.start > subtractDays(adjustedEnd, 29)
          ? report.availability.start
          : subtractDays(adjustedEnd, 29);
        const adjustedFilters = {
          ...filters,
          start_date: adjustedStart,
          end_date: adjustedEnd,
        };
        report = await api<ReportData>(`/reports?${new URLSearchParams(adjustedFilters).toString()}`);
        setStartDate(adjustedStart);
        setEndDate(adjustedEnd);
        setRangePreset("custom");
        setAvailabilityNote(
          `The selected dates had no source records. Showing the latest available data, ${adjustedStart} through ${adjustedEnd}.`,
        );
      } else {
        setAvailabilityNote("");
      }
      setData(report);
      setMetricKey((current) => report.metrics.some((metric) => metric.key === current)
        ? current
        : report.metrics[0]?.key || "");
      setPage(0);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load this report.");
    } finally {
      setLoading(false);
    }
  }, []);

  const buildFilters = useCallback((): Record<string, string> => {
    const filters: Record<string, string> = {
      start_date: startDate,
      end_date: endDate,
      interval,
      comparison,
    };
    if (category) filters.category = category;
    if (region) filters.region = region;
    if (comparison === "custom") {
      filters.comparison_start = customComparisonStart;
      filters.comparison_end = customComparisonEnd;
    }
    return filters;
  }, [startDate, endDate, interval, comparison, category, region, customComparisonStart, customComparisonEnd]);

  // The report is loaded only when the user applies filters; nothing is queried on page open.

  const metrics = data?.metrics || [];
  const selectedMetric = metrics.find((item) => item.key === metricKey) || metrics[0];
  const selectedTrend = selectedMetric?.trend || [];
  const selectedCategory = data?.categories || [];
  const selectedRegions = data?.regions || [];
  const visibleRows = useMemo(() => {
    const rows = [...(data?.rows || [])];
    rows.sort((left, right) => {
      const a = left[sortKey];
      const b = right[sortKey];
      const order = typeof a === "number" && typeof b === "number"
        ? a - b
        : String(a ?? "").localeCompare(String(b ?? ""), undefined, { numeric: true });
      return sortDirection === "asc" ? order : -order;
    });
    return rows;
  }, [data?.rows, sortKey, sortDirection]);
  const pageCount = Math.ceil(visibleRows.length / PAGE_SIZE);
  const currentRows = visibleRows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const appliedPeriod = data?.period;
  const monthValue = rangePreset === "month" ? month : "";

  function applyFilters() {
    if (!startDate || !endDate) {
      setError("Select a date range, or enter a start date and an end date.");
      return;
    }
    if (startDate > endDate) {
      setError("Choose a valid start date that is on or before the end date.");
      return;
    }
    if (comparison === "custom" && (!customComparisonStart || !customComparisonEnd || customComparisonStart > customComparisonEnd)) {
      setError("Choose a valid custom comparison period.");
      return;
    }
    void loadReport(buildFilters());
  }

  function updatePreset(value: RangePreset) {
    setRangePreset(value);
    if (value === "7" || value === "30" || value === "90" || value === "180") {
      const dates = getRange(Number(value));
      setStartDate(dates.start);
      setEndDate(dates.end);
    } else if (value === "ytd") {
      const dates = getYearToDate();
      setStartDate(dates.start);
      setEndDate(dates.end);
    } else if (value === "month") {
      const dates = getMonthRange(month);
      setStartDate(dates.start);
      setEndDate(dates.end);
      setInterval("daily");
    }
  }

  function updateMonth(value: string) {
    if (!value) {
      setRangePreset("");
      setStartDate("");
      setEndDate("");
      return;
    }
    setMonth(value);
    setRangePreset("month");
    const dates = getMonthRange(value);
    setStartDate(dates.start);
    setEndDate(dates.end);
  }

  function resetFilters() {
    setRangePreset("");
    setStartDate("");
    setEndDate("");
    setMonth(initialMonth());
    setInterval("weekly");
    setComparison("previous_period");
    setCustomComparisonStart("");
    setCustomComparisonEnd("");
    setCategory("");
    setRegion("");
    setMetricKey("");
    setError("");
    setAvailabilityNote("");
    setData(null);
    setPage(0);
  }

  function exportCsv() {
    if (!data) return;
    const lines = [
      ["Period", "Category", "Region", "Metric", "Value"].map(escapeCsv).join(","),
      ...visibleRows.map((row) =>
        [row.period, row.category, row.region, row.metric, row.value].map(escapeCsv).join(","),
      ),
    ];
    const blob = new Blob([`\uFEFF${lines.join("\r\n")}`], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `ryze-report-${data.domain}-${data.period.start}-${data.period.end}.csv`;
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  function changeSort(key: SortKey) {
    if (sortKey === key) setSortDirection((direction) => direction === "asc" ? "desc" : "asc");
    else {
      setSortKey(key);
      setSortDirection("asc");
    }
    setPage(0);
  }

  return (
    <Shell>
      <div className="dashboard-page workspace-dark report-page">
        <div className="report-heading">
          <div>
            <p className="report-eyebrow"><Activity size={14} /> BUSINESS PERFORMANCE</p>
            <h1>Report Analysis</h1>
            <p className="dashboard-subtitle">Explore governed performance metrics for your authorized business domain.</p>
          </div>
          <div className="report-heading__actions">
            {data ? <span className="report-freshness"><span />Updated {new Date(data.updated_at).toLocaleString()}</span> : null}
            <button type="button" className="report-action" onClick={exportCsv} disabled={!data?.rows.length}><Download size={15} /> Export CSV</button>
            <button type="button" className="report-action report-action--icon" onClick={() => window.print()} aria-label="Print report"><Printer size={15} /></button>
          </div>
        </div>
        <div className="dashboard-workspace">
          <WorkspaceSidebar activeView="report" domain={data?.domain_label} source="Authorized" />
          <section className="dashboard-content report-content">
            <section className="report-filter-panel" aria-label="Report filters">
              <div className="report-filter-panel__title"><CalendarRange size={15} /><strong>Report filters</strong></div>
              <div className="report-filters">
                <label>
                  <span>Date range</span>
                  <select value={rangePreset} onChange={(event) => updatePreset(event.target.value as RangePreset)}>
                    <option value="" disabled>Select date range</option>
                    <option value="7">Last 7 days</option>
                    <option value="30">Last 30 days</option>
                    <option value="90">Last 3 months</option>
                    <option value="180">Last 6 months</option>
                    <option value="ytd">Year to date</option>
                    <option value="month">Reporting month</option>
                    <option value="custom">Custom range</option>
                  </select>
                </label>
                <label>
                  <span>Reporting month</span>
                  <DateField type="month" value={monthValue} placeholder="Select month" onChange={updateMonth} />
                </label>
                <label>
                  <span>Start date</span>
                  <DateField type="date" value={startDate} placeholder="Enter start date" max={endDate || undefined} onChange={(value) => { setStartDate(value); setRangePreset("custom"); }} />
                </label>
                <label>
                  <span>End date</span>
                  <DateField type="date" value={endDate} placeholder="Enter end date" min={startDate || undefined} onChange={(value) => { setEndDate(value); setRangePreset("custom"); }} />
                </label>
                <label>
                  <span>Category / product</span>
                  <select value={category} onChange={(event) => setCategory(event.target.value)}>
                    <option value="">All available</option>
                    {(data?.filter_options.categories || []).map((option) => <option key={option} value={option}>{option}</option>)}
                  </select>
                </label>
                <label>
                  <span>Region / branch</span>
                  <select value={region} onChange={(event) => setRegion(event.target.value)}>
                    <option value="">All available</option>
                    {(data?.filter_options.regions || []).map((option) => <option key={option} value={option}>{option}</option>)}
                  </select>
                </label>
                <label>
                  <span>Reporting interval</span>
                  <select value={interval} onChange={(event) => setInterval(event.target.value as Interval)}>
                    <option value="daily">Daily</option>
                    <option value="weekly">Weekly</option>
                    <option value="monthly">Monthly</option>
                    <option value="quarterly">Quarterly</option>
                  </select>
                </label>
                <label>
                  <span>Compare with</span>
                  <select value={comparison} onChange={(event) => setComparison(event.target.value as Comparison)}>
                    <option value="previous_period">Previous period</option>
                    <option value="previous_month">Previous month</option>
                    <option value="previous_quarter">Previous quarter</option>
                    <option value="previous_year">Previous year</option>
                    <option value="custom">Custom period</option>
                  </select>
                </label>
                {comparison === "custom" ? (
                  <>
                    <label><span>Compare start</span><input type="date" value={customComparisonStart} onChange={(event) => setCustomComparisonStart(event.target.value)} /></label>
                    <label><span>Compare end</span><input type="date" value={customComparisonEnd} onChange={(event) => setCustomComparisonEnd(event.target.value)} /></label>
                  </>
                ) : null}
              </div>
              <div className="report-filter-panel__footer">
                <span>Filters apply only to your authorized domain and tenant scope.</span>
                <div>
                  <button type="button" className="report-action" onClick={resetFilters}><FilterX size={14} /> Reset</button>
                  <button type="button" className="report-apply" onClick={applyFilters} disabled={loading}>
                    {loading ? <LoaderCircle size={15} className="chat-spin" /> : <RefreshCw size={14} />}
                    Apply filters
                  </button>
                </div>
              </div>
            </section>

            {error ? <div className="report-error" role="alert">{error}<button type="button" onClick={applyFilters}>Retry</button></div> : null}
            {appliedPeriod ? (
              <div className="report-period-note">
                <span><b>Reporting period</b> {appliedPeriod.label}</span>
                <span><b>Comparison</b> {appliedPeriod.comparison_label}</span>
              </div>
            ) : null}
            {availabilityNote ? <div className="report-availability-note" role="status">{availabilityNote}</div> : null}

            {loading && !data ? (
              <div className="report-loading" role="status"><LoaderCircle size={22} className="chat-spin" /><strong>Loading report data</strong><span>Querying your authorized business source…</span></div>
            ) : data && !data.rows.length && metrics.every((metric) => metric.current === null) ? (
              <div className="dashboard-table-empty" role="status">
                No data available for the selected period.
                {data.availability.start && data.availability.end
                  ? ` ${data.domain_label} source data is available from ${data.availability.start} to ${data.availability.end}.`
                  : ""}
              </div>
            ) : data ? (
              <>
                <div className="report-kpis">
                  {metrics.map((metric, index) => (
                    <button
                      type="button"
                      className={`report-kpi${metric.key === metricKey ? " report-kpi--selected" : ""}`}
                      key={metric.key}
                      onClick={() => setMetricKey(metric.key)}
                      title={metric.definition}
                    >
                      <span className="report-kpi__title">{metric.label}</span>
                      <strong>{metric.current === null ? "Unavailable" : formatNumber(metric.current, metric.unit)}</strong>
                      <span className="report-kpi__comparison">
                        Compare: {metric.comparison === null ? "Unavailable" : formatNumber(metric.comparison, metric.unit)}
                      </span>
                      <span className={`report-kpi__change${changeClass(metric)}`}>
                        {metric.change_percent === null ? null : metric.change_percent > 0 ? <ArrowUp size={12} /> : metric.change_percent < 0 ? <ArrowDown size={12} /> : null}
                        {formatChange(metric.change_percent)}
                      </span>
                      {metric.trend.length > 1 ? <Sparkline values={metric.trend.map((point) => point.value)} color={chartColors[index % chartColors.length]} /> : null}
                    </button>
                  ))}
                  {!metrics.length ? <div className="dashboard-table-empty">No report metrics are available for this domain and period.</div> : null}
                </div>

                <div className="report-chart-grid">
                  {selectedMetric ? (
                    <ReportCard title={`Performance trend · ${selectedMetric.label}`} subtitle={`${appliedPeriod?.label || ""} · ${selectedMetric.unit}`}>
                      <LineChart
                        data={selectedTrend}
                        colors={chartColors}
                        label={selectedMetric.label}
                      />
                    </ReportCard>
                  ) : null}
                  {selectedCategory.length ? (
                    <ReportCard title="Category breakdown" subtitle={`${metrics[0]?.label || "Metric"} contribution by category`}>
                      <div className="report-category-chart"><PieChart data={selectedCategory} colors={chartColors} /></div>
                    </ReportCard>
                  ) : null}
                  {selectedRegions.length ? (
                    <ReportCard title="Regional performance" subtitle={`Ranked by ${metrics[0]?.label || "available metric"}`}>
                      <BarList data={[...selectedRegions].sort((left, right) => right.value - left.value)} colors={REGION_CHART_COLORS} />
                    </ReportCard>
                  ) : null}
                  {selectedTrend.some((point) => point.comparison_value !== null) ? (
                    <ReportCard title="Period comparison" subtitle={`${appliedPeriod?.label} vs ${appliedPeriod?.comparison_label}`}>
                      <ComparisonBarChart
                        data={selectedTrend.filter((point): point is { label: string; value: number; comparison_value: number } =>
                          point.value !== null && point.comparison_value !== null,
                        )}
                        colors={chartColors}
                        currentLabel="Current period"
                        comparisonLabel="Previous period"
                      />
                    </ReportCard>
                  ) : null}
                </div>

                <ReportCard title="Detailed report" subtitle={`${visibleRows.length.toLocaleString()} aggregated rows`}>
                  {visibleRows.length ? (
                    <>
                      <div className="report-table-wrap">
                        <table className="report-table">
                          <thead><tr>
                            {(["period", "category", "region", "metric", "value"] as SortKey[]).map((key) => (
                              <th key={key} aria-sort={sortKey === key ? sortDirection === "asc" ? "ascending" : "descending" : "none"}>
                                <button type="button" onClick={() => changeSort(key)}>
                                  {key === "period" ? "Period" : key === "category" ? "Category" : key === "region" ? "Region" : key === "metric" ? "Metric" : "Value"}
                                  {sortKey === key ? sortDirection === "asc" ? " ↑" : " ↓" : ""}
                                </button>
                              </th>
                            ))}
                          </tr></thead>
                          <tbody>
                            {currentRows.map((row, index) => (
                              <tr key={`${row.period}-${row.category}-${row.region}-${row.metric}-${index}`}>
                                <td>{row.period}</td><td>{row.category || "—"}</td><td>{row.region || "—"}</td><td>{metrics.find((metric) => metric.key === row.metric)?.label || row.metric}</td><td>{formatNumber(row.value, metrics.find((metric) => metric.key === row.metric)?.unit || "number")}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                      <div className="report-pagination">
                        <span>Page {page + 1} of {Math.max(pageCount, 1)}</span>
                        <div>
                          <button type="button" onClick={() => setPage((current) => Math.max(0, current - 1))} disabled={page === 0}>Previous</button>
                          <button type="button" onClick={() => setPage((current) => Math.min(pageCount - 1, current + 1))} disabled={page + 1 >= pageCount}>Next</button>
                        </div>
                      </div>
                    </>
                  ) : <div className="dashboard-table-empty">No detailed rows are available for this applied period and filters.</div>}
                </ReportCard>
              </>
            ) : !error ? (
              <div className="dashboard-table-empty">Apply filters to load report data.</div>
            ) : null}
          </section>
        </div>
      </div>
    </Shell>
  );
}

function DateField({
  type,
  value,
  placeholder,
  min,
  max,
  onChange,
}: {
  type: "date" | "month";
  value: string;
  placeholder: string;
  min?: string;
  max?: string;
  onChange: (value: string) => void;
}) {
  // Opens the browser calendar on click instead of requiring typing into the dd-mm-yyyy mask.
  function openPicker(event: React.MouseEvent<HTMLInputElement>) {
    try {
      event.currentTarget.showPicker();
    } catch {
      // Older browsers without showPicker() still open the picker from the input's own control.
    }
  }
  return (
    <span className={`report-date-field${value ? "" : " report-date-field--empty"}`}>
      <input
        type={type}
        value={value}
        min={min}
        max={max}
        aria-label={placeholder}
        onClick={openPicker}
        onChange={(event) => onChange(event.target.value)}
      />
      {value ? null : <span className="report-date-field__placeholder" aria-hidden="true">{placeholder}</span>}
    </span>
  );
}

function ReportCard({ title, subtitle, children }: { title: string; subtitle: string; children: React.ReactNode }) {
  return (
    <section className="dashboard-card report-card">
      <div className="report-card__heading"><div><h2>{title}</h2><p>{subtitle}</p></div></div>
      {children}
    </section>
  );
}

function Sparkline({ values, color }: { values: (number | null)[]; color: string }) {
  const available = values.filter((value): value is number => value !== null);
  if (available.length < 2) return null;
  const min = Math.min(...available);
  const max = Math.max(...available);
  const range = max - min || 1;
  const segments: string[] = [];
  let segment: string[] = [];
  values.forEach((value, index) => {
    if (value === null) {
      if (segment.length > 1) segments.push(segment.join(" "));
      segment = [];
      return;
    }
    const x = (index / (values.length - 1)) * 100;
    const y = 25 - ((value - min) / range) * 20;
    segment.push(`${x},${y}`);
  });
  if (segment.length > 1) segments.push(segment.join(" "));
  if (!segments.length) return null;
  return <svg className="report-sparkline" viewBox="0 0 100 28" role="img" aria-label="Metric trend sparkline">{segments.map((points, index) => <polyline key={index} points={points} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />)}</svg>;
}
