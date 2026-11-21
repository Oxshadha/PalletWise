import "./copilot.css";

import { useEffect, useRef, useState } from "react";

import { fetchStarters, type Starter } from "../../shared/api/client";
import type { Suggestion } from "../../shared/api/runStream";
import { Icon } from "../../shared/ui/Icon";
import { useWorkspace } from "../../shared/workspace/WorkspaceContext";
import { AnswerCard } from "./AnswerCard";
import { BriefCard, useBrief } from "./BriefCard";
import { Composer } from "./Composer";
import { HistoryMenu } from "./HistoryMenu";
import { useCopilot } from "./useCopilot";

/** Copilot panel (UX spec 6), with today's brief at the top (UX spec 6a). */
export function CopilotPanel({
  notReadyText,
  onOpenInbox,
}: {
  notReadyText: string | null;
  onOpenInbox: () => void;
}) {
  const { state, dispatch, siteId } = useWorkspace();
  const { ask, stop, running } = useCopilot();
  const brief = useBrief();
  const [starters, setStarters] = useState<Starter[]>([]);
  const end = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetchStarters(controller.signal)
      .then(setStarters)
      .catch(() => undefined);
    return () => {
      controller.abort();
    };
  }, []);

  // A question asked elsewhere, such as "Ask about this bin" on the map (FR-MAP-07).
  useEffect(() => {
    const pending = state.pending;
    if (!pending || notReadyText) return;
    dispatch({ type: "pending-taken" });
    void ask(pending.question, {
      ...(pending.suggestion ? { suggestion: pending.suggestion } : {}),
      ...(pending.pageContext ? { pageContext: pending.pageContext } : {}),
    });
  }, [state.pending, notReadyText, dispatch, ask]);

  // A new question always comes into view; while an answer streams, the view follows it unless
  // the user has scrolled up to read earlier answers (UX spec 6).
  const lastText = state.answers.at(-1)?.text;
  const count = state.answers.length;
  const seen = useRef(count);
  useEffect(() => {
    const box = list.current;
    if (!box) return;
    const asked = count > seen.current;
    seen.current = count;
    const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 120;
    if (asked || nearBottom) end.current?.scrollIntoView({ block: "end" });
  }, [count, lastText]);

  const choose = (suggestion: Suggestion) => {
    void ask(suggestion.label, { suggestion });
  };

  return (
    <section className="pw-panel pw-copilot" aria-labelledby="pw-copilot-title">
      <header className="pw-panel-header">
        <div>
          <h2 id="pw-copilot-title" tabIndex={-1} className="pw-copilot-title">
            <span className="pw-copilot-mark" aria-hidden="true">
              <Icon name="chat" size={16} />
            </span>
            PalletWise Copilot
          </h2>
          <p className="pw-muted">Ask about your warehouse, inventory, orders or risks.</p>
        </div>
        <div className="pw-panel-actions">
          <HistoryMenu stop={stop} />
          <button
            type="button"
            className="pw-button-quiet"
            aria-label="New conversation"
            onClick={() => {
              void stop();
              dispatch({ type: "new-conversation" });
            }}
          >
            <Icon name="plus" size={16} /> New
          </button>
        </div>
      </header>

      <div className="pw-conversation" ref={list}>
        {brief && <BriefCard brief={brief} onOpenInbox={onOpenInbox} onAsk={choose} />}
        {state.answers.length === 0 && (
          <div className="pw-empty-state">
            <p className="pw-greeting">How can I help with the warehouse today?</p>
            {starters.length > 0 && (
              <div className="pw-chips" role="group" aria-label="Starter questions">
                {starters.map((s) => (
                  <button
                    key={s.label}
                    type="button"
                    className="pw-chip"
                    disabled={notReadyText !== null}
                    onClick={() => {
                      choose(s);
                    }}
                  >
                    <Icon name="arrow" size={14} /> {s.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
        {state.answers.map((answer) => (
          <AnswerCard
            key={answer.id}
            answer={answer}
            siteId={siteId}
            active={state.activeAnswerId === answer.id && state.highlightsVisible}
            onShowOnMap={() => {
              dispatch({ type: "show-answer", id: answer.id });
            }}
            onSuggestion={choose}
          />
        ))}
        <div ref={end} />
      </div>

      <Composer
        running={running}
        disabledReason={notReadyText}
        onSend={(question) => {
          void ask(question);
        }}
        onStop={() => {
          void stop();
        }}
      />
    </section>
  );
}
