import { useCallback, useEffect, useState } from "react";

import {
  ApiProblem,
  fetchExceptions,
  type Inbox,
  SignedOut,
  signIn,
} from "../../shared/api/client";
import { useSiteChanges } from "../../shared/api/siteEvents";
import { useWorkspace } from "../../shared/workspace/WorkspaceContext";

export type InboxLoad =
  | { kind: "loading" }
  | { kind: "ready"; inbox: Inbox }
  | { kind: "not-ready" }
  | { kind: "failed"; message: string; traceId: string | null };

/**
 * The inbox snapshot (slice-3-acceptance step 4). Refetched when the site stream says exceptions
 * or the data status changed, so a new exception appears and a cleared one leaves without a
 * reload. The chosen exception on the map follows each snapshot.
 */
export function useInbox() {
  const { siteId, dispatch } = useWorkspace();
  const [load, setLoad] = useState<InboxLoad>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);
  const changed = useSiteChanges(siteId, ["exceptions", "data_status"]);

  useEffect(() => {
    const controller = new AbortController();
    fetchExceptions(siteId, controller.signal)
      .then((inbox) => {
        setLoad({ kind: "ready", inbox });
        dispatch({
          type: "exceptions-refreshed",
          exceptions: inbox.exceptions.map((e) => ({ id: e.id, highlights: e.highlights })),
        });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        if (error instanceof SignedOut) signIn();
        else if (error instanceof ApiProblem && error.code === "DATA_NOT_READY")
          setLoad({ kind: "not-ready" });
        else
          setLoad({
            kind: "failed",
            message: "The exceptions could not be loaded.",
            traceId: error instanceof ApiProblem ? error.traceId : null,
          });
      });
    return () => {
      controller.abort();
    };
  }, [siteId, changed, attempt, dispatch]);

  const reload = useCallback(() => {
    setAttempt((n) => n + 1);
  }, []);
  return { load, reload };
}
