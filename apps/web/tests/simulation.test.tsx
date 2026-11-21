import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Inbox, Simulation } from "../src/shared/api/client";
import { useWorkspace, WorkspaceProvider } from "../src/shared/workspace/WorkspaceContext";

/** Slice 4 step 5: the time shift bar, the guided demo and the bell. */

let simulation: Simulation;
let inbox: Inbox;
let changes = 0; // what the site stream reports; a test bumps it and rerenders

const shiftTime = vi.fn(() =>
  Promise.resolve({
    from_time: simulation.site_time ?? "",
    to: "2026-04-01T04:30:00Z",
    records: 9,
  }),
);
const runScenario = vi.fn(() =>
  Promise.resolve({
    scenario: "outlet_window_at_risk",
    title: "A large outlet order at risk",
    events: 2,
    from_time: "2026-04-01T00:30:00Z",
    to: "2026-04-01T00:45:00Z",
    records: 12,
    tour: [],
  }),
);
const resetSimulation = vi.fn(() =>
  Promise.resolve({
    outcome: "reset" as const,
    new_run: "r2",
    clock_start: "2026-04-01T00:30:00Z",
    data_status: "REBUILDING",
  }),
);

vi.mock("../src/shared/api/client", async (original) => ({
  ...(await original<typeof import("../src/shared/api/client")>()),
  fetchSimulation: () => Promise.resolve(simulation),
  fetchExceptions: () => Promise.resolve(inbox),
  shiftTime,
  runScenario,
  resetSimulation,
}));
vi.mock("../src/shared/api/siteEvents", () => ({ useSiteChanges: () => changes }));

const { SimulationControls } = await import("../src/features/simulation/SimulationControls");
const { Bell } = await import("../src/features/simulation/Bell");

function base(overrides: Partial<Simulation> = {}): Simulation {
  return {
    mode: "SIMULATED",
    data_status: "READY",
    site_time: "2026-04-01T00:30:00Z",
    time_zone: "Asia/Colombo",
    simulator_time: "2026-04-01T00:30:00Z",
    shifting: false,
    next_morning: "2026-04-02T00:30:00Z",
    can_control: true,
    ...overrides,
  };
}

function Probe() {
  const { state } = useWorkspace();
  return <div data-testid="asked">{state.pending?.question ?? ""}</div>;
}

const onAsked = vi.fn();

function controls() {
  const tree = () => (
    <WorkspaceProvider
      me={{ name: "M", role: "warehouse_manager", sites: [] }}
      siteId="s1"
      dataReady
    >
      <SimulationControls onAsked={onAsked} />
      <Probe />
    </WorkspaceProvider>
  );
  const view = render(tree());
  /** The site stream reports a change: the bar refetches the state. */
  const changed = async (next: Simulation) => {
    simulation = next;
    changes += 1;
    await act(async () => {
      view.rerender(tree());
      await Promise.resolve();
    });
  };
  return { changed };
}

beforeEach(() => {
  simulation = base();
  inbox = { as_of: "2026-04-01T00:30:00Z", time_zone: "Asia/Colombo", exceptions: [] };
  changes = 0;
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("time shift bar (slice-4-acceptance step 5)", () => {
  it("shows the site time, the shift buttons, the demo and reset to a holder of simulation.control", async () => {
    controls();
    expect(await screen.findByRole("region", { name: "Simulation time" })).toBeTruthy();
    for (const name of [
      "+1 hour",
      "+4 hours",
      "+1 day",
      "+1 week",
      "Next morning",
      "Choose a time",
      "Demo",
      "Reset",
    ])
      expect(screen.getByRole("button", { name })).toBeTruthy();
    expect(screen.getByText(/06:00, 1 Apr 2026/u)).toBeTruthy();
  });

  it("shows nothing to a viewer without simulation.control, or on a real site", async () => {
    simulation = base({ can_control: false });
    controls();
    await act(() => Promise.resolve());
    expect(screen.queryByRole("region", { name: "Simulation time" })).toBeNull();
    cleanup();
    simulation = base({ mode: "REAL" });
    controls();
    await act(() => Promise.resolve());
    expect(screen.queryByRole("region", { name: "Simulation time" })).toBeNull();
  });

  it("confirms a shift before it is sent, then shows progress until the clock arrives", async () => {
    const { changed } = controls();
    fireEvent.click(await screen.findByRole("button", { name: "+4 hours" }));
    expect(screen.getByRole("alertdialog").textContent).toContain("Move time forward 4 hours?");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(shiftTime).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "+4 hours" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Move time" }));
      await Promise.resolve();
    });
    expect(shiftTime).toHaveBeenCalledWith("s1", { by: "4h" });
    // Emitted, not yet ingested: the bar says where time is going, and the buttons wait.
    await changed(base({ simulator_time: "2026-04-01T04:30:00Z", shifting: true }));
    expect(screen.getByRole("status").textContent).toBe(
      "Moving time to 10:00, 1 Apr 2026 (Asia/Colombo)",
    );
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "+1 hour" }).disabled).toBe(true);
    // The ingested clock reached the target: done.
    await changed(
      base({ site_time: "2026-04-01T04:30:00Z", simulator_time: "2026-04-01T04:30:00Z" }),
    );
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "+1 hour" }).disabled).toBe(false);
  });

  it("says what a reset discards before it resets, and shows the rebuild until READY", async () => {
    const { changed } = controls();
    fireEvent.click(await screen.findByRole("button", { name: "Reset" }));
    const dialog = screen.getByRole("alertdialog");
    expect(dialog.textContent).toContain("Every order, movement, exception and acknowledgement");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Reset the simulation" }));
      await Promise.resolve();
    });
    expect(resetSimulation).toHaveBeenCalledWith("s1");
    await changed(base({ data_status: "REBUILDING", simulator_time: null }));
    expect(screen.getByRole("status").textContent).toContain("Resetting");
    await changed(base());
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("shows the API's refusal, and a time can be chosen", async () => {
    const { ApiProblem } = await import("../src/shared/api/client");
    shiftTime.mockRejectedValueOnce(
      new ApiProblem(409, "SIMULATION_BUSY", "Another time shift or reset is running.", null),
    );
    controls();
    fireEvent.click(await screen.findByRole("button", { name: "Choose a time" }));
    fireEvent.change(screen.getByLabelText("Move time to (Asia/Colombo)"), {
      target: { value: "2026-04-01T09:00" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(screen.getByRole("alertdialog").textContent).toContain("to 09:00, 1 Apr 2026");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Move time" }));
      await Promise.resolve();
    });
    expect(shiftTime).toHaveBeenCalledWith("s1", { to: "2026-04-01T03:30:00.000Z" });
    expect(screen.getByRole("alert").textContent).toBe("Another time shift or reset is running.");
  });
});

