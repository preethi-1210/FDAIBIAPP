import type { AskStreamEvent } from "@/lib/askStream";

/**
 * The three-stage journey shown while a question runs. Every transition comes from an event the
 * API actually sent, or from its final result: a stage is never marked complete in advance.
 */
export type StageState = "pending" | "active" | "done" | "failed" | "skipped";
export type JourneyStage = { state: StageState; message: string };
export type Journey = { stages: [JourneyStage, JourneyStage, JourneyStage]; error?: string; retryable?: boolean };

export const STAGE_TITLES = [
  "Connecting to AI services",
  "Retrieving business data",
  "Your business insight",
] as const;

// Stages the server records only after data has been read: retrieval is then genuinely over.
const AFTER_RETRIEVAL = new Set(["result_validation", "grounding_check", "synthesis", "feature_builder", "prediction", "business_rules", "hitl"]);

function retrievalMessage(stage: string, ok: boolean, domainLabel: string): string | null {
  switch (stage) {
    case "input_policy": return "Checking your question";
    case "context": return `Applying ${domainLabel} business context`;
    case "planner": return "Understanding your question";
    case "router":
    case "tool": return "Matching approved business metrics";
    case "sql_generation": return ok ? "Preparing a governed query" : "Preparing the query again";
    case "sql_validation": return ok ? "Query passed security and policy checks" : "Correcting the query after a policy check";
    case "sql_execution": return ok ? `Reading governed ${domainLabel} data` : "The data request was rejected; checking why";
    case "data_availability": return "Checking data coverage";
    default: return null;
  }
}

export function startJourney(): Journey {
  return {
    stages: [
      { state: "active", message: "Connecting securely…" },
      { state: "pending", message: "Waiting" },
      { state: "pending", message: "Waiting" },
    ],
  };
}

export function applyStreamEvent(journey: Journey, event: AskStreamEvent, domainLabel: string): Journey {
  const [service, retrieval, insight] = journey.stages;
  if (event.type === "phase" && event.phase === "connected") {
    return {
      ...journey,
      stages: [
        { state: "done", message: "AI service connected successfully" },
        { state: "active", message: `Retrieving ${domainLabel} business data…` },
        insight,
      ],
    };
  }
  if (event.type !== "step" || retrieval.state !== "active") return journey;
  if (event.ok && AFTER_RETRIEVAL.has(event.stage)) {
    return {
      ...journey,
      stages: [
        service,
        { state: "done", message: `${domainLabel} data retrieved and validated successfully` },
        { state: "active", message: "Validating results and preparing your insight…" },
      ],
    };
  }
  const message = retrievalMessage(event.stage, event.ok, domainLabel);
  return message ? { ...journey, stages: [service, { ...retrieval, message }, insight] } : journey;
}

/** Stops at the stage that was running when the request failed; later stages stay pending. */
export function failJourney(journey: Journey, message: string, retryable = true): Journey {
  let failed = false;
  const stages = journey.stages.map((stage) => {
    if (!failed && stage.state === "active") {
      failed = true;
      return { state: "failed" as const, message: "Stopped" };
    }
    return stage;
  }) as Journey["stages"];
  if (!failed) stages[0] = { state: "failed", message: "Stopped" };
  return { stages, error: message, retryable };
}
