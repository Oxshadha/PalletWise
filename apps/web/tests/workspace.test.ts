import { describe, expect, it } from "vitest";

import {
  activeHighlights,
  initialWorkspace,
  workspaceReducer,
  type WorkspaceAction,
  type WorkspaceState,
} from "../src/shared/workspace/workspace";
import { BIN_A, BIN_B, envelope, event } from "./fixtures";

function run(actions: WorkspaceAction[], start: WorkspaceState = initialWorkspace): WorkspaceState {
  return actions.reduce(workspaceReducer, start);
}

const ask = (id: string, bins: string[]): WorkspaceAction[] => [
  { type: "answer-started", id, question: `q-${id}` },
  { type: "run-event", id, event: event("RUN_STARTED", { run_id: id, path: "B" }) },
  {
    type: "run-event",
    id,
    event: event("pw.result", { tool_call_id: "1", result: envelope(bins) }),
  },
  {
    type: "run-event",
    id,
    event: event("TEXT_MESSAGE_CONTENT", { message_id: "m", delta: "Answer." }),
  },
  {
    type: "run-event",
    id,
    event: event("RUN_FINISHED", { run_id: id, trace_id: "t", path: "B", outcome: "ANSWER" }),
  },
];

describe("highlight lifecycle (UX spec 7, FR-MAP-04)", () => {
  it("the map shows exactly the active answer's highlights", () => {
    const state = run(ask("one", [BIN_A, BIN_B]));
    expect(state.activeAnswerId).toBe("one");
    expect(
      activeHighlights(state)
        .map((h) => h.target_id)
        .sort(),
    ).toEqual([BIN_A, BIN_B].sort());
  });

  it("the newest answer becomes active when its result arrives; an older one can be shown again", () => {
    const state = run([...ask("one", [BIN_A]), ...ask("two", [BIN_B])]);
    expect(activeHighlights(state).map((h) => h.target_id)).toEqual([BIN_B]);
    const older = workspaceReducer(state, { type: "show-answer", id: "one" });
    expect(activeHighlights(older).map((h) => h.target_id)).toEqual([BIN_A]);
  });

  it("selecting a location changes only the selection", () => {
    const state = run([...ask("one", [BIN_A]), { type: "select-bin", binId: BIN_B }]);
    expect(state.selectedBinId).toBe(BIN_B);
    expect(activeHighlights(state).map((h) => h.target_id)).toEqual([BIN_A]);
  });

  it("clearing removes the active layer but keeps the answers", () => {
    const state = run([...ask("one", [BIN_A]), { type: "clear-highlights" }]);
    expect(activeHighlights(state)).toEqual([]);
    expect(state.answers[0]?.text).toBe("Answer.");
  });

  it("switching 2D and 3D keeps highlights and selection", () => {
    const state = run([
      ...ask("one", [BIN_A]),
      { type: "select-bin", binId: BIN_A },
      { type: "set-view", view: "2d" },
    ]);
    expect(state.view).toBe("2d");
    expect(state.selectedBinId).toBe(BIN_A);
    expect(activeHighlights(state)).toHaveLength(1);
  });
});

describe("run events", () => {
  it("ignores duplicate and out-of-order events (agent spec 9.1)", () => {
    const base = run([{ type: "answer-started", id: "a", question: "q" }]);
    const once = workspaceReducer(base, {
      type: "run-event",
      id: "a",
      event: event("TEXT_MESSAGE_CONTENT", { message_id: "m", delta: "x" }, 5),
    });
    const again = workspaceReducer(once, {
      type: "run-event",
      id: "a",
      event: event("TEXT_MESSAGE_CONTENT", { message_id: "m", delta: "x" }, 5),
    });
    const late = workspaceReducer(again, {
      type: "run-event",
      id: "a",
      event: event("TEXT_MESSAGE_CONTENT", { message_id: "m", delta: "y" }, 3),
    });
    expect(late.answers[0]?.text).toBe("x");
  });

  it("a stream that ends without RUN_FINISHED points to the saved answer", () => {
    const state = run([
      { type: "answer-started", id: "a", question: "q" },
      { type: "stream-interrupted", id: "a" },
    ]);
    expect(state.answers[0]?.status).toBe("error");
    expect(state.answers[0]?.error?.code).toBe("INTERRUPTED");
    const finished = run([...ask("b", [BIN_A]), { type: "stream-interrupted", id: "b" }]);
    expect(finished.answers[0]?.status).toBe("done");
  });

  it("reopened answers are historical and do not take over the map", () => {
    const state = run([
      {
        type: "conversation-loaded",
        conversationId: "c",
        runs: [
          {
            id: "r1",
            seq: 1,
            question: "Where is SKU-102 stored?",
            path: "B",
            status: "COMPLETED",
            outcome: "ANSWER",
            answer_text: "It is in A8-01-02.",
            results: [envelope([BIN_A])],
            suggestions: [],
            trace_id: "t",
            created_at: "2026-04-01T00:30:00Z",
            finished_at: "2026-04-01T00:30:01Z",
          },
        ],
      },
    ]);
    expect(state.answers[0]?.historical).toBe(true);
    expect(activeHighlights(state)).toEqual([]);
    expect(
      activeHighlights(workspaceReducer(state, { type: "show-answer", id: "r1" })),
    ).toHaveLength(1);
  });
});
