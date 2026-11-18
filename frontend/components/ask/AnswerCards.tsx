"use client";

import { useId, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { ChevronDown, CircleAlert, CircleCheck, CircleDashed, Database, Info, Lightbulb, ListChecks } from "lucide-react";
import { CHART_COLORS, ChartTypePicker, DynamicChart, useChartType } from "@/components/Charts";

/** The server's business-friendly description of an answer (backend app/services/presentation.py). */
export type Presentation = {
  domain_label?: string;
  metric_label?: string | null;
  definition?: string | null;
  filters?: { label: string; values: string[] }[];
  period?: string | null;
  datasets?: string[];
  freshness?: {
    available: boolean;
    latest_date?: string;
    first_date?: string | null;
    age_days?: number;
    stale?: boolean;
    future?: boolean;
    note?: string | null;
  };
  verification?: { status: "passed" | "failed" | "not_verified"; summary: string; checks: string[] };
  summary?: string;
  details?: string[];
  technical_details?: boolean;
};

export type InsightResult = {
  answer: string;
  status?: string;
  domain?: string | null;
  table?: Record<string, unknown>[];
  chart?: { type: string; x: string; y: string; data: { label: string; value: number }[] } | null;
  evidence?: { kind?: string; metric?: string; interpreted_as?: string; coverage?: Record<string, unknown> } | null;
  presentation?: Presentation | null;
};

const AMOUNT = /(amount|balance|revenue|premium|sales|deposit|value|price|cost|spend|limit|disburs)/i;

function humanize(key: string): string {
  if (key === "record_count") return "Records included";
  const words = key.replace(/[_-]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function formatValue(value: number, column: string, currency: boolean): string {
  if (/rate|percent|pct|ratio/i.test(column)) return `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}%`;
  if (AMOUNT.test(column)) {
    const amount = value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    return currency ? `₹${amount}` : amount;
  }
  return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

function formatDate(value?: string | null): string | null {
  if (!value) return null;
  const date = new Date(`${value}T00:00:00`);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

function sentenceCase(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

/**
 * The measure plotted against its label, read from the returned rows. record_count is the number
 * of source rows behind each figure, not the measure, so it is plotted only when nothing else is.
 */
function seriesFromRows(rows: Record<string, unknown>[]): { x: string; y: string; data: { label: string; value: number }[] } | null {
  if (rows.length < 2) return null;
  const columns = Object.keys(rows[0]);
  const numeric = columns.filter((key) => rows.every((row) => typeof row[key] === "number" || row[key] == null));
  const x = columns.find((key) => !numeric.includes(key));
  const y = numeric.find((key) => key !== "record_count") ?? numeric[0];
  if (!x || !y) return null;
  return { x, y, data: rows.filter((row) => typeof row[y] === "number").map((row) => ({ label: String(row[x]), value: row[y] as number })) };
}

function VerificationBadge({ status, compact = false }: { status?: string; compact?: boolean }) {
  if (status === "passed") {
    return <span className="ask-badge ask-badge--passed"><CircleCheck size={13} /> {compact ? "Passed" : "Verified result"}</span>;
  }
  if (status === "failed") {
    return <span className="ask-badge ask-badge--failed"><CircleAlert size={13} /> {compact ? "Failed" : "Not verified"}</span>;
  }
  return <span className="ask-badge ask-badge--neutral"><CircleDashed size={13} /> Not verified</span>;
}

/**
 * The business answer card. Every figure comes from the API result: the value is the single row it
 * returned, verification is the server's record of its own checks, and freshness is the source's
 * real coverage. Technical detail (SQL, physical sources, execution log) is not in this card.
 */
export function AnswerCard({ result, elapsedMs }: { result: InsightResult; elapsedMs?: number }) {
  const reduceMotion = useReducedMotion();
  const [open, setOpen] = useState(false);
  const [chartType, setChartType] = useChartType();
  const detailsId = useId();
  const view = result.presentation ?? {};
  const verification = view.verification;
  const filters = view.filters ?? [];
  const row = result.table?.length === 1 ? result.table[0] : null;
  // The API states amounts in INR in its answer; without that, no currency is assumed.
  const currency = /\bINR\b/.test(result.answer);
  const numbers = row
    ? Object.entries(row).filter((entry): entry is [string, number] => typeof entry[1] === "number" && Number.isFinite(entry[1]))
    : [];
  const primary = numbers.find(([key]) => key !== "record_count") ?? numbers[0];
  // record_count repeats the headline for a plain count, so it is shown only when it adds something.
  const secondary = numbers.filter((entry) => entry !== primary && !(entry[0] === "record_count" && primary && entry[1] === primary[1])).slice(0, 3);
  const heading = view.metric_label ?? (result.evidence?.metric ? humanize(result.evidence.metric) : primary ? humanize(primary[0]) : "Business insight");
  const freshness = view.freshness;
  const latest = formatDate(freshness?.latest_date);
  const facts: [string, string][] = [
    ["Domain", view.domain_label ?? "—"],
    ["Applied filters", filters.length ? filters.map((item) => `${sentenceCase(item.label)}: ${item.values.join(", ")}`).join("; ") : "None"],
    ["Reporting period", view.period ? sentenceCase(view.period) : "Not specified"],
    ["Data freshness", latest ? `Latest record ${latest}` : "Not reported for this answer"],
  ];
  const chart = seriesFromRows(result.table ?? []);
  const datasets = view.datasets ?? [];

  return (
    <motion.section
      className="ask-answer"
      aria-label={`${heading} answer`}
      initial={reduceMotion ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: "easeOut" }}
    >
      <header className="ask-answer__header">
        <span>{view.domain_label ?? "Business"} · Business insight</span>
        <VerificationBadge status={verification?.status} />
      </header>

      <h3 className="ask-answer__metric">{heading}</h3>
      {filters.length ? (
        <div className="ask-answer__chips" aria-label="Applied filters">
          {filters.map((item) => <span key={item.label}>{sentenceCase(item.label)}: {item.values.join(", ")}</span>)}
        </div>
      ) : null}

      {primary ? (
        <strong className="ask-answer__value">{formatValue(primary[1], primary[0], currency)}</strong>
      ) : result.status === "no_data" ? (
        <strong className="ask-answer__value ask-answer__value--empty">No matching data</strong>
      ) : null}
      {secondary.length ? (
        <div className="ask-answer__kpis">
          {secondary.map(([key, value]) => (
            <div key={key} className="ask-answer__kpi"><span>{humanize(key)}</span><strong>{formatValue(value, key, currency)}</strong></div>
          ))}
        </div>
      ) : null}

      {view.summary ? (
        <div className="ask-answer__summary">
          <h4>Business summary</h4>
          <p>{view.summary}</p>
        </div>
      ) : null}

      {chart ? (
        <div className="ask-answer__chart">
          <div className="ask-answer__chart-head">
            <span>{humanize(chart.y)} by {humanize(chart.x).toLowerCase()}</span>
            <ChartTypePicker value={chartType} onChange={setChartType} />
          </div>
          <DynamicChart type={chartType} data={chart.data} colors={CHART_COLORS} />
        </div>
      ) : null}

      <dl className="ask-answer__facts">
        {facts.map(([label, value]) => (
          <div key={label} className={label === "Data freshness" && (freshness?.stale || freshness?.future) ? "ask-answer__fact--stale" : undefined}>
            <dt>{label}</dt>
            <dd>{value}</dd>
            {label === "Data freshness" && freshness?.future ? (
              <small>Later than today: the source contains future-dated records</small>
            ) : label === "Data freshness" && freshness?.stale && freshness.age_days != null ? (
              <small>{freshness.age_days} days old: newer activity may not be included</small>
            ) : null}
          </div>
        ))}
      </dl>

      <div className="ask-answer__verification">
        <div>
          <b>Answer verification</b>
          <p>{verification?.summary ?? "Verification was not reported for this answer."}</p>
        </div>
        <VerificationBadge status={verification?.status} compact />
      </div>

      <div className={`ask-answer__prepared${open ? " ask-answer__prepared--open" : ""}`}>
        <button type="button" aria-expanded={open} aria-controls={detailsId} onClick={() => setOpen((value) => !value)}>
          <Lightbulb size={16} />
          <span>
            <b>How was this answer prepared?</b>
            <small>{elapsedMs != null ? `Prepared in ${(elapsedMs / 1000).toFixed(1)}s · ` : ""}{open ? "Collapse details" : "Expand for details"}</small>
          </span>
          <ChevronDown size={16} className="ask-answer__chevron" />
        </button>
        <AnimatePresence initial={false}>
          {open ? (
            <motion.div
              id={detailsId}
              className="ask-answer__prepared-body"
              initial={reduceMotion ? false : { height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={reduceMotion ? undefined : { height: 0, opacity: 0 }}
              transition={{ duration: 0.22, ease: "easeInOut" }}
            >
              <ol className="ask-answer__steps">
                {result.evidence?.interpreted_as ? (
                  <li><b>Understood your question as</b><span>{result.evidence.interpreted_as}</span></li>
                ) : null}
                {view.definition ? (
                  <li>
                    <b>Approved definition{view.metric_label ? ` of ${view.metric_label.toLowerCase()}` : ""}</b>
                    <span>{view.definition}</span>
                    {filters.length ? (
                      <span className="ask-answer__filter-note">
                        This answer applies the filter you asked for on top of that definition:{" "}
                        {filters.map((item) => `${item.label} = ${item.values.join(", ")}`).join("; ")}.
                      </span>
                    ) : null}
                  </li>
                ) : null}
                {datasets.length ? (
                  <li>
                    <b>Data used</b>
                    <span className="ask-answer__sources">
                      {datasets.map((name, index) => (
                        <span key={name} className="ask-source" tabIndex={0}>
                          <span>Source {index + 1}</span><i aria-hidden="true">|</i><span>{name}</span>
                          <span className="ask-source__popover" role="tooltip">
                            <b>{name}</b>
                            <span>
                              {freshness?.available && freshness.first_date
                                ? `Records from ${formatDate(freshness.first_date)} to ${formatDate(freshness.latest_date)}.`
                                : "Coverage was not reported for this source."}
                            </span>
                            {freshness?.note ? <span>{freshness.note}</span> : null}
                          </span>
                        </span>
                      ))}
                    </span>
                  </li>
                ) : null}
                {verification?.checks.length ? (
                  <li>
                    <b>Checks that passed</b>
                    <ul className="ask-answer__checks">
                      {verification.checks.map((check) => <li key={check}><CircleCheck size={13} /> {check}</li>)}
                    </ul>
                  </li>
                ) : null}
                {view.details?.length ? (
                  <li><b>Notes</b>{view.details.map((detail) => <span key={detail}>{detail}</span>)}</li>
                ) : null}
              </ol>
              <p className="ask-answer__technical-note">
                <Database size={12} />
                {view.technical_details
                  ? "SQL, physical sources and the execution log are in the Lineage, Guardrails and Audit tabs below."
                  : "SQL, physical sources and the execution log are kept in the audit record and shown to administrators."}
              </p>
            </motion.div>
          ) : null}
        </AnimatePresence>
      </div>
    </motion.section>
  );
}

/** A business question the approved metric registry cannot answer yet. Distinct from off-topic questions. */
export function UnsupportedNotice({ answer, domainLabel }: { answer: string; domainLabel: string }) {
  return (
    <section className="ask-unsupported" aria-label="Unsupported business question">
      <span className="ask-unsupported__icon"><ListChecks size={18} /></span>
      <div>
        <h3>This business question is not currently supported by the approved metrics.</h3>
        <p className="ask-unsupported__domain"><Info size={12} /> No query was run and no figure is reported for {domainLabel}.</p>
        <p>{answer}</p>
      </div>
    </section>
  );
}
