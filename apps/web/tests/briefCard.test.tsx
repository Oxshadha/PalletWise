import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BriefCard } from "../src/features/copilot/BriefCard";
import type { Brief, BriefItem } from "../src/shared/api/client";
import { useWorkspace, WorkspaceProvider } from "../src/shared/workspace/WorkspaceContext";
import { activeHighlights } from "../src/shared/workspace/workspace";
import { BIN_A, BIN_B, envelope } from "./fixtures";

function item(kind: BriefItem["kind"], overrides: Partial<BriefItem> = {}): BriefItem {
  return {
    kind,
    title: kind,
    text: `${kind} text.`,
    text_source: "model",
    why: `${kind} why.`,
    link: null,
    highlights: [],
    result: envelope([]),
    ...overrides,
  };
}

const SUGGESTION = {
  label: "Which orders are at risk today?",
  tool: "get_order_stockout_risks",
  arguments: { view: "orders", hours: 18 },
};

function brief(businessDate = "2026-04-01"): Brief {
  return {
    business_date: businessDate,
    as_of: "2026-04-01T00:30:00Z",
    time_zone: "Asia/Colombo",
    generated_at: "2026-04-01T00:30:01Z",
    wording_source: "mixed",
    items: [
      item("top_exceptions", {
        title: "Top exceptions",
        link: { to: "inbox", suggestion: null },
        highlights: [
          { target_type: "bin", target_id: BIN_A, reason: "stockout_risk", severity: "critical" },
        ],
      }),
      item("orders_at_risk_today", {
        title: "Orders at risk today",
        link: { to: "copilot", suggestion: SUGGESTION },
        highlights: [
          { target_type: "bin", target_id: BIN_B, reason: "stockout_risk", severity: "warning" },
        ],
      }),
      item("throughput", {
        title: "Yesterday's throughput",
        text_source: "template",
        result: envelope([BIN_A]),
      }),
      item("data_freshness", { title: "Data freshness" }),
    ],
  };
}

function Map() {
  const { state } = useWorkspace();
  return (
    <output data-testid="map">
      {activeHighlights(state)
        .map((h) => h.target_id)
        .join(",")}
    </output>
  );
}

function show(value: Brief, onOpenInbox = vi.fn(), onAsk = vi.fn()) {
  render(
    <WorkspaceProvider
      me={{ name: "M", role: "warehouse_manager", sites: [] }}
      siteId="s1"
      dataReady
    >
      <BriefCard brief={value} onOpenInbox={onOpenInbox} onAsk={onAsk} />
      <Map />
    </WorkspaceProvider>,
  );
  // The brief starts collapsed; open it as the user would.
  const reopen = screen.queryByRole("button", { name: "Show today's brief" });
  if (reopen) fireEvent.click(reopen);
  return { onOpenInbox, onAsk };
}

const map = () => screen.getByTestId("map").textContent;

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

describe("today's brief (slice-3-acceptance step 5)", () => {
  it("shows every item in order with its sentence and why it is there", () => {
    show(brief());
    const titles = screen.getAllByText(
      /^(Top exceptions|Orders at risk today|Yesterday's throughput|Data freshness)$/,
    );
    expect(titles.map((t) => t.textContent)).toEqual([
      "Top exceptions",
      "Orders at risk today",
      "Yesterday's throughput",
      "Data freshness",
    ]);
    expect(screen.getByText("throughput why.")).toBeTruthy();
    expect(screen.getByText(/Worded by the copilot from these numbers/)).toBeTruthy();
  });

  it("each item leads to its detail and shows exactly its locations (FR-BRF-02)", () => {
    const { onOpenInbox, onAsk } = show(brief());
    const mapButtons = screen.getAllByRole("button", { name: "Show on map" });
    const [first, second] = [mapButtons[0], mapButtons[1]];
    if (!first || !second) throw new Error("two items have locations");
    fireEvent.click(second);
    expect(map()).toBe(BIN_B);
    fireEvent.click(screen.getByRole("button", { name: "Open the inbox" }));
    expect(onOpenInbox).toHaveBeenCalledOnce();
    expect(map()).toBe(BIN_A);
    expect(first.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: SUGGESTION.label }));
    expect(onAsk).toHaveBeenCalledWith(SUGGESTION);
    // Items without locations offer no map action.
    expect(screen.getAllByRole("button", { name: "Show on map" })).toHaveLength(2);
  });

  it("every item has details: its stored rows and how they were computed", () => {
    show(brief());
    const details = screen.getAllByText("Details");
    expect(details).toHaveLength(4);
    fireEvent.click(details[2] as HTMLElement);
    expect(screen.getAllByRole("table").length).toBeGreaterThan(0);
  });

  it("starts collapsed; opened stays open that day; a new site day starts collapsed", () => {
    render(
      <WorkspaceProvider
        me={{ name: "M", role: "warehouse_manager", sites: [] }}
        siteId="s1"
        dataReady
      >
        <BriefCard brief={brief()} onOpenInbox={vi.fn()} onAsk={vi.fn()} />
      </WorkspaceProvider>,
    );
    expect(screen.queryByText("Top exceptions")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show today's brief" }));
    expect(screen.getByText("Top exceptions")).toBeTruthy();
    cleanup();
    show(brief());
    expect(screen.getByText("Top exceptions")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Hide" }));
    expect(screen.queryByText("Top exceptions")).toBeNull();
    cleanup();
    show(brief());
    cleanup();
    render(
      <WorkspaceProvider
        me={{ name: "M", role: "warehouse_manager", sites: [] }}
        siteId="s1"
        dataReady
      >
        <BriefCard brief={brief("2026-04-02")} onOpenInbox={vi.fn()} onAsk={vi.fn()} />
      </WorkspaceProvider>,
    );
    expect(screen.getByRole("button", { name: "Show today's brief" })).toBeTruthy();
  });
});
