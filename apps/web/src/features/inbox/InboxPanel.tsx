import "./inbox.css";

import { type KeyboardEvent, useRef, useState } from "react";

import {
  acknowledgeException,
  ApiProblem,
  type InboxException,
  SignedOut,
  signIn,
} from "../../shared/api/client";
import { formatNumber, formatSiteTime } from "../../shared/format";
import { Icon } from "../../shared/ui/Icon";
import { useWorkspace } from "../../shared/workspace/WorkspaceContext";
import { useInbox } from "./useInbox";

/** The type in words (semantic spec 6). */
export const TYPE_TEXT: Record<InboxException["exception_type"], string> = {
  STOCKOUT_RISK: "Stockout risk",
  DELAYED_ORDER: "Delayed order",
  LOW_STOCK: "Low stock",
  DEAD_STOCK: "Dead stock",
  SLOW_IN_PRIME: "Slow stock in a prime location",
  CAPACITY_HOTSPOT: "Capacity hotspot",
  FESTIVAL_SHORTAGE: "Festival shortage",
};

const SEVERITY_TEXT: Record<InboxException["severity"], string> = {
  info: "Information",
  warning: "Warning",
  critical: "Critical",
};

/** Severity as icon and word, never color alone (FR-MAP-03); one severity per exception. */
function SeverityBadge({ severity }: { severity: InboxException["severity"] }) {
  return (
    <span className={`pw-severity is-${severity}`}>
      <Icon name={severity} size={14} />
      {SEVERITY_TEXT[severity]}
    </span>
  );
}

function urgencyText(hours: number | null): string {
  if (hours === null) return "No order affected";
  if (hours <= 0) return "Overdue";
  return `Due in ${formatNumber(hours)} hours`;
}

