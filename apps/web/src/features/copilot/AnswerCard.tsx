import { useState } from "react";

import { type ResultEnvelope, sendFeedback } from "../../shared/api/client";
import type { Suggestion } from "../../shared/api/runStream";
import { formatSiteTime } from "../../shared/format";
import { type Answer, highlightCount } from "../../shared/workspace/workspace";

const TABLE_ROWS = 10;

const OUTCOME_LABEL: Record<string, string> = {
  NO_RESULTS: "No results",
  DECLINE: "Not answered",
  STALE: "Data not available",
  DENIED: "Not permitted",
};

function cell(value: unknown, type: string, timeZone: string): string {
  if (value === null || value === undefined) return "";
  if (type === "datetime") return typeof value === "string" ? formatSiteTime(value, timeZone) : "";
  if (type === "boolean") return value === true ? "Yes" : "No";
  if (typeof value === "string" || typeof value === "number") return String(value);
  return JSON.stringify(value);
}

function ResultTable({ result }: { result: ResultEnvelope }) {
  const [all, setAll] = useState(false);
  const columns = result.columns;
  const rows = result.rows;
  if (columns.length === 0 || rows.length === 0) return null;
  const shown = all ? rows : rows.slice(0, TABLE_ROWS);
  const zone = result.evidence.time_zone;
  return (
    <div className="pw-answer-table">
      <table className="pw-table">
        <thead>
          <tr>
            {columns.map((c) => (
              <th
                key={c.key}
                scope="col"
                className={c.type === "integer" || c.type === "number" ? "pw-num" : ""}
              >
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.map((row, i) => (
            <tr key={i}>
              {columns.map((c) => (
                <td
                  key={c.key}
                  className={c.type === "integer" || c.type === "number" ? "pw-num pw-kpi" : ""}
                >
                  {cell(row[c.key], c.type, zone)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > TABLE_ROWS && (
        <button
          type="button"
          className="pw-link-button"
          onClick={() => {
            setAll((v) => !v);
          }}
        >
          {all ? "Show fewer" : `Show all ${String(rows.length)}`}
        </button>
      )}
      {result.truncated && (
        <p className="pw-muted">Only the first rows are shown; the full result is larger.</p>
      )}
    </div>
  );
}

function Feedback({ siteId, runId }: { siteId: string; runId: string }) {
  const [sent, setSent] = useState<"HELPFUL" | "NOT_HELPFUL" | null>(null);
  const [asking, setAsking] = useState(false);
  const [comment, setComment] = useState("");
  const submit = (rating: "HELPFUL" | "NOT_HELPFUL", text?: string) => {
    sendFeedback(siteId, runId, rating, text)
      .then(() => {
        setSent(rating);
        setAsking(false);
      })
      .catch(() => undefined);
  };
  if (sent) return <p className="pw-muted">Thank you for the feedback.</p>;
  return (
    <div className="pw-feedback">
      <button
        type="button"
        className="pw-chip"
        onClick={() => {
          submit("HELPFUL");
        }}
      >
        Helpful
      </button>
      <button
        type="button"
        className="pw-chip"
        aria-expanded={asking}
        onClick={() => {
          setAsking(true);
        }}
      >
        Not helpful
      </button>
      {asking && (
        <form
          className="pw-feedback-form"
          onSubmit={(event) => {
            event.preventDefault();
            submit("NOT_HELPFUL", comment.trim() || undefined);
          }}
        >
          <label htmlFor={`fb-${runId}`}>What was wrong? (optional)</label>
          <textarea
            id={`fb-${runId}`}
            maxLength={1000}
            value={comment}
            onChange={(e) => {
              setComment(e.target.value);
            }}
          />
          <button type="submit" className="pw-button">
            Send feedback
          </button>
        </form>
      )}
    </div>
  );
}

/** One answer (UX spec 7): result first, evidence one click away (NFR-UX-03). */
export function AnswerCard({
  answer,
  siteId,
  active,
  onShowOnMap,
  onSuggestion,
}: {
  answer: Answer;
  siteId: string;
  active: boolean;
  onShowOnMap: () => void;
  onSuggestion: (suggestion: Suggestion) => void;
}) {
  const locations = highlightCount(answer);
  const first = answer.results[0];
  const howComputed = [...new Set(answer.results.flatMap((r) => r.evidence.how_computed))];
  const asOf = first ? (first.evidence.data_as_of ?? first.evidence.as_of) : null;
  const streaming = answer.status === "streaming";

  return (
    <article className={answer.historical ? "pw-turn" : "pw-turn pw-enter"} aria-busy={streaming}>
      <p className="pw-question">{answer.question}</p>
      <div className={`pw-answer${active ? " is-active" : ""}`}>
        {answer.historical && asOf && first && (
          <p className="pw-historical">
            Historical: data as of {formatSiteTime(asOf, first.evidence.time_zone)}
          </p>
        )}
        {answer.outcome && OUTCOME_LABEL[answer.outcome] && (
          <p className="pw-outcome">{OUTCOME_LABEL[answer.outcome]}</p>
        )}
        <div className="pw-answer-text" aria-live="polite">
          {answer.text || (streaming ? <span className="pw-muted">Answering...</span> : null)}
        </div>
        {answer.status === "cancelled" && <p className="pw-muted">Stopped.</p>}
        {answer.error && (
          <div role="alert" className="pw-answer-error">
            <p>{answer.error.message}</p>
            {answer.error.traceId && <p className="pw-muted">Reference: {answer.error.traceId}</p>}
          </div>
        )}
        {locations > 0 && (
          <button
            type="button"
            className="pw-button-quiet"
            aria-pressed={active}
            onClick={onShowOnMap}
          >
            Show on map ({locations} {locations === 1 ? "location" : "locations"})
          </button>
        )}
        {answer.results.map((result, i) => (
          <ResultTable key={i} result={result} />
        ))}
        {howComputed.length > 0 && (
          <details className="pw-how">
            <summary>How this was computed</summary>
            <ul>
              {howComputed.map((text) => (
                <li key={text}>{text}</li>
              ))}
            </ul>
          </details>
        )}
        {asOf && first && !answer.historical && (
          <p className="pw-muted pw-freshness">
            Data as of {formatSiteTime(asOf, first.evidence.time_zone)}
          </p>
        )}
        {answer.suggestions.length > 0 && !streaming && (
          <div className="pw-chips" role="group" aria-label="Suggested next questions">
            {answer.suggestions.map((s) => (
              <button
                key={s.label}
                type="button"
                className="pw-chip"
                onClick={() => {
                  onSuggestion(s);
                }}
              >
                {s.label}
              </button>
            ))}
          </div>
        )}
        {answer.runId && answer.status === "done" && (
          <Feedback siteId={siteId} runId={answer.runId} />
        )}
      </div>
    </article>
  );
}
