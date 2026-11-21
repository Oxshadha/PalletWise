import "./simulation.css";

import { useEffect, useRef, useState } from "react";

import type { ShiftBy } from "../../shared/api/client";
import { formatSiteTime, isoToSiteLocal, siteLocalToIso } from "../../shared/format";
import { Icon } from "../../shared/ui/Icon";
import { useWorkspace } from "../../shared/workspace/WorkspaceContext";
import { DemoPanel, type DemoSetup, storeStep } from "./DemoPanel";
import { type Move, type Operation, useSimulation } from "./useSimulation";

const STEPS: { by: Exclude<ShiftBy, "next_morning">; label: string; words: string }[] = [
  { by: "1h", label: "+1 hour", words: "1 hour" },
  { by: "4h", label: "+4 hours", words: "4 hours" },
  { by: "1d", label: "+1 day", words: "1 day" },
  { by: "1w", label: "+1 week", words: "1 week" },
];

/** The reset discards all of this (D3): the confirmation says so. */
const RESET_TEXT =
  "Reset the simulation? Every order, movement, exception and acknowledgement since the start is discarded, with every time shift and scenario. The site is rebuilt from the seed; the copilot opens again when the rebuild is complete.";

interface Pending {
  question: string;
  confirm: string;
  danger?: boolean;
  act: () => Promise<boolean>;
}

function progressText(operation: Operation, timeZone: string): string {
  switch (operation.kind) {
    case "shift":
      return operation.to
        ? `Moving time to ${formatSiteTime(operation.to, timeZone)}`
        : "Moving time";
    case "scenario":
      return `Running the scenario: ${operation.title}`;
    case "reset":
      return "Resetting: the site is being rebuilt from the seed";
  }
}

/**
 * The time shift bar and the guided demo (slice-4-acceptance step 5, D3, D6). Shown only to a
 * viewer who holds `simulation.control` on a simulated site, as the API reports it. Every action
 * is confirmed before it is sent; while it runs, the bar says what is happening and stays
 * disabled until the site shows the result.
 */
const FOLD_KEY = "pw.timebar.folded";

/** Folded unless the viewer unfolded it last time (a per-browser convenience). */
function storedFolded(): boolean {
  try {
    return window.localStorage.getItem(FOLD_KEY) !== "0";
  } catch {
    return true;
  }
}

