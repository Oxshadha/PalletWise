import { useCallback, useEffect, useRef } from "react";

import { ApiProblem, cancelRun, signIn, SignedOut } from "../../shared/api/client";
import { startRun, type Suggestion } from "../../shared/api/runStream";
import { useWorkspace } from "../../shared/workspace/WorkspaceContext";
import type { PendingQuestion } from "../../shared/workspace/workspace";

interface ActiveRun {
  controller: AbortController;
  answerId: string;
  runId: string | null;
}

/** Runs questions one at a time per conversation (UX spec 6, FR-COP-10). */
export function useCopilot() {
  const { state, dispatch, siteId } = useWorkspace();
  const active = useRef<ActiveRun | null>(null);
  const conversation = useRef(state.conversationId);

  useEffect(() => {
    conversation.current = state.conversationId;
  }, [state.conversationId]);

  const stop = useCallback(async () => {
    const run = active.current;
    if (!run) return;
    active.current = null;
    run.controller.abort();
    dispatch({ type: "answer-cancelled", id: run.answerId });
    // Stops the server side at once (model calls and remaining tool calls).
    if (run.runId) await cancelRun(siteId, run.runId).catch(() => undefined);
  }, [dispatch, siteId]);

  const ask = useCallback(
    async (
      question: string,
      options: { suggestion?: Suggestion; pageContext?: PendingQuestion["pageContext"] } = {},
    ) => {
      await stop(); // sending a new question cancels the previous one
      const id = crypto.randomUUID();
      const controller = new AbortController();
      const run: ActiveRun = { controller, answerId: id, runId: null };
      active.current = run;
      dispatch({ type: "answer-started", id, question });
      try {
        await startRun(
          {
            site_id: siteId,
            question,
            conversation_id: conversation.current,
            suggestion: options.suggestion ?? null,
            page_context: options.pageContext ?? null,
          },
          controller.signal,
          (handle) => {
            run.runId = handle.runId;
            conversation.current = handle.conversationId;
            dispatch({ type: "run-identified", id, ...handle });
          },
          (event) => {
            dispatch({ type: "run-event", id, event });
          },
        );
        dispatch({ type: "stream-interrupted", id });
      } catch (error) {
        if (controller.signal.aborted) return;
        if (error instanceof SignedOut) {
          signIn();
          return;
        }
        if (error instanceof ApiProblem) {
          dispatch({
            type: "answer-failed",
            id,
            code: error.code,
            message: error.message,
            traceId: error.traceId,
          });
        } else {
          dispatch({ type: "stream-interrupted", id });
        }
      } finally {
        if (active.current === run) active.current = null;
      }
    },
    [dispatch, siteId, stop],
  );

  return { ask, stop, running: state.answers.some((a) => a.status === "streaming") };
}
