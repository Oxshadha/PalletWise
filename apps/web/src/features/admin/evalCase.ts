import type { FeedbackRow } from "../../shared/api/client";

/**
 * A draft evaluation case from a "not helpful" answer (FR-COP-14, evals spec 8): one line for
 * evals/sets/regression.jsonl. A reviewer completes the gold answer before it is added; the gold
 * highlights come from a reference query, never from the answer that was rated wrong.
 */
export function evalCaseDraft(row: FeedbackRow): string {
  return JSON.stringify({
    id: `REG-${row.trace_id.slice(0, 8)}`,
    question: row.question ?? "",
    category: "DATA",
    role: "warehouse_manager",
    page_context: null,
    follows: null,
    scenario: null,
    gold: {
      path: "C",
      tool: row.tools[0] ?? null,
      params: {},
      outcome: "ANSWER",
      highlights_ref: null,
      facts: [],
      forbidden: [],
    },
  });
}
