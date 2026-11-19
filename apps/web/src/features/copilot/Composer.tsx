import { type KeyboardEvent, type SyntheticEvent, useState } from "react";

import { Icon } from "../../shared/ui/Icon";

export const MAX_QUESTION = 1000;

/** Question input (UX spec 6): Enter sends, Shift+Enter adds a line, Stop while answering. */
export function Composer({
  running,
  disabledReason,
  onSend,
  onStop,
}: {
  running: boolean;
  disabledReason: string | null;
  onSend: (question: string) => void;
  onStop: () => void;
}) {
  const [text, setText] = useState("");
  const disabled = disabledReason !== null;

  const send = () => {
    const question = text.trim();
    if (!question || disabled) return;
    onSend(question);
    setText("");
  };
  const onSubmit = (event: SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (running) onStop();
    else send();
  };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  };

  return (
    <form className="pw-composer" onSubmit={onSubmit}>
      <label htmlFor="pw-question" className="pw-visually-hidden">
        Ask a question
      </label>
      <textarea
        id="pw-question"
        rows={2}
        maxLength={MAX_QUESTION}
        placeholder={disabledReason ?? "Ask a question..."}
        value={text}
        disabled={disabled}
        aria-describedby={text.length > MAX_QUESTION - 100 ? "pw-question-count" : undefined}
        onChange={(event) => {
          setText(event.target.value);
        }}
        onKeyDown={onKeyDown}
      />
      {text.length > MAX_QUESTION - 100 && (
        <span id="pw-question-count" className="pw-muted pw-counter">
          {text.length} of {MAX_QUESTION} characters
        </span>
      )}
      <button
        type="submit"
        className="pw-send"
        aria-label={running ? "Stop the answer" : "Send"}
        disabled={disabled || (!running && !text.trim())}
      >
        <Icon name={running ? "stop" : "send"} size={18} />
      </button>
    </form>
  );
}
