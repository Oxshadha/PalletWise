import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiProblem,
  fetchSimulation,
  resetSimulation,
  runScenario,
  type ShiftBy,
  shiftTime,
  SignedOut,
  signIn,
  type Simulation,
} from "../../shared/api/client";
import { useSiteChanges } from "../../shared/api/siteEvents";

/** What the presenter asked for, shown until the site has caught up with it. */
export type Operation =
  | { kind: "shift"; to: string | null } // the target, once the API has answered
  | { kind: "scenario"; title: string }
  | { kind: "reset" };

export type Move = { by: ShiftBy } | { to: string };

/** True when a state read after the request shows the operation's result (D5, D9). */
export function caughtUp(operation: Operation, site: Simulation): boolean {
  if (site.shifting || site.data_status !== "READY") return false;
  if (operation.kind !== "shift") return true;
  if (operation.to === null) return true;
  return site.site_time !== null && new Date(site.site_time) >= new Date(operation.to);
}

/**
 * The site's simulation controls (slice-4-acceptance step 5). The state is refetched whenever
 * the site stream says the clock or the data status changed, so "moving time" ends when the
 * ingested clock reaches the target and "rebuilding" ends when the new run is READY.
 */
export function useSimulation(siteId: string) {
  const [simulation, setSimulation] = useState<Simulation | null>(null);
  const [busy, setBusy] = useState<{ operation: Operation; sent: number | null } | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Requests sent and states read, counted, so only a state read after a request can end it.
  const sent = useRef(0);
  const [read, setRead] = useState(0);
  const [attempt, setAttempt] = useState(0);
  const changed = useSiteChanges(siteId, ["clock", "data_status"]);

  useEffect(() => {
    const controller = new AbortController();
    const after = sent.current;
    fetchSimulation(siteId, controller.signal)
      .then((state) => {
        setSimulation(state);
        setRead(after);
      })
      .catch((e: unknown) => {
        if (e instanceof SignedOut) signIn();
        /* otherwise the bar keeps its last state; the next change refetches */
      });
    return () => {
      controller.abort();
    };
  }, [siteId, changed, attempt]);

  /** `call` may answer a more exact operation (a shift's target from the API). */
  const run = useCallback(async (operation: Operation, call: () => Promise<Operation | null>) => {
    setBusy({ operation, sent: null });
    setError(null);
    let done: Operation;
    try {
      done = (await call()) ?? operation;
    } catch (e: unknown) {
      if (e instanceof SignedOut) signIn();
      setError(e instanceof ApiProblem ? e.message : "The request could not be completed.");
      setBusy(null);
      return false;
    }
    sent.current += 1;
    setBusy({ operation: done, sent: sent.current });
    setAttempt((n) => n + 1);
    return true;
  }, []);

  const shift = useCallback(
    (move: Move) =>
      run({ kind: "shift", to: null }, async () => ({
        kind: "shift",
        to: (await shiftTime(siteId, move)).to,
      })),
    [run, siteId],
  );
  const scenario = useCallback(
    (id: string, title: string) =>
      run({ kind: "scenario", title }, async () => {
        await runScenario(siteId, id);
        return null;
      }),
    [run, siteId],
  );
  const reset = useCallback(
    () =>
      run({ kind: "reset" }, async () => {
        await resetSimulation(siteId);
        return null;
      }),
    [run, siteId],
  );
  const dismissError = useCallback(() => {
    setError(null);
  }, []);

  // Shown until a state read after the request shows its result.
  const over =
    busy?.sent != null &&
    read >= busy.sent &&
    simulation !== null &&
    caughtUp(busy.operation, simulation);
  return {
    simulation,
    operation: busy && !over ? busy.operation : null,
    error,
    shift,
    scenario,
    reset,
    dismissError,
  };
}
