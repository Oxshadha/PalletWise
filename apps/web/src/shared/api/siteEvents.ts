import { useEffect, useState } from "react";

/**
 * The site live stream (agent spec 9.2, slice-3-acceptance 2.1). Events are hints, never data:
 * a hint tells which snapshots to refetch. Each event's ID is the site's change sequence, so a
 * repeated or late event (ID at or below the last applied) is ignored. The browser reconnects by
 * itself and sends the last ID; the server answers with what was missed, or "everything".
 */
export type ChangeKind = "inventory" | "exceptions" | "clock" | "data_status";

export const ALL_KINDS: readonly ChangeKind[] = ["inventory", "exceptions", "clock", "data_status"];

/** Hints within this window become one refetch per snapshot. */
export const BATCH_MS = 500;

export type SiteEvent =
  | { type: "hello"; id: number }
  | { type: "change"; id: number; kinds: readonly string[]; complete: boolean }
  | { type: "heartbeat"; id: number }
  | { type: "end" };

/** Which snapshots an event asks to refetch, and the sequence applied after it. */
export function apply(
  applied: number | null,
  event: SiteEvent,
): { refetch: ChangeKind[]; applied: number | null } {
  switch (event.type) {
    case "hello":
      // On connect and reconnect every snapshot is refetched: changes may have been missed.
      return { refetch: [...ALL_KINDS], applied: event.id };
    case "change": {
      if (applied !== null && event.id <= applied) return { refetch: [], applied };
      const kinds = event.complete
        ? ALL_KINDS.filter((k) => event.kinds.includes(k))
        : [...ALL_KINDS];
      return { refetch: kinds, applied: event.id };
    }
    case "heartbeat":
      // A heartbeat ahead of what was applied means a hint was lost: refetch everything.
      if (applied !== null && event.id > applied)
        return { refetch: [...ALL_KINDS], applied: event.id };
      return { refetch: [], applied: applied ?? event.id };
    case "end":
      return { refetch: [], applied };
  }
}

type Listener = (kinds: ReadonlySet<ChangeKind>) => void;

interface Channel {
  source: EventSource;
  listeners: Set<Listener>;
  applied: number | null;
  pending: Set<ChangeKind>;
  timer: number | null;
}

const channels = new Map<string, Channel>();

function open(siteId: string): Channel {
  const source = new EventSource(`/api/sites/${encodeURIComponent(siteId)}/events`);
  const channel: Channel = {
    source,
    listeners: new Set(),
    applied: null,
    pending: new Set(),
    timer: null,
  };
  const flush = () => {
    channel.timer = null;
    const kinds = new Set(channel.pending);
    channel.pending.clear();
    for (const listener of channel.listeners) listener(kinds);
  };
  const receive = (event: SiteEvent) => {
    const result = apply(channel.applied, event);
    channel.applied = result.applied;
    if (result.refetch.length === 0) return;
    for (const kind of result.refetch) channel.pending.add(kind);
    channel.timer ??= window.setTimeout(flush, BATCH_MS);
  };
  const parse = (message: MessageEvent<unknown>) => {
    if (typeof message.data !== "string") return null;
    try {
      return {
        id: Number(message.lastEventId),
        data: JSON.parse(message.data) as Record<string, unknown>,
      };
    } catch {
      return null;
    }
  };
  source.addEventListener("hello", (m) => {
    const p = parse(m);
    if (p) receive({ type: "hello", id: p.id });
  });
  source.addEventListener("change", (m) => {
    const p = parse(m);
    if (!p) return;
    const kinds = Array.isArray(p.data.kinds) ? p.data.kinds.map(String) : [];
    receive({ type: "change", id: p.id, kinds, complete: p.data.complete === true });
  });
  source.addEventListener("heartbeat", (m) => {
    const p = parse(m);
    if (p) receive({ type: "heartbeat", id: p.id });
  });
  source.addEventListener("end", () => {
    // The session ended or the site is no longer allowed: stop; the next request signs in again.
    source.close();
  });
  return channel;
}

/** Calls `listener` with the kinds of data that changed at a site, batched. */
export function subscribeSiteChanges(siteId: string, listener: Listener): () => void {
  let channel = channels.get(siteId);
  if (!channel) {
    channel = open(siteId);
    channels.set(siteId, channel);
  }
  channel.listeners.add(listener);
  const owned = channel;
  return () => {
    owned.listeners.delete(listener);
    if (owned.listeners.size === 0) {
      owned.source.close();
      if (owned.timer !== null) window.clearTimeout(owned.timer);
      channels.delete(siteId);
    }
  };
}

/**
 * A number that grows each time any of `kinds` changes at the site. Put it in an effect's
 * dependencies to refetch that snapshot.
 */
export function useSiteChanges(siteId: string | null, kinds: readonly ChangeKind[]): number {
  const [version, setVersion] = useState(0);
  const key = kinds.join(",");
  useEffect(() => {
    if (!siteId || typeof EventSource === "undefined") return;
    const wanted = new Set(key.split(","));
    return subscribeSiteChanges(siteId, (changed) => {
      if ([...changed].some((k) => wanted.has(k))) setVersion((v) => v + 1);
    });
  }, [siteId, key]);
  return version;
}
