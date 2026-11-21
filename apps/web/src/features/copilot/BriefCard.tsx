import { useEffect, useState } from "react";

import {
  ApiProblem,
  type Brief,
  type BriefItem,
  fetchBrief,
  SignedOut,
  signIn,
} from "../../shared/api/client";
import type { Suggestion } from "../../shared/api/runStream";
import { useSiteChanges } from "../../shared/api/siteEvents";
import { formatSiteTime } from "../../shared/format";
import { Icon } from "../../shared/ui/Icon";
import { useWorkspace } from "../../shared/workspace/WorkspaceContext";
import { ResultTable } from "./AnswerCard";

const HIDDEN_KEY = "pw.brief.hidden";

/** The business date the user hid the brief for, per site; read and written defensively. */
function hiddenFor(siteId: string): string | null {
  try {
    const all = JSON.parse(window.localStorage.getItem(HIDDEN_KEY) ?? "{}") as Record<
      string,
      string
    >;
    return all[siteId] ?? null;
  } catch {
    return null;
  }
}

function rememberHidden(siteId: string, businessDate: string | null): void {
  try {
    const all = JSON.parse(window.localStorage.getItem(HIDDEN_KEY) ?? "{}") as Record<
      string,
      string
    >;
    const kept = Object.fromEntries(Object.entries(all).filter(([site]) => site !== siteId));
    window.localStorage.setItem(
      HIDDEN_KEY,
      JSON.stringify(businessDate ? { ...kept, [siteId]: businessDate } : kept),
    );
  } catch {
    /* storage unavailable: the brief simply shows again next time */
  }
}

/**
 * Today's brief for the site (slice-3-acceptance step 5). Refetched when the site clock or data
 * status changes, so a new site day shows its new brief; a stored brief is returned unchanged.
 */
export function useBrief(): Brief | null {
  const { siteId, dataReady } = useWorkspace();
  const [brief, setBrief] = useState<Brief | null>(null);
  const changed = useSiteChanges(siteId, ["clock", "data_status"]);
  useEffect(() => {
    if (!dataReady) return;
    const controller = new AbortController();
    fetchBrief(siteId, controller.signal)
      .then(setBrief)
      .catch((error: unknown) => {
        if (error instanceof SignedOut) signIn();
        else if (!(error instanceof ApiProblem)) setBrief(null);
        /* a refusal (data loading) keeps the last brief; the next change retries */
      });
    return () => {
      controller.abort();
    };
  }, [siteId, dataReady, changed]);
  return brief;
}

/**
 * "Today's brief" (UX spec 6a): at most four items, numbers from code, one sentence each. Each
 * item leads to its detail (FR-BRF-02): its locations on the map, the inbox, or the copilot's
 * answer for it.
 */
export function BriefCard({
  brief,
  onOpenInbox,
  onAsk,
}: {
  brief: Brief;
  onOpenInbox: () => void;
  onAsk: (suggestion: Suggestion) => void;
}) {
  const { state, dispatch, siteId } = useWorkspace();
  // The day the user hid the brief for: a new site day's brief shows again by itself.
  const [hiddenDate, setHiddenDate] = useState(() => hiddenFor(siteId));
  const hidden = hiddenDate === brief.business_date;

  const show = (item: BriefItem) => {
    dispatch({
      type: "show-layer",
      layer: { kind: "brief", id: item.kind, highlights: item.highlights },
    });
  };

  if (hidden) {
    return (
      <button
        type="button"
        className="pw-button-quiet pw-brief-reopen"
        onClick={() => {
          rememberHidden(siteId, null);
          setHiddenDate(null);
        }}
      >
        <Icon name="inbox" size={16} /> Show today&apos;s brief
      </button>
    );
  }

  return (
    <section className="pw-brief pw-card-soft" aria-labelledby="pw-brief-title">
      <header className="pw-brief-head">
        <h3 id="pw-brief-title">Today&apos;s brief</h3>
        <button
          type="button"
          className="pw-button-quiet"
          onClick={() => {
            rememberHidden(siteId, brief.business_date);
            setHiddenDate(brief.business_date);
          }}
        >
          Hide
        </button>
      </header>
      <ol className="pw-brief-items">
        {brief.items.map((item) => {
          const shown = state.layer?.kind === "brief" && state.layer.id === item.kind;
          const suggestion = item.link?.suggestion ?? null;
          return (
            <li key={item.kind} className="pw-brief-item">
              <p className="pw-brief-title">{item.title}</p>
              <p>{item.text}</p>
              <p className="pw-muted pw-brief-why">{item.why}</p>
              <details className="pw-brief-details">
                <summary>Details</summary>
                <ResultTable result={item.result} />
                {item.result.evidence.how_computed.map((line) => (
                  <p key={line} className="pw-muted">
                    {line}
                  </p>
                ))}
              </details>
              <div className="pw-brief-actions">
                {item.highlights.length > 0 && (
                  <button
                    type="button"
                    className="pw-button-quiet"
                    aria-pressed={shown}
                    onClick={() => {
                      show(item);
                    }}
                  >
                    <Icon name="pin" size={14} /> Show on map
                  </button>
                )}
                {item.link?.to === "inbox" && (
                  <button
                    type="button"
                    className="pw-button-quiet"
                    onClick={() => {
                      show(item);
                      onOpenInbox();
                    }}
                  >
                    <Icon name="inbox" size={14} /> Open the inbox
                  </button>
                )}
                {item.link?.to === "copilot" && suggestion && (
                  <button
                    type="button"
                    className="pw-button-quiet"
                    onClick={() => {
                      onAsk(suggestion);
                    }}
                  >
                    <Icon name="arrow" size={14} /> {suggestion.label}
                  </button>
                )}
              </div>
            </li>
          );
        })}
      </ol>
      <p className="pw-muted pw-brief-foot">
        As of {formatSiteTime(brief.as_of, brief.time_zone)}.{" "}
        {brief.wording_source === "template"
          ? "Standard wording."
          : "Worded by the copilot from these numbers."}
      </p>
    </section>
  );
}
