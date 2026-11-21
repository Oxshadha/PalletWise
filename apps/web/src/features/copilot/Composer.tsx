import { type KeyboardEvent, type SyntheticEvent, useEffect, useRef, useState } from "react";

import { useVoiceInput, voiceInputAvailable } from "../../shared/speech/useVoiceInput";
import { Icon } from "../../shared/ui/Icon";

export const MAX_QUESTION = 1000;

/**
 * Question input (UX spec 6): Enter sends, Shift+Enter adds a line, Stop while answering. With
 * voice input the words appear as they are recognised, over the microphone's level; stop puts
 * them in the box to edit, the arrow sends them as they are, the cross discards them.
 */
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
  const voice = useVoiceInput();
  const [canListen] = useState(voiceInputAvailable);
  const after = useRef<"edit" | "send" | "discard">("edit");
  const box = useRef<HTMLTextAreaElement>(null);
  const live = useRef<HTMLParagraphElement>(null);

  const send = (question = text.trim()) => {
    if (!question || disabled) return;
    onSend(question.slice(0, MAX_QUESTION));
    setText("");
  };

  // When listening ends (stopped, or the speaker went quiet), the words go where the button said.
  const active = voice.phase !== "idle";
  const wasActive = useRef(false);
  useEffect(() => {
    if (wasActive.current && !active) {
      const words = voice.transcript.trim();
      if (words && after.current === "send") send(words);
      else if (words && after.current === "edit") {
        setText((current) => (current ? `${current} ${words}` : words).slice(0, MAX_QUESTION));
        window.requestAnimationFrame(() => box.current?.focus());
      }
    }
    wasActive.current = active;
    // send is recreated each render; only the end of listening matters here
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  // Keep the newest words in view while they arrive.
  useEffect(() => {
    if (live.current) live.current.scrollLeft = live.current.scrollWidth;
  }, [voice.transcript]);

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

  if (voice.phase === "listening" || voice.phase === "finishing") {
    const finishing = voice.phase === "finishing";
    const heard = voice.transcript;
    const settled = heard.slice(0, heard.length - voice.interim.length);
    return (
      <div className="pw-composer pw-composer-voice" role="group" aria-label="Voice question">
        <button
          type="button"
          className="pw-round-button"
          aria-label="Discard the voice question"
          onClick={() => {
            after.current = "discard";
            voice.cancel();
          }}
        >
          <Icon name="close" size={18} />
        </button>
        <div className="pw-voice-live">
          <p ref={live} className="pw-voice-text" aria-live="polite">
            {heard ? (
              <>
                {settled}
                <span className="pw-voice-interim">{voice.interim}</span>
              </>
            ) : (
              <span className="pw-muted">{finishing ? "Transcribing..." : "Listening..."}</span>
            )}
          </p>
          <div
            className="pw-voice-wave"
            data-finishing={finishing ? "" : undefined}
            aria-hidden="true"
          >
            {voice.levels.map((level, i) => (
              <span key={i} style={{ height: `${String(Math.max(8, level * 100))}%` }} />
            ))}
          </div>
        </div>
        <button
          type="button"
          className="pw-round-button"
          aria-label="Stop listening and edit the question"
          disabled={finishing}
          onClick={() => {
            after.current = "edit";
            voice.stop();
          }}
        >
          <Icon name="stop" size={16} />
        </button>
        <button
          type="button"
          className="pw-send"
          aria-label="Send the voice question"
          disabled={disabled || !heard || finishing}
          onClick={() => {
            after.current = "send";
            voice.stop();
          }}
        >
          <Icon name="arrow-up" size={18} />
        </button>
      </div>
    );
  }

  return (
    <form className="pw-composer" onSubmit={onSubmit}>
      <label htmlFor="pw-question" className="pw-visually-hidden">
        Ask a question
      </label>
      <textarea
        ref={box}
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
      <div className="pw-composer-actions">
        {canListen && !running && (
          <button
            type="button"
            className="pw-round-button pw-mic"
            aria-label={
              voice.phase === "starting" ? "Getting the microphone ready" : "Ask by voice"
            }
            title={voice.phase === "starting" ? "Getting the microphone ready" : "Ask by voice"}
            data-starting={voice.phase === "starting" ? "" : undefined}
            disabled={disabled || voice.phase === "starting"}
            onClick={() => {
              after.current = "edit";
              voice.start();
            }}
          >
            <Icon name="mic" size={18} />
          </button>
        )}
        <button
          type="submit"
          className="pw-send"
          aria-label={running ? "Stop the answer" : "Send"}
          disabled={disabled || (!running && !text.trim())}
        >
          <Icon name={running ? "stop" : "arrow-up"} size={18} />
        </button>
      </div>
      {voice.error && (
        <p className="pw-muted pw-counter" role="status">
          {voice.error}
        </p>
      )}
      {text.length > MAX_QUESTION - 100 && (
        <span id="pw-question-count" className="pw-muted pw-counter">
          {text.length} of {MAX_QUESTION} characters
        </span>
      )}
    </form>
  );
}