describe("guided demo (D6)", () => {
  it("walks the storyline; a step's setup is confirmed, and its prompts ask the copilot", async () => {
    controls();
    fireEvent.click(await screen.findByRole("button", { name: "Demo" }));
    expect(screen.getByText("Guided demo, step 1 of 8")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Start of shift" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByRole("heading", { name: "A big outlet order arrives" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Add the outlet order" }));
    expect(runScenario).not.toHaveBeenCalled(); // confirmed first
    await act(async () => {
      fireEvent.click(
        within(screen.getByRole("alertdialog")).getByRole("button", {
          name: "Add the outlet order",
        }),
      );
      await Promise.resolve();
    });
    expect(runScenario).toHaveBeenCalledWith("s1", "outlet_window_at_risk");

    fireEvent.click(screen.getByRole("button", { name: "Which of today's orders are at risk?" }));
    expect(screen.getByTestId("asked").textContent).toBe("Which of today's orders are at risk?");
    expect(onAsked).toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.getByRole("heading", { name: "Find anything" })).toBeTruthy();

    // A reset starts the story again at step 1.
    fireEvent.click(screen.getByRole("button", { name: "Reset" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Reset the simulation" }));
      await Promise.resolve();
    });
    expect(screen.getByRole("heading", { name: "Start of shift" })).toBeTruthy();
  });
});

describe("guided demo after a refused reset", () => {
  it("stays on its step when the reset is refused", async () => {
    const { ApiProblem } = await import("../src/shared/api/client");
    resetSimulation.mockRejectedValueOnce(
      new ApiProblem(409, "SIMULATION_BUSY", "Another time shift or reset is running.", null),
    );
    controls();
    fireEvent.click(await screen.findByRole("button", { name: "Demo" }));
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    fireEvent.click(screen.getByRole("button", { name: "Reset" }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Reset the simulation" }));
      await Promise.resolve();
    });
    expect(screen.getByRole("alert").textContent).toBe("Another time shift or reset is running.");
    expect(screen.getByRole("heading", { name: "Find anything" })).toBeTruthy();
  });
});

describe("notification bell (D8)", () => {
  const exception = (id: string) =>
    ({ id, status: "OPEN", highlights: [] }) as unknown as Inbox["exceptions"][number];

  function bell(inboxOpen: boolean) {
    return <Bell siteId="s1" inboxOpen={inboxOpen} onOpenInbox={vi.fn()} />;
  }

  it("shows a dot for an exception opened since the inbox was last shown, and clears it when shown", async () => {
    inbox = { ...inbox, exceptions: [exception("a")] };
    const view = render(bell(false));
    await act(() => Promise.resolve());
    // A first visit takes what is there as seen.
    expect(screen.getByRole("button").getAttribute("aria-label")).toContain("nothing new");

    inbox = { ...inbox, exceptions: [exception("a"), exception("b")] };
    changes += 1;
    await act(async () => {
      view.rerender(bell(false));
      await Promise.resolve();
    });
    expect(screen.getByRole("button").getAttribute("aria-label")).toContain("1 new exception");

    view.rerender(bell(true));
    await act(() => Promise.resolve());
    view.rerender(bell(false));
    expect(screen.getByRole("button").getAttribute("aria-label")).toContain("nothing new");
  });
});