export function SimulationControls({ onAsked }: { onAsked: () => void }) {
  const { siteId, dispatch } = useWorkspace();
  const onAsk = (question: string) => {
    dispatch({ type: "ask", pending: { question } });
    onAsked();
  };
  const { simulation, operation, error, shift, scenario, reset, dismissError } =
    useSimulation(siteId);
  const [pending, setPending] = useState<Pending | null>(null);
  const [picking, setPicking] = useState(false);
  const [demoOpen, setDemoOpen] = useState(false);
  // A reset starts the story again: the demo goes back to its first step.
  const [demoRun, setDemoRun] = useState(0);
  // Folded to a small pill beside the site name, so the map and copilot keep the height.
  const [folded, setFolded] = useState(storedFolded);
  const fold = (value: boolean) => {
    setFolded(value);
    try {
      window.localStorage.setItem(FOLD_KEY, value ? "1" : "0");
    } catch {
      /* storage blocked: the choice lasts for this page only */
    }
  };

  if (!simulation?.can_control || simulation.mode !== "SIMULATED") return null;
  const zone = simulation.time_zone;
  const ready = simulation.data_status === "READY";
  const idle = operation === null && pending === null && ready;
  const now = simulation.site_time;

  const ask = (p: Pending) => {
    dismissError();
    setPicking(false);
    setPending(p);
  };

  const askShift = (move: Move, words: string) => {
    ask({
      question: `Move time forward ${words}? Every order, pick, shipment and receipt of that period is generated and loaded.`,
      confirm: "Move time",
      act: () => shift(move),
    });
  };

  const setup = (step: DemoSetup) => {
    if (step.type === "scenario") {
      ask({
        question: `${step.label}? The scenario's events are added from the current site time, then time moves 15 minutes.`,
        confirm: step.label,
        act: () => scenario(step.scenario, step.label),
      });
    } else if (step.by === "next_morning") {
      if (simulation.next_morning)
        askShift({ by: "next_morning" }, `to ${formatSiteTime(simulation.next_morning, zone)}`);
    } else {
      const found = STEPS.find((s) => s.by === step.by);
      if (found) askShift({ by: found.by }, found.words);
    }
  };

  // Opens by itself whenever something needs attention: a confirmation, a move in progress,
  // the site not ready, an error, or the demo steps.
  const attention =
    pending !== null || picking || operation !== null || !ready || error !== null || demoOpen;
  const timeText = now ? formatSiteTime(now, zone) : "not set";

  if (folded && !attention) {
    return (
      <button
        type="button"
        className="pw-timebar-pill"
        aria-expanded={false}
        aria-label={`Simulated time ${timeText}. Show the time controls`}
        onClick={() => {
          fold(false);
        }}
      >
        <Icon name="history" size={16} />
        <span className="pw-muted">Simulated time</span> <strong>{timeText}</strong>
        <Icon name="tilt-down" size={16} />
      </button>
    );
  }

  return (
    <section className="pw-timebar" aria-label="Simulation time">
      <div className="pw-timebar-row">
        <p className="pw-timebar-time">
          <span className="pw-muted">Simulated time</span> <strong>{timeText}</strong>
        </p>
        <div className="pw-timebar-actions" role="group" aria-label="Move time forward">
          {STEPS.map((s) => (
            <button
              key={s.by}
              type="button"
              className="pw-button-quiet"
              disabled={!idle}
              onClick={() => {
                askShift({ by: s.by }, s.words);
              }}
            >
              {s.label}
            </button>
          ))}
          <button
            type="button"
            className="pw-button-quiet"
            disabled={!idle || !simulation.next_morning}
            onClick={() => {
              if (simulation.next_morning)
                askShift(
                  { by: "next_morning" },
                  `to ${formatSiteTime(simulation.next_morning, zone)}`,
                );
            }}
          >
            Next morning
          </button>
          <button
            type="button"
            className="pw-button-quiet"
            aria-expanded={picking}
            disabled={!idle}
            onClick={() => {
              setPicking(!picking);
            }}
          >
            Choose a time
          </button>
        </div>
        <div className="pw-timebar-actions">
          <button
            type="button"
            className="pw-button-quiet"
            aria-expanded={demoOpen}
            onClick={() => {
              setDemoOpen(!demoOpen);
            }}
          >
            Demo
          </button>
          <button
            type="button"
            className="pw-button-quiet pw-danger"
            disabled={operation !== null || pending !== null}
            onClick={() => {
              ask({
                question: RESET_TEXT,
                confirm: "Reset the simulation",
                danger: true,
                act: async () => {
                  const done = await reset();
                  if (done) {
                    // Only a reset that happened starts the story again.
                    storeStep(0);
                    setDemoRun((n) => n + 1);
                  }
                  return done;
                },
              });
            }}
          >
            <Icon name="rotate-left" size={16} /> Reset
          </button>
          <button
            type="button"
            className="pw-icon-button pw-timebar-fold"
            aria-expanded={true}
            aria-label="Fold the time controls"
            title="Fold the time controls"
            disabled={attention}
            onClick={() => {
              fold(true);
            }}
          >
            <Icon name="tilt-up" size={16} />
          </button>
        </div>
      </div>

      {picking && now && (
        <TimePicker
          min={isoToSiteLocal(now, zone)}
          zone={zone}
          onChoose={(local) => {
            const to = siteLocalToIso(local, zone);
            askShift({ to }, `to ${formatSiteTime(to, zone)}`);
          }}
        />
      )}

      {pending && (
        <div
          className="pw-confirm"
          role="alertdialog"
          aria-label="Confirm"
          aria-describedby="pw-confirm-text"
        >
          <p id="pw-confirm-text">{pending.question}</p>
          <div className="pw-timebar-actions">
            <ConfirmButton
              pending={pending}
              onDone={() => {
                setPending(null);
              }}
            />
            <button
              type="button"
              className="pw-button-quiet"
              onClick={() => {
                setPending(null);
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {operation && (
        <p className="pw-timebar-progress" role="status" aria-live="polite">
          {progressText(operation, zone)}
        </p>
      )}
      {!operation && !ready && (
        <p className="pw-timebar-progress" role="status" aria-live="polite">
          {simulation.data_status === "FAILED"
            ? "The last rebuild failed. Reset to try again."
            : "The site is being rebuilt."}
        </p>
      )}
      {error && (
        <p className="pw-timebar-error" role="alert">
          {error}
        </p>
      )}

      {demoOpen && (
        <DemoPanel
          key={demoRun}
          disabled={!idle}
          onSetup={setup}
          onAsk={onAsk}
          onClose={() => {
            setDemoOpen(false);
          }}
        />
      )}
    </section>
  );
}

function ConfirmButton({ pending, onDone }: { pending: Pending; onDone: () => void }) {
  // The confirmation takes focus, so Enter confirms and Tab reaches Cancel.
  const ref = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    ref.current?.focus();
  }, [pending]);
  return (
    <button
      ref={ref}
      type="button"
      className={pending.danger ? "pw-button pw-danger-fill" : "pw-button"}
      onClick={() => {
        onDone();
        void pending.act();
      }}
    >
      {pending.confirm}
    </button>
  );
}

function TimePicker({
  min,
  zone,
  onChoose,
}: {
  min: string;
  zone: string;
  onChoose: (local: string) => void;
}) {
  const [picked, setValue] = useState(min);
  const value = picked < min ? min : picked; // time moved on since the choice
  return (
    <form
      className="pw-timebar-picker"
      onSubmit={(event) => {
        event.preventDefault();
        if (value > min) onChoose(value);
      }}
    >
      <label>
        <span>Move time to ({zone})</span>
        <input
          type="datetime-local"
          step={900}
          min={min}
          value={value}
          onChange={(event) => {
            setValue(event.target.value);
          }}
        />
      </label>
      <button type="submit" className="pw-button-quiet" disabled={value <= min}>
        Continue
      </button>
    </form>
  );
}
