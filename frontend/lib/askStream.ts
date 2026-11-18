import { API_URL, ApiError, token } from "@/lib/api";

/** Progress events the API streams while a governed analysis runs (POST /query/stream). */
export type AskStreamEvent =
  | { type: "phase"; phase: "connected" }
  | { type: "step"; stage: string; ok: boolean }
  | { type: "result"; data: unknown }
  | { type: "error"; status: number; detail: string };

/**
 * Runs one question through the streamed endpoint and resolves with the final result.
 * Each progress event is passed to `onEvent` as the server records it; nothing is simulated.
 * Rejects with ApiError for a refused or failed analysis, and with an AbortError when cancelled.
 */
export async function askStream<T>(
  body: Record<string, unknown>,
  onEvent: (event: AskStreamEvent) => void,
  signal?: AbortSignal,
): Promise<T> {
  const headers = new Headers({ "Content-Type": "application/json" });
  const t = token();
  if (t) headers.set("Authorization", `Bearer ${t}`);
  const res = await fetch(`${API_URL}/query/stream`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    credentials: "include",
    signal,
  });
  if (res.status === 401) {
    localStorage.removeItem("ryze_token");
    localStorage.removeItem("ryze_user");
    window.location.href = "/login";
    throw new ApiError("Unauthorized", 401);
  }
  if (!res.ok || !res.body) {
    const detail = await res.json().then((value: { detail?: unknown }) => value.detail).catch(() => undefined);
    throw new ApiError(typeof detail === "string" ? detail : `Request failed (${res.status})`, res.status);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    let newline = buffer.indexOf("\n");
    while (newline >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      newline = buffer.indexOf("\n");
      if (!line) continue;
      const event = JSON.parse(line) as AskStreamEvent;
      if (event.type === "result") return event.data as T;
      if (event.type === "error") throw new ApiError(event.detail || "The analysis could not be completed.", event.status);
      onEvent(event);
    }
    if (done) break;
  }
  // The connection closed before the server sent a result: never treat that as success.
  throw new ApiError("The connection to the analytics service closed before the analysis finished.", 0);
}