/** "Why it is ranked here" (FR-EXC-02): the stored components, caps and weights as sent. */
function WhyRanked({ exception }: { exception: InboxException }) {
  const c = exception.components;
  const rows = [
    {
      factor: "Order lines affected",
      value: `${formatNumber(c.lines)} (full weight at ${formatNumber(c.lines_cap)})`,
      scaled: c.lines_normalized,
      weight: c.weight_lines,
    },
    {
      factor: "Urgency",
      value: `${urgencyText(c.hours_until_due)} (full weight when overdue, none beyond ${formatNumber(c.hours_cap)} hours)`,
      scaled: c.urgency_normalized,
      weight: c.weight_urgency,
    },
    {
      factor: "Value at risk",
      value: `${formatNumber(c.value_at_risk)} (full weight at ${formatNumber(c.value_cap)})`,
      scaled: c.value_normalized,
      weight: c.weight_value,
    },
  ];
  return (
    <table className="pw-why">
      <caption>
        Why it is ranked here: score {formatNumber(exception.score)} of 100, the weighted average of
        three factors, each scaled from 0 to 1
      </caption>
      <thead>
        <tr>
          <th scope="col">Factor</th>
          <th scope="col">Value</th>
          <th scope="col">Scaled</th>
          <th scope="col">Weight</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.factor}>
            <th scope="row">{r.factor}</th>
            <td>{r.value}</td>
            <td>{formatNumber(r.scaled)}</td>
            <td>{formatNumber(r.weight)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Details({
  exception,
  timeZone,
  onAsk,
}: {
  exception: InboxException;
  timeZone: string;
  onAsk: () => void;
}) {
  const { me, siteId } = useWorkspace();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const manager = me.role === "warehouse_manager";

  const acknowledge = async () => {
    setBusy(true);
    setError(null);
    try {
      await acknowledgeException(siteId, exception.id);
      // The site stream announces the change and the inbox refetches it.
    } catch (failure) {
      if (failure instanceof SignedOut) signIn();
      else if (failure instanceof ApiProblem && failure.code === "EXCEPTION_RESOLVED")
        setError("This exception has already been resolved.");
      else setError("The acknowledgement did not go through. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="pw-exception-details">
      <WhyRanked exception={exception} />
      {exception.orders.length > 0 && (
        <p>
          <span className="pw-muted">Orders affected: </span>
          {exception.orders.map((o) => o.order_no).join(", ")}
        </p>
      )}
      <p className="pw-muted">
        First detected {formatSiteTime(exception.first_detected_at, timeZone)}
        {exception.acknowledged_at
          ? `. Acknowledged ${formatSiteTime(exception.acknowledged_at, timeZone)}.`
          : "."}
      </p>
      <div className="pw-exception-actions">
        <button type="button" className="pw-button-quiet" onClick={onAsk}>
          <Icon name="chat" size={16} /> Ask the copilot
        </button>
        {manager && exception.status === "OPEN" && (
          <button
            type="button"
            className="pw-button"
            disabled={busy}
            onClick={() => void acknowledge()}
          >
            <Icon name="check" size={16} /> {busy ? "Acknowledging" : "Acknowledge"}
          </button>
        )}
      </div>
      {error && <p role="alert">{error}</p>}
    </div>
  );
}

/**
 * Exceptions inbox (slice-3-acceptance step 4, D4): ranked by score, beside the map. Choosing an
 * exception shows exactly its highlights on the map (FR-MAP-04). Arrow keys move through the list.
 */
export function InboxPanel({
  onAskCopilot,
  onShowOnMap,
}: {
  onAskCopilot: () => void;
  onShowOnMap: () => void;
}) {
  const { state, dispatch } = useWorkspace();
  const { load, reload } = useInbox();
  const list = useRef<HTMLOListElement>(null);
  const chosen = state.layer?.kind === "exception" ? state.layer.id : null;

  const choose = (exception: InboxException) => {
    if (chosen === exception.id) {
      dispatch({ type: "hide-layer" });
      return;
    }
    dispatch({
      type: "show-layer",
      layer: { kind: "exception", id: exception.id, highlights: exception.highlights },
    });
    onShowOnMap();
  };

  const ask = (exception: InboxException) => {
    dispatch({
      type: "ask",
      pending: {
        question: `What should I do about the ${TYPE_TEXT[exception.exception_type].toLowerCase()} for ${exception.subject_label}?`,
        pageContext: {
          selected_exception_id: exception.id,
          selected_exception_type: exception.exception_type,
        },
      },
    });
    onAskCopilot();
  };

  const move = (event: KeyboardEvent<HTMLButtonElement>) => {
    const keys: Record<string, (i: number, n: number) => number> = {
      ArrowDown: (i, n) => Math.min(i + 1, n - 1),
      ArrowUp: (i) => Math.max(i - 1, 0),
      Home: () => 0,
      End: (_, n) => n - 1,
    };
    const step = keys[event.key];
    if (!step || !list.current) return;
    const rows = [...list.current.querySelectorAll<HTMLButtonElement>(".pw-exception-row")];
    const index = rows.indexOf(event.currentTarget);
    event.preventDefault();
    rows[step(index, rows.length)]?.focus();
  };

  const count = load.kind === "ready" ? load.inbox.exceptions.length : null;

  return (
    <section className="pw-panel pw-inbox" aria-labelledby="pw-inbox-title">
      <header className="pw-panel-header">
        <div>
          <h2 id="pw-inbox-title" tabIndex={-1}>
            Inbox
          </h2>
          <p className="pw-muted" role="status">
            {count === null
              ? "Exceptions ranked by impact"
              : count === 1
                ? "1 open exception, ranked by impact"
                : `${String(count)} open exceptions, ranked by impact`}
          </p>
        </div>
      </header>

      {load.kind === "loading" && <p className="pw-muted">Loading exceptions.</p>}
      {load.kind === "not-ready" && (
        <p className="pw-muted">Exceptions appear when the warehouse data has loaded.</p>
      )}
      {load.kind === "failed" && (
        <div role="alert" className="pw-inbox-message">
          <p>{load.message}</p>
          {load.traceId && <p className="pw-muted">Trace id {load.traceId}</p>}
          <button type="button" className="pw-button-quiet" onClick={reload}>
            Try again
          </button>
        </div>
      )}
      {load.kind === "ready" && load.inbox.exceptions.length === 0 && (
        <p className="pw-muted">Nothing needs attention right now.</p>
      )}
      {load.kind === "ready" && load.inbox.exceptions.length > 0 && (
        <ol className="pw-exceptions" ref={list} aria-label="Exceptions, highest impact first">
          {load.inbox.exceptions.map((exception) => {
            const open = chosen === exception.id;
            return (
              <li key={exception.id} className="pw-exception" data-chosen={open || undefined}>
                <button
                  type="button"
                  className="pw-exception-row"
                  aria-expanded={open}
                  onClick={() => {
                    choose(exception);
                  }}
                  onKeyDown={move}
                >
                  <span className="pw-exception-head">
                    <SeverityBadge severity={exception.severity} />
                    <span className="pw-exception-type">{TYPE_TEXT[exception.exception_type]}</span>
                    {exception.status === "ACKNOWLEDGED" && (
                      <span className="pw-exception-ack">Acknowledged</span>
                    )}
                  </span>
                  <span className="pw-exception-subject">{exception.subject_label}</span>
                  <span className="pw-exception-score">Score {formatNumber(exception.score)}</span>
                </button>
                {open && (
                  <Details
                    exception={exception}
                    timeZone={load.inbox.time_zone}
                    onAsk={() => {
                      ask(exception);
                    }}
                  />
                )}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
