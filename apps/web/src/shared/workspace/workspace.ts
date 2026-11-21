/**
 * Workspace state shared by the map and the copilot (UX spec 3, 6 and 7).
 *
 * Highlight lifecycle (UX spec 7): each answer keeps its own highlight set; the newest answer's
 * set becomes active when its result arrives; showing an older answer reactivates its set;
 * selecting a location changes only the selection; clearing removes the active layer only.
 * The map draws exactly the active answer's highlights (FR-MAP-04). Nothing here calculates
 * business values: answers hold what the API sent.
 */
import type { Highlight, PageContext, ResultEnvelope, RunRecord } from "../api/client";
import type { RunEvent, Suggestion } from "../api/runStream";

export type AnswerStatus = "streaming" | "done" | "error" | "cancelled";

export interface Answer {
  /** Client id until the run id is known, then the run id. */
  id: string;
  runId: string | null;
  question: string;
  status: AnswerStatus;
  path: string | null;
  outcome: string | null;
  text: string;
  /** Plain-language progress while the answer is being prepared (agent spec 9.1, pw.status). */
  progress: string | null;
  /** The exact template headline, shown when the answer is late (ADR 0001, pw.headline). */
  headline: string | null;
  results: ResultEnvelope[];
  suggestions: Suggestion[];
  error: { code: string; message: string; traceId: string | null } | null;
  traceId: string | null;
  /** Reopened from history: its evidence describes the past, never the current state. */
  historical: boolean;
  lastSeq: number;
}

export interface PendingQuestion {
  question: string;
  pageContext?: PageContext;
  suggestion?: Suggestion;
}

/**
 * A layer chosen outside the copilot: an inbox exception or a daily brief item, with the
 * highlights the API sent for it.
 */
export interface MapLayer {
  kind: "exception" | "brief";
  id: string;
  highlights: Highlight[];
}

export interface WorkspaceState {
  answers: Answer[];
  activeAnswerId: string | null;
  /** The inbox exception or brief item shown on the map; it replaces the answer's highlights. */
  layer: MapLayer | null;
  highlightsVisible: boolean;
  selectedBinId: string | null;
  view: "3d" | "2d";
  conversationId: string | null;
  /** A question asked from outside the copilot panel, e.g. "Ask about this bin". */
  pending: PendingQuestion | null;
}

export const initialWorkspace: WorkspaceState = {
  answers: [],
  activeAnswerId: null,
  layer: null,
  highlightsVisible: true,
  selectedBinId: null,
  view: "3d",
  conversationId: null,
  pending: null,
};

export type WorkspaceAction =
  | { type: "answer-started"; id: string; question: string }
  | { type: "run-identified"; id: string; runId: string; conversationId: string; traceId: string }
  | { type: "run-event"; id: string; event: RunEvent }
  | { type: "answer-failed"; id: string; code: string; message: string; traceId: string | null }
  | { type: "answer-cancelled"; id: string }
  | { type: "stream-interrupted"; id: string }
  | { type: "show-answer"; id: string }
  | { type: "show-layer"; layer: MapLayer }
  | { type: "hide-layer" }
  | { type: "exceptions-refreshed"; exceptions: { id: string; highlights: Highlight[] }[] }
  | { type: "clear-highlights" }
  | { type: "select-bin"; binId: string | null }
  | { type: "set-view"; view: "3d" | "2d" }
  | { type: "new-conversation" }
  | { type: "conversation-loaded"; conversationId: string; runs: RunRecord[] }
  | { type: "ask"; pending: PendingQuestion }
  | { type: "pending-taken" };

function update(
  state: WorkspaceState,
  id: string,
  change: (answer: Answer) => Answer,
): WorkspaceState {
  return { ...state, answers: state.answers.map((a) => (a.id === id ? change(a) : a)) };
}

function applyEvent(answer: Answer, event: RunEvent): Answer {
  if (event.seq <= answer.lastSeq) return answer; // duplicate or out of order (agent spec 9.1)
  const next = { ...answer, lastSeq: event.seq };
  switch (event.type) {
    case "RUN_STARTED":
      return { ...next, path: event.path };
    case "pw.status":
      return { ...next, progress: event.label };
    case "pw.headline":
      return { ...next, headline: event.text };
    case "pw.result":
      return { ...next, results: [...next.results, event.result] };
    case "TEXT_MESSAGE_CONTENT":
      return { ...next, text: next.text + event.delta };
    case "pw.suggestions":
      return { ...next, suggestions: event.suggestions };
    case "RUN_FINISHED":
      return {
        ...next,
        status: "done",
        outcome: event.outcome,
        path: event.path,
        traceId: event.trace_id,
      };
    case "RUN_ERROR":
      return {
        ...next,
        status: "error",
        error: { code: event.code, message: event.message, traceId: event.trace_id },
      };
    default:
      return next;
  }
}

