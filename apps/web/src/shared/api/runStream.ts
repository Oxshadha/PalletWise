/**
 * The copilot run stream (agent spec 9.1). A run starts with a POST, so the stream is read from
 * fetch, not EventSource. Events carry sequence numbers; consumers ignore duplicates and
 * out-of-order events.
 */
import type { ResultEnvelope, RunRequest } from "./client";
import { ApiProblem, problemFrom, SignedOut } from "./client";

export interface Suggestion {
  label: string;
  tool: string;
  arguments: Record<string, string | number | boolean | null>;
}

interface Base {
  seq: number;
}

export type RunEvent = Base &
  (
    | { type: "RUN_STARTED"; run_id: string; path: "A" | "B" | "C" }
    | { type: "TOOL_CALL_START"; tool_call_id: string; tool: string }
    | { type: "TOOL_CALL_END"; tool_call_id: string; tool: string; outcome: string }
    | { type: "pw.status"; stage: string; label: string }
    | { type: "pw.headline"; text: string }
    | { type: "pw.result"; tool_call_id: string; result: ResultEnvelope }
    | { type: "TEXT_MESSAGE_START"; message_id: string }
    | { type: "TEXT_MESSAGE_CONTENT"; message_id: string; delta: string }
    | { type: "TEXT_MESSAGE_END"; message_id: string }
    | { type: "pw.suggestions"; suggestions: Suggestion[] }
    | {
        type: "RUN_FINISHED";
        run_id: string;
        trace_id: string;
        path: string;
        outcome: string;
        text_source?: "model" | "template" | "message" | "cache" | null;
      }
    | { type: "RUN_ERROR"; code: string; message: string; trace_id: string }
  );

export interface RunHandle {
  runId: string;
  conversationId: string;
  traceId: string;
}

/**
 * Splits buffered SSE text into complete events. Returns the parsed `data` payloads and the
 * unfinished remainder, which the caller prepends to the next chunk.
 */
export function parseSse(buffer: string): { events: unknown[]; rest: string } {
  const normalized = buffer.replace(/\r\n/gu, "\n");
  const blocks = normalized.split("\n\n");
  const rest = blocks.pop() ?? "";
  const events: unknown[] = [];
  for (const block of blocks) {
    const data = block
      .split("\n")
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).replace(/^ /u, ""))
      .join("\n");
    if (data) {
      try {
        events.push(JSON.parse(data));
      } catch {
        /* a malformed event is skipped; the sequence check catches gaps */
      }
    }
  }
  return { events, rest };
}

/**
 * Starts a run and calls `onEvent` for each event. Resolves when the stream ends. Aborting the
 * signal stops reading; the server cancels the run after its grace period, and an explicit
 * cancel (cancelRun) stops it at once.
 */
export async function startRun(
  body: RunRequest,
  signal: AbortSignal,
  onStart: (handle: RunHandle) => void,
  onEvent: (event: RunEvent) => void,
): Promise<void> {
  const response = await fetch("/api/copilot/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  if (response.status === 401) throw new SignedOut();
  if (!response.ok) throw await problemFrom(response);
  const runId = response.headers.get("X-Run-Id");
  const conversationId = response.headers.get("X-Conversation-Id");
  if (!runId || !conversationId || !response.body) {
    throw new ApiProblem(500, "ERROR", "The answer could not be started.", null);
  }
  onStart({ runId, conversationId, traceId: response.headers.get("X-Trace-Id") ?? "" });

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    const parsed = parseSse(buffer + value);
    buffer = parsed.rest;
    for (const event of parsed.events) onEvent(event as RunEvent);
  }
}
