import { useEffect, useMemo, useState } from "react";

import { fetchExceptions, SignedOut, signIn } from "../../shared/api/client";
import { useSiteChanges } from "../../shared/api/siteEvents";
import { Icon } from "../../shared/ui/Icon";

const MAX_SEEN = 1000;
const key = (siteId: string) => `pw.bell.seen.${siteId}`;

function readSeen(siteId: string): Set<string> | null {
  try {
    const raw = window.localStorage.getItem(key(siteId));
    return raw === null ? null : new Set(JSON.parse(raw) as string[]);
  } catch {
    return null;
  }
}

function writeSeen(siteId: string, ids: string[]): void {
  try {
    window.localStorage.setItem(key(siteId), JSON.stringify(ids.slice(0, MAX_SEEN)));
  } catch {
    /* a per-browser convenience only (D8) */
  }
}

/**
 * The notification bell (UX spec 2 row 27, D8): a dot when exceptions opened since this browser
 * last showed the inbox. "Seen" is the set of exception ids the inbox showed, kept per browser,
 * so a reset (new records) and a resolved exception (gone) need no special case. The first visit
 * takes the current exceptions as seen.
 */
export function Bell({
  siteId,
  inboxOpen,
  onOpenInbox,
}: {
  siteId: string;
  inboxOpen: boolean;
  onOpenInbox: () => void;
}) {
  const [ids, setIds] = useState<string[] | null>(null);
  const changed = useSiteChanges(siteId, ["exceptions", "data_status"]);

  useEffect(() => {
    const controller = new AbortController();
    fetchExceptions(siteId, controller.signal)
      .then((inbox) => {
        setIds(inbox.exceptions.map((e) => e.id));
      })
      .catch((e: unknown) => {
        if (e instanceof SignedOut) signIn();
        /* not ready or unreachable: the bell keeps its last state */
      });
    return () => {
      controller.abort();
    };
  }, [siteId, changed]);

  // What this browser has seen; read again when the inbox closes or the exceptions change.
  const seen = useMemo(
    () => (inboxOpen || ids === null ? null : readSeen(siteId)),
    [inboxOpen, ids, siteId],
  );
  // The open inbox shows everything, and a first visit takes what is there: both mark it seen.
  const marking = ids !== null && (inboxOpen || seen === null);
  useEffect(() => {
    if (marking) writeSeen(siteId, ids);
  }, [marking, ids, siteId]);

  const fresh = ids && seen && !marking ? ids.filter((id) => !seen.has(id)).length : 0;
  const label =
    fresh > 0
      ? `Notifications: ${String(fresh)} new ${fresh === 1 ? "exception" : "exceptions"}. Open the inbox`
      : "Notifications: nothing new. Open the inbox";

  return (
    <button
      type="button"
      className="pw-icon-button pw-bell"
      aria-label={label}
      onClick={onOpenInbox}
    >
      <Icon name="bell" size={18} />
      {fresh > 0 && <span className="pw-bell-dot" aria-hidden="true" />}
    </button>
  );
}