function fromRecord(run: RunRecord): Answer {
  return {
    id: run.id,
    runId: run.id,
    question: run.question,
    status:
      run.status === "COMPLETED" ? "done" : run.status === "CANCELLED" ? "cancelled" : "error",
    path: run.path ?? null,
    outcome: run.outcome ?? null,
    text: run.answer_text ?? "",
    progress: null,
    headline: null,
    results: run.results as ResultEnvelope[],
    suggestions: run.suggestions as Suggestion[],
    error:
      run.status === "FAILED"
        ? { code: "FAILED", message: "This answer did not complete.", traceId: run.trace_id }
        : null,
    traceId: run.trace_id,
    historical: true,
    lastSeq: Number.MAX_SAFE_INTEGER,
  };
}

export function workspaceReducer(state: WorkspaceState, action: WorkspaceAction): WorkspaceState {
  switch (action.type) {
    case "answer-started":
      return {
        ...state,
        answers: [
          ...state.answers,
          {
            id: action.id,
            runId: null,
            question: action.question,
            status: "streaming",
            path: null,
            outcome: null,
            text: "",
            progress: null,
            headline: null,
            results: [],
            suggestions: [],
            error: null,
            traceId: null,
            historical: false,
            lastSeq: 0,
          },
        ],
      };
    case "run-identified":
      return {
        ...update(state, action.id, (a) => ({
          ...a,
          runId: action.runId,
          traceId: action.traceId,
        })),
        conversationId: action.conversationId,
      };
    case "run-event": {
      const next = update(state, action.id, (a) => applyEvent(a, action.event));
      // The newest answer's highlights become active as soon as its result arrives.
      if (action.event.type === "pw.result") {
        return {
          ...next,
          activeAnswerId: action.id,
          layer: null,
          highlightsVisible: true,
        };
      }
      return next;
    }
    case "answer-failed":
      return update(state, action.id, (a) => ({
        ...a,
        status: "error",
        error: { code: action.code, message: action.message, traceId: action.traceId },
      }));
    case "answer-cancelled":
      return update(state, action.id, (a) =>
        a.status === "streaming" ? { ...a, status: "cancelled" } : a,
      );
    case "stream-interrupted":
      // The stream ended without RUN_FINISHED: the stored run is the truth (agent spec 9.1).
      return update(state, action.id, (a) =>
        a.status === "streaming"
          ? {
              ...a,
              status: "error",
              error: {
                code: "INTERRUPTED",
                message:
                  "The connection was interrupted. Reopen this conversation from History to see the saved answer.",
                traceId: a.traceId,
              },
            }
          : a,
      );
    case "show-answer":
      return {
        ...state,
        activeAnswerId: action.id,
        layer: null,
        highlightsVisible: true,
      };
    case "show-layer":
      return { ...state, layer: action.layer, highlightsVisible: true };
    case "hide-layer":
      return { ...state, layer: null };
    case "exceptions-refreshed": {
      // A chosen exception follows the inbox: new highlights, or none once it has resolved. A
      // brief item is a stored snapshot and stays as it is.
      const chosen = state.layer;
      if (chosen?.kind !== "exception") return state;
      const current = action.exceptions.find((e) => e.id === chosen.id);
      return { ...state, layer: current ? { ...chosen, highlights: current.highlights } : null };
    }
    case "clear-highlights":
      return { ...state, highlightsVisible: false };
    case "select-bin":
      return { ...state, selectedBinId: action.binId };
    case "set-view":
      return { ...state, view: action.view };
    case "new-conversation":
      return { ...state, answers: [], activeAnswerId: null, conversationId: null };
    case "conversation-loaded":
      return {
        ...state,
        answers: action.runs.map(fromRecord),
        activeAnswerId: null,
        conversationId: action.conversationId,
      };
    case "ask":
      return { ...state, pending: action.pending };
    case "pending-taken":
      return { ...state, pending: null };
  }
}

export function activeAnswer(state: WorkspaceState): Answer | null {
  return state.answers.find((a) => a.id === state.activeAnswerId) ?? null;
}

/**
 * Exactly the active layer's highlights, once per target (FR-MAP-04): the chosen inbox exception
 * or brief item, otherwise the active answer.
 */
export function activeHighlights(state: WorkspaceState): Highlight[] {
  if (!state.highlightsVisible) return [];
  const answer = activeAnswer(state);
  const sets = state.layer
    ? [state.layer.highlights]
    : (answer?.results.map((r) => r.highlights) ?? []);
  const seen = new Map<string, Highlight>();
  for (const highlights of sets) {
    for (const highlight of highlights) {
      seen.set(`${highlight.target_type}:${highlight.target_id}`, highlight);
    }
  }
  return [...seen.values()];
}

export function highlightCount(answer: Answer): number {
  return new Set(answer.results.flatMap((r) => r.highlights.map((h) => h.target_id))).size;
}
