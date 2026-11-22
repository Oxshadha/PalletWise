import { useEffect, useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

/**
 * A confirmation in a popup over the page: a dimmed backdrop, focus kept inside, Escape or a click
 * outside cancels. The caller supplies the question and the action buttons; Cancel is focused first
 * so a stray Enter never confirms.
 */
export function ConfirmDialog({
  question,
  actions,
  onCancel,
}: {
  question: string;
  actions: ReactNode;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const textId = useId();
  const cancel = useRef(onCancel);
  useEffect(() => {
    cancel.current = onCancel;
  });

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const node = ref.current;
    const buttons = () => Array.from(node?.querySelectorAll<HTMLButtonElement>("button") ?? []);
    const last = buttons().at(-1);
    (last ?? node)?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        cancel.current();
      } else if (event.key === "Tab") {
        const all = buttons();
        const first = all[0];
        const end = all.at(-1);
        if (!first || !end) return;
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          end.focus();
        } else if (!event.shiftKey && document.activeElement === end) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      previous?.focus();
    };
  }, []);

  return createPortal(
    <div
      className="pw-modal-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onCancel();
      }}
    >
      <div
        ref={ref}
        className="pw-modal"
        role="alertdialog"
        aria-modal="true"
        aria-label="Confirm"
        aria-describedby={textId}
        tabIndex={-1}
      >
        <p id={textId}>{question}</p>
        <div className="pw-modal-actions">{actions}</div>
      </div>
    </div>,
    document.body,
  );
}
