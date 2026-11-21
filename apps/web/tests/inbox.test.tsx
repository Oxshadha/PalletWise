import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Inbox, InboxException, Me } from "../src/shared/api/client";
import { useWorkspace, WorkspaceProvider } from "../src/shared/workspace/WorkspaceContext";
import { activeHighlights } from "../src/shared/workspace/workspace";
import { BIN_A, BIN_B } from "./fixtures";

const acknowledged = vi.fn((_site: string, id: string) =>
  Promise.resolve({
    id,
    status: "ACKNOWLEDGED" as const,
    acknowledged_by: "m",
    acknowledged_at: "2026-04-01T06:00:00Z",
  }),
);

function exception(id: string, overrides: Partial<InboxException> = {}): InboxException {
  return {
    id,
    exception_type: "STOCKOUT_RISK",
    status: "OPEN",
    subject_key: `product:${id}`,
    subject_label: `SKU-${id} Wireless Mouse`,
    product_id: null,
    zone_id: null,
    order_id: null,
    severity: "warning",
    score: 57.2,
    components: {
      lines: 5,
      hours_until_due: 12,
      value_at_risk: 20000,
      lines_normalized: 0.25,
      urgency_normalized: 0.75,
      value_normalized: 0.7168,
      lines_cap: 20,
      hours_cap: 48,
      value_cap: 1000000,
      weight_lines: 1,
      weight_urgency: 1,
      weight_value: 1,
    },
    settings_version_id: "v",
    first_detected_at: "2026-04-01T05:00:00Z",
    last_evaluated_at: "2026-04-01T06:00:00Z",
    acknowledged_by: null,
    acknowledged_at: null,
    highlights: [
      { target_type: "bin", target_id: BIN_A, reason: "stockout_risk", severity: "warning" },
    ],
    orders: [{ order_id: null, order_no: "SO-1001" }],
    ...overrides,
  };
}

const INBOX: Inbox = {
  as_of: "2026-04-01T06:00:00Z",
  time_zone: "Asia/Colombo",
  exceptions: [
    exception("101", {
      severity: "critical",
      score: 80,
      highlights: [
        { target_type: "bin", target_id: BIN_B, reason: "stockout_risk", severity: "critical" },
      ],
    }),
    exception("102"),
    exception("103", { exception_type: "DEAD_STOCK", severity: "info", score: 10 }),
  ],
};

vi.mock("../src/shared/api/client", async (original) => ({
  ...(await original<typeof import("../src/shared/api/client")>()),
  fetchExceptions: () => Promise.resolve(INBOX),
  acknowledgeException: acknowledged,
}));

const { InboxPanel } = await import("../src/features/inbox/InboxPanel");

/** What the map would draw, and the question handed to the copilot. */
function Probe() {
  const { state } = useWorkspace();
  return (
    <output data-testid="probe">
      {JSON.stringify({
        map: activeHighlights(state).map((h) => [h.target_id, h.severity]),
        pending: state.pending,
      })}
    </output>
  );
}

const probe = () =>
  JSON.parse(screen.getByTestId("probe").textContent) as {
    map: [string, string][];
    pending: { question: string; pageContext: Record<string, string> } | null;
  };

function show(role: Me["role"] = "warehouse_manager", onAskCopilot = vi.fn()) {
  render(
    <WorkspaceProvider me={{ name: "M", role, sites: [] }} siteId="s1" dataReady>
      <InboxPanel onAskCopilot={onAskCopilot} onShowOnMap={vi.fn()} />
      <Probe />
    </WorkspaceProvider>,
  );
  return onAskCopilot;
}

const rows = async () => {
  const list = await screen.findByRole("list", { name: "Exceptions, highest impact first" });
  return within(list).getAllByRole("button", { expanded: false });
};

/** The nth row; fails clearly when the list is shorter. */
const row = async (n: number): Promise<HTMLElement> => {
  const found = (await rows())[n];
  if (!found) throw new Error(`no row ${String(n)}`);
  return found;
};

afterEach(() => {
  cleanup();
  acknowledged.mockClear();
});

describe("exceptions inbox (slice-3-acceptance step 4)", () => {
  it("lists in the order the API ranked, with type, subject, score and severity in words", async () => {
    show();
    const found = await rows();
    expect(found.map((r) => r.textContent)).toEqual([
      "CriticalStockout riskSKU-101 Wireless MouseScore 80",
      "WarningStockout riskSKU-102 Wireless MouseScore 57.2",
      "InformationDead stockSKU-103 Wireless MouseScore 10",
    ]);
    expect(screen.getByText("3 open exceptions, ranked by impact").getAttribute("role")).toBe(
      "status",
    );
  });

  it("choosing one shows exactly its highlights on the map and why it is ranked here", async () => {
    show();
    fireEvent.click(await row(1));
    expect(probe().map).toEqual([[BIN_A, "warning"]]);
    const table = screen.getByRole("table");
    expect(within(table).getByText(/score 57.2 of 100/)).toBeTruthy();
    const factors = within(table)
      .getAllByRole("row")
      .slice(1)
      .map((r) => [...r.children].map((c) => c.textContent));
    expect(factors).toEqual([
      ["Order lines affected", "5 (full weight at 20)", "0.25", "1"],
      ["Urgency", "Due in 12 hours (full weight when overdue, none beyond 48 hours)", "0.75", "1"],
      ["Value at risk", "20,000 (full weight at 1,000,000)", "0.72", "1"],
    ]);
    expect(screen.getByText("SO-1001")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { expanded: true }));
    expect(probe().map).toEqual([]);
  });

  it("arrow keys, Home and End move through the list", async () => {
    show();
    const [first, second, third] = [await row(0), await row(1), await row(2)];
    first.focus();
    fireEvent.keyDown(first, { key: "ArrowDown" });
    expect(document.activeElement).toBe(second);
    fireEvent.keyDown(second, { key: "End" });
    expect(document.activeElement).toBe(third);
    fireEvent.keyDown(third, { key: "ArrowDown" });
    expect(document.activeElement).toBe(third);
    fireEvent.keyDown(third, { key: "Home" });
    expect(document.activeElement).toBe(first);
  });

  it("a Manager acknowledges; an Admin sees no acknowledge action", async () => {
    show();
    fireEvent.click(await row(0));
    fireEvent.click(screen.getByRole("button", { name: "Acknowledge" }));
    expect(acknowledged).toHaveBeenCalledWith("s1", "101");
    cleanup();
    show("admin");
    fireEvent.click(await row(0));
    expect(screen.queryByRole("button", { name: "Acknowledge" })).toBeNull();
    expect(screen.getByRole("button", { name: "Ask the copilot" })).toBeTruthy();
  });

  it("asks the copilot with the exception as page context", async () => {
    const onAsk = show();
    fireEvent.click(await row(1));
    fireEvent.click(screen.getByRole("button", { name: "Ask the copilot" }));
    expect(onAsk).toHaveBeenCalledOnce();
    expect(probe().pending).toEqual({
      question: "What should I do about the stockout risk for SKU-102 Wireless Mouse?",
      pageContext: { selected_exception_id: "102", selected_exception_type: "STOCKOUT_RISK" },
    });
  });
});
