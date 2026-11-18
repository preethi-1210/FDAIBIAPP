"use client";

import { motion, useReducedMotion } from "framer-motion";
import { Check, Database, LoaderCircle, Minus, RotateCcw, Search, ShieldCheck, Sparkles, X } from "lucide-react";
import { STAGE_TITLES, type Journey, type StageState } from "@/lib/askJourney";

const STATE_LABELS: Record<StageState, string> = {
  pending: "Pending",
  active: "In progress",
  done: "Completed",
  failed: "Failed",
  skipped: "Not run",
};

function Indicator({ state, index }: { state: StageState; index: number }) {
  return (
    <span className={`ask-progress__dot ask-progress__dot--${state}`} aria-hidden="true">
      {state === "done" ? <Check size={14} strokeWidth={3} />
        : state === "failed" ? <X size={14} strokeWidth={3} />
          : state === "skipped" ? <Minus size={14} strokeWidth={3} />
            : state === "active" ? <LoaderCircle size={16} className="chat-spin" />
              : index + 1}
    </span>
  );
}

/**
 * The three-stage execution journey. Purely presentational: the states come from the
 * server's progress events and final result (see lib/askJourney).
 */
export function ExecutionProgress({
  journey,
  domainLabel,
  compact = false,
  onCancel,
  onRetry,
  onDismiss,
}: {
  journey: Journey;
  domainLabel: string;
  compact?: boolean;
  onCancel?: () => void;
  onRetry?: () => void;
  onDismiss?: () => void;
}) {
  const reduceMotion = useReducedMotion();
  const activeIndex = journey.stages.findIndex((stage) => stage.state === "active");
  const failed = journey.stages.some((stage) => stage.state === "failed");

  return (
    <motion.section
      className={`ask-progress${compact ? " ask-progress--compact" : ""}${failed ? " ask-progress--failed" : ""}`}
      aria-label="Analysis progress"
      initial={reduceMotion || compact ? false : { opacity: 0, y: 10, scale: 0.985 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.28, ease: "easeOut" }}
    >
      <ol className="ask-progress__steps">
        {journey.stages.map((stage, index) => (
          <li key={STAGE_TITLES[index]} className={`ask-progress__step ask-progress__step--${stage.state}`}>
            <Indicator state={stage.state} index={index} />
            <span className="ask-progress__copy">
              <b>{`Step ${index + 1} – ${STAGE_TITLES[index]}`}</b>
              <small>{stage.message}</small>
              <span className="sr-only">{STATE_LABELS[stage.state]}</span>
            </span>
            {index < journey.stages.length - 1 ? (
              <span className="ask-progress__connector" aria-hidden="true">
                <motion.span
                  className="ask-progress__connector-fill"
                  initial={false}
                  animate={{ scaleX: stage.state === "done" ? 1 : stage.state === "active" ? 0.35 : 0 }}
                  transition={{ duration: reduceMotion ? 0 : 0.45, ease: "easeInOut" }}
                />
              </span>
            ) : null}
          </li>
        ))}
      </ol>

      {!compact && failed ? (
        <div className="ask-progress__panel ask-progress__panel--failed" role="alert">
          <span className="ask-progress__panel-icon"><X size={22} /></span>
          <div>
            <strong>The analysis stopped</strong>
            <p>{journey.error || "The analysis could not be completed."}</p>
            <div className="ask-progress__actions">
              {onRetry && journey.retryable !== false ? (
                <button type="button" className="ask-progress__button ask-progress__button--primary" onClick={onRetry}>
                  <RotateCcw size={13} /> Retry
                </button>
              ) : null}
              {onDismiss ? <button type="button" className="ask-progress__button" onClick={onDismiss}>Dismiss</button> : null}
            </div>
          </div>
        </div>
      ) : null}

      {!compact && !failed && activeIndex >= 0 ? (
        <div className="ask-progress__panel" role="status" aria-live="polite">
          <span className="ask-progress__illustration" aria-hidden="true">
            {activeIndex === 0 ? <ShieldCheck size={30} /> : activeIndex === 1 ? (
              <>
                <Database size={34} />
                <motion.span
                  className="ask-progress__lens"
                  animate={reduceMotion ? undefined : { x: [0, 7, 0, -5, 0], y: [0, -4, 2, 0, 0] }}
                  transition={{ duration: 2.4, repeat: Infinity, ease: "easeInOut" }}
                >
                  <Search size={20} strokeWidth={2.6} />
                </motion.span>
              </>
            ) : <Sparkles size={30} />}
          </span>
          <div>
            <strong>
              {activeIndex === 0 ? "Connecting to AI services…"
                : activeIndex === 1 ? `Fetching ${domainLabel} insights…`
                  : "Preparing your insight…"}
            </strong>
            <p>{journey.stages[activeIndex].message}</p>
          </div>
          {onCancel ? (
            <button type="button" className="ask-progress__button ask-progress__cancel" onClick={onCancel}>
              <X size={13} /> Cancel
            </button>
          ) : null}
        </div>
      ) : null}
    </motion.section>
  );
}
