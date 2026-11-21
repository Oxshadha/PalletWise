import { type RefObject, useEffect, useState } from "react";

const KEY = "pw.split";
const DEFAULT = 58;
const MIN = 35;
const MAX = 75;

function stored(): number {
  try {
    const value = Number(window.localStorage.getItem(KEY));
    return value >= MIN && value <= MAX ? value : DEFAULT;
  } catch {
    return DEFAULT; // storage blocked: the default split
  }
}

function clamp(value: number): number {
  return Math.min(MAX, Math.max(MIN, Math.round(value)));
}

/**
 * The bar between the map and the side panel: drag it, or use the arrow keys, to give either more
 * width; double-click returns to the default. The split is the map's share in percent, kept per
 * browser (a convenience, never shared).
 */
export function Splitter({ workspace }: { workspace: RefObject<HTMLElement | null> }) {
  const [split, setSplit] = useState(stored);

  useEffect(() => {
    workspace.current?.style.setProperty("--pw-split", `${String(split)}%`);
    try {
      window.localStorage.setItem(KEY, String(split));
    } catch {
      /* storage blocked: the split lasts for this page only */
    }
  }, [split, workspace]);

  const follow = (clientX: number) => {
    const element = workspace.current;
    if (!element) return;
    const box = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    const left = box.left + parseFloat(style.paddingLeft);
    const width = box.width - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
    if (width > 0) setSplit(clamp(((clientX - left) / width) * 100));
  };

  // A focusable separator with a value is interactive (WAI-ARIA window splitter pattern); the
  // lint rule does not know the pattern.
  return (
    // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions
    <div
      className="pw-splitter"
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize the map and the side panel"
      aria-valuemin={MIN}
      aria-valuemax={MAX}
      aria-valuenow={split}
      // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
      tabIndex={0}
      onPointerDown={(event) => {
        event.currentTarget.setPointerCapture(event.pointerId);
        document.body.classList.add("pw-resizing");
      }}
      onPointerMove={(event) => {
        if (event.currentTarget.hasPointerCapture(event.pointerId)) follow(event.clientX);
      }}
      onPointerUp={(event) => {
        event.currentTarget.releasePointerCapture(event.pointerId);
        document.body.classList.remove("pw-resizing");
      }}
      onLostPointerCapture={() => {
        document.body.classList.remove("pw-resizing");
      }}
      onDoubleClick={() => {
        setSplit(DEFAULT);
      }}
      onKeyDown={(event) => {
        const step = event.shiftKey ? 10 : 2;
        if (event.key === "ArrowLeft") setSplit((v) => clamp(v - step));
        else if (event.key === "ArrowRight") setSplit((v) => clamp(v + step));
        else if (event.key === "Home") setSplit(MIN);
        else if (event.key === "End") setSplit(MAX);
        else return;
        event.preventDefault();
      }}
    >
      <span aria-hidden="true" />
    </div>
  );
}
