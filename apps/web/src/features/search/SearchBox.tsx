import "./search.css";

import { type KeyboardEvent, useEffect, useId, useRef, useState } from "react";

import { type SearchResult, searchSite, SignedOut, signIn } from "../../shared/api/client";
import type { Suggestion } from "../../shared/api/runStream";
import { Icon } from "../../shared/ui/Icon";
import { useWorkspace } from "../../shared/workspace/WorkspaceContext";

/** Results follow typing after this pause, so a word is one request, not one per key. */
export const DEBOUNCE_MS = 200;

const KIND_TEXT: Record<SearchResult["kind"], string> = {
  product: "Product",
  bin: "Location",
  order: "Order",
};

type Found =
  { kind: "idle" } | { kind: "ready"; query: string; results: SearchResult[] } | { kind: "failed" };

/**
 * The command box (UX spec 2, slice-3-acceptance step 6): finds SKUs, bins and orders as you type
 * and runs the matching fast-path question in the copilot. The list is a listbox: arrow keys move,
 * Enter chooses, Escape closes.
 */
function CommandBox({
  onClose,
  onChoose,
}: {
  onClose: () => void;
  onChoose: (s: Suggestion) => void;
}) {
  const { siteId } = useWorkspace();
  const [text, setText] = useState("");
  const [found, setFound] = useState<Found>({ kind: "idle" });
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const dialog = useRef<HTMLDivElement>(null);
  const listId = useId();

  useEffect(() => {
    input.current?.focus();
  }, []);

  useEffect(() => {
    // A click outside the box closes it, as Escape does.
    const outside = (event: MouseEvent) => {
      if (dialog.current && !dialog.current.contains(event.target as Node)) onClose();
    };
    document.addEventListener("mousedown", outside);
    return () => {
      document.removeEventListener("mousedown", outside);
    };
  }, [onClose]);

  useEffect(() => {
    const query = text.trim();
    if (!query) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      searchSite(siteId, query, controller.signal)
        .then((answer) => {
          setFound({ kind: "ready", query, results: answer.results });
          setActive(0);
        })
        .catch((error: unknown) => {
          if (error instanceof SignedOut) signIn();
          else if (!controller.signal.aborted) setFound({ kind: "failed" });
        });
    }, DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [text, siteId]);

  // Only the results of what is typed now: Enter never picks a stale list while a new search is
  // still waiting for its pause (code review).
  const results =
    text.trim() && found.kind === "ready" && found.query === text.trim() ? found.results : [];
  const choose = (result: SearchResult) => {
    onChoose(result.suggestion);
    onClose();
  };

  const onKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
    } else if (event.key === "ArrowDown" && results.length > 0) {
      event.preventDefault();
      setActive((i) => Math.min(i + 1, results.length - 1));
    } else if (event.key === "ArrowUp" && results.length > 0) {
      event.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (event.key === "Enter") {
      const chosen = results[active];
      if (chosen) {
        event.preventDefault();
        choose(chosen);
      }
    }
  };

  const optionId = (i: number) => `${listId}-${String(i)}`;
  const empty =
    text.trim() !== "" &&
    found.kind === "ready" &&
    found.query === text.trim() &&
    found.results.length === 0;

  return (
    <div className="pw-search-backdrop">
      <div
        ref={dialog}
        className="pw-search pw-panel"
        role="dialog"
        aria-modal="true"
        aria-label="Search"
      >
        <div className="pw-search-field">
          <Icon name="search" size={18} />
          <input
            ref={input}
            type="text"
            role="combobox"
            aria-expanded={results.length > 0}
            aria-controls={listId}
            aria-activedescendant={results.length > 0 ? optionId(active) : undefined}
            aria-autocomplete="list"
            aria-label="Search for a SKU, location or order"
            placeholder="Search for a SKU, location or order"
            maxLength={64}
            value={text}
            onChange={(event) => {
              setText(event.target.value);
            }}
            onKeyDown={onKey}
          />
        </div>
        <ul id={listId} role="listbox" aria-label="Results" className="pw-search-results">
          {results.map((result, i) => (
            <li
              key={`${result.kind}:${result.id}`}
              id={optionId(i)}
              role="option"
              aria-selected={i === active}
              className="pw-search-result"
              onMouseEnter={() => {
                setActive(i);
              }}
              tabIndex={-1}
              onClick={() => {
                choose(result);
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter") choose(result);
              }}
            >
              <span className="pw-search-kind">{KIND_TEXT[result.kind]}</span>
              <span className="pw-search-code">{result.code}</span>
              {result.label && <span className="pw-muted">{result.label}</span>}
            </li>
          ))}
        </ul>
        <p className="pw-muted pw-search-status" role="status">
          {empty
            ? `Nothing matches "${text.trim()}".`
            : found.kind === "failed"
              ? "Search is not available right now."
              : results.length > 0
                ? `${String(results.length)} ${results.length === 1 ? "result" : "results"}. Enter opens it in the copilot.`
                : ""}
        </p>
      </div>
    </div>
  );
}

/**
 * The shell's search icon and its command box; Ctrl or Cmd with K opens it too. Choosing a result
 * asks its fast-path question in the copilot (validated and executed through tools.executor like
 * any suggestion); `onAsked` then shows the copilot.
 */
export function Search({ onAsked }: { onAsked: () => void }) {
  const { dispatch } = useWorkspace();
  const [open, setOpen] = useState(false);
  const onChoose = (suggestion: Suggestion) => {
    dispatch({ type: "ask", pending: { question: suggestion.label, suggestion } });
    onAsked();
  };
  const button = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const onKey = (event: globalThis.KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
    };
  }, []);
  return (
    <>
      <button
        ref={button}
        type="button"
        className="pw-icon-button"
        aria-label="Search"
        aria-haspopup="dialog"
        onClick={() => {
          setOpen(true);
        }}
      >
        <Icon name="search" />
      </button>
      {open && (
        <CommandBox
          onChoose={onChoose}
          onClose={() => {
            setOpen(false);
            button.current?.focus();
          }}
        />
      )}
    </>
  );
}
