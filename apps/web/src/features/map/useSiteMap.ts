import { useCallback, useEffect, useState } from "react";

import {
  ApiProblem,
  fetchLayout,
  fetchMapState,
  type Layout,
  type MapState,
  signIn,
  SignedOut,
} from "../../shared/api/client";
import { useSiteChanges } from "../../shared/api/siteEvents";

export type LayoutStatus =
  | { kind: "loading" }
  | { kind: "ready"; layout: Layout }
  | { kind: "empty" }
  | { kind: "failed"; message: string; traceId: string | null };

export function useSiteMap(siteId: string) {
  const [attempt, setAttempt] = useState(0);
  // Each load is tagged with what it was for, so a new site or a retry shows "loading" without
  // resetting state inside the effect.
  const [loaded, setLoaded] = useState<{ key: string; status: LayoutStatus } | null>(null);
  const [state, setState] = useState<MapState | null>(null);
  const key = `${siteId}:${String(attempt)}`;
  const layout: LayoutStatus = loaded?.key === key ? loaded.status : { kind: "loading" };

  useEffect(() => {
    const controller = new AbortController();
    const finish = (status: LayoutStatus) => {
      setLoaded({ key, status });
    };
    fetchLayout(siteId, controller.signal)
      .then((found) => {
        finish(found.bins.length === 0 ? { kind: "empty" } : { kind: "ready", layout: found });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        if (error instanceof SignedOut) {
          signIn();
          return;
        }
        if (error instanceof ApiProblem && error.status === 404) {
          finish({ kind: "empty" });
          return;
        }
        finish({
          kind: "failed",
          message: "The warehouse map could not be loaded.",
          traceId: error instanceof ApiProblem ? error.traceId : null,
        });
      });
    return () => {
      controller.abort();
    };
  }, [siteId, key]);

  // Re-read when the site stream says stock or the data status changed (agent spec 9.2); the
  // stream refetches on connect and reconnect, so nothing is missed between hints.
  const changed = useSiteChanges(siteId, ["inventory", "data_status"]);
  useEffect(() => {
    const controller = new AbortController();
    fetchMapState(siteId, controller.signal)
      .then(setState)
      .catch(() => {
        /* keep the last known state; the next hint retries */
      });
    return () => {
      controller.abort();
    };
  }, [siteId, changed]);

  const retry = useCallback(() => {
    setAttempt((n) => n + 1);
  }, []);

  return { layout, state, retry };
}
