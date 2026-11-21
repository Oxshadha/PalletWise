import { useState } from "react";

import type { ShiftBy } from "../../shared/api/client";
import { Icon } from "../../shared/ui/Icon";
import storyline from "./storyline.json";

/** A step's setup: run through the same API as the time bar, confirmed first (D6). */
export type DemoSetup =
  | { type: "scenario"; scenario: string; label: string }
  | { type: "shift"; by: ShiftBy; label: string };

export interface DemoStep {
  id: string;
  title: string;
  show: string;
  setup?: DemoSetup;
  prompts: string[];
}

/** The storyline (slice-4-acceptance 2.1): a data file, so later slices add their steps to it. */
export const STEPS = storyline.steps as DemoStep[];

const STORED_STEP = "pw.demo.step";

function storedStep(): number {
  try {
    const n = Number(window.localStorage.getItem(STORED_STEP));
    return Number.isInteger(n) && n >= 0 && n < STEPS.length ? n : 0;
  } catch {
    return 0;
  }
}

export function storeStep(n: number): void {
  try {
    window.localStorage.setItem(STORED_STEP, String(n));
  } catch {
    /* a per-browser convenience only */
  }
}

/**
 * The guided demo (D6): the storyline's steps in order. A step's setup runs only when the
 * presenter clicks it; its prompts ask the copilot like any other question.
 */
export function DemoPanel({
  disabled,
  onSetup,
  onAsk,
  onClose,
}: {
  disabled: boolean;
  onSetup: (setup: DemoSetup) => void;
  onAsk: (question: string) => void;
  onClose: () => void;
}) {
  const [index, setIndex] = useState(storedStep);
  const step = STEPS[index];
  if (!step) return null;
  const go = (n: number) => {
    setIndex(n);
    storeStep(n);
  };

  return (
    <section className="pw-demo pw-panel" aria-labelledby="pw-demo-title">
      <header className="pw-demo-head">
        <p className="pw-muted">
          Guided demo, step {index + 1} of {STEPS.length}
        </p>
        <button
          type="button"
          className="pw-icon-button"
          aria-label="Close the demo"
          onClick={onClose}
        >
          <Icon name="close" size={16} />
        </button>
      </header>
      <h2 id="pw-demo-title">{step.title}</h2>
      <p>{step.show}</p>
      {step.setup && (
        <button
          type="button"
          className="pw-button"
          disabled={disabled}
          onClick={() => {
            if (step.setup) onSetup(step.setup);
          }}
        >
          {step.setup.label}
        </button>
      )}
      {step.prompts.length > 0 && (
        <div className="pw-chips" role="group" aria-label="Ask the copilot">
          {step.prompts.map((prompt) => (
            <button
              key={prompt}
              type="button"
              className="pw-chip"
              onClick={() => {
                onAsk(prompt);
              }}
            >
              {prompt}
            </button>
          ))}
        </div>
      )}
      <nav className="pw-demo-nav" aria-label="Demo steps">
        <button
          type="button"
          className="pw-button-quiet"
          disabled={index === 0}
          onClick={() => {
            go(index - 1);
          }}
        >
          Back
        </button>
        <button
          type="button"
          className="pw-button-quiet"
          disabled={index === STEPS.length - 1}
          onClick={() => {
            go(index + 1);
          }}
        >
          Next <Icon name="arrow" size={16} />
        </button>
      </nav>
    </section>
  );
}
