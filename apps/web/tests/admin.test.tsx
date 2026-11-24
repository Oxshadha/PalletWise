import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  AdminAgent,
  AuditRow,
  Feedback,
  SettingsValues,
  SiteSettings,
} from "../src/shared/api/client";

let agent: AdminAgent = {
  id: "copilot",
  version: "1",
  status: "ACTIVE",
  primary_model: "gemini",
  prompt_version: "v2",
  daily_token_budget: 2000000,
  used_tokens_today: 1234,
  governed_query_enabled: true,
  governed_query_evaluated: false,
};
const setAgentStatus = vi.fn((status: "ACTIVE" | "SUSPENDED") => {
  agent = { ...agent, status };
  return Promise.resolve();
});
const audit: AuditRow[] = [
  {
    occurred_at: "2026-11-21T10:00:00Z",
    actor_type: "AGENT",
    user_subject: "manager",
    user_name: "Nadia Perera",
    user_role: "warehouse_manager",
    agent_id: "copilot",
    agent_version: "1",
    model: "gemini",
    prompt_version: "v2",
    run_id: null,
    action: "tool.call",
    scope: "inventory.read",
    tool: "get_low_stock",
    decision: "ALLOW",
    deny_reasons: null,
    outcome: "OK",
    row_count: 3,
    latency_ms: 40,
    cost_estimated: null,
    trace_id: "0123456789abcdef0123456789abcdef",
    parameters: null,
  },
];
const fetchAudit = vi.fn(() => Promise.resolve(audit));
const feedback: Feedback = {
  helpful: 4,
  not_helpful: 1,
  rows: [
    {
      created_at: "2026-11-21T10:00:00Z",
      rating: "NOT_HELPFUL",
      comment: "wrong bin",
      question: "Where is SKU-102 stored?",
      tools: ["find_product_locations"],
      outcome: "ANSWER",
      trace_id: "0123456789abcdef0123456789abcdef",
    },
  ],
};
const fetchFeedback = vi.fn(() => Promise.resolve(feedback));
const values: SettingsValues = {
  dead_stock_days: 30,
  slow_in_prime_days: 14,
  capacity_threshold_pct: 90,
  stockout_horizon_hours: 24,
  trailing_window_operating_days: 28,
  impact_lines_cap: 20,
  impact_hours_cap: 48,
  impact_value_cap: 1000000,
  impact_weight_lines: 1,
  impact_weight_urgency: 1,
  impact_weight_value: 1,
};
const settings: SiteSettings = {
  version_no: 2,
  values,
  fields: Object.keys(values).map((key) => ({
    key,
    group: key.startsWith("impact") ? ("impact" as const) : ("rules" as const),
    label: key === "dead_stock_days" ? "Dead-stock period" : key,
    affects: `affects ${key}`,
    unit: "",
    step: 1,
  })),
  history: [{ version_no: 2, saved_at: "2026-11-21T10:00:00Z", saved_by: "System", note: null }],
};
const saveSettings = vi.fn(() => Promise.resolve({ outcome: "changed" as const, version_no: 3 }));

vi.mock("../src/shared/api/client", async (original) => ({
  ...(await original<typeof import("../src/shared/api/client")>()),
  fetchAdminAgent: () => Promise.resolve(agent),
  setAgentStatus,
  fetchAudit,
  fetchUsage: () => Promise.resolve([]),
  fetchUnanswered: () => Promise.resolve([]),
  fetchFeedback,
  fetchSettings: () => Promise.resolve(settings),
  saveSettings,
}));

const { AdminPanel } = await import("../src/features/admin/AdminPanel");

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("admin panel", () => {
  it("suspends the copilot only after a confirmation", async () => {
    render(<AdminPanel />);
    expect(await screen.findByText("Active")).toBeTruthy();
    expect(screen.getByRole("img", { name: /used today/u })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Suspend the copilot" }));
    expect(setAgentStatus).not.toHaveBeenCalled();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
      await Promise.resolve();
    });
    expect(setAgentStatus).toHaveBeenCalledWith("SUSPENDED");
    expect(await screen.findByText("Suspended")).toBeTruthy();
  });

  it("filters the audit log and exports the same view", async () => {
    render(<AdminPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Audit log" }));
    expect(await screen.findByText("get_low_stock")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Decision"), { target: { value: "DENY" } });
    await act(() => Promise.resolve());
    expect(fetchAudit).toHaveBeenLastCalledWith({ decision: "DENY" }, expect.anything());
    expect(screen.getByRole("link", { name: "Export CSV" }).getAttribute("href")).toBe(
      "/api/admin/audit.csv?decision=DENY",
    );
  });

  it("shows answer feedback with its question and copies an evaluation case", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    Object.assign(navigator, { clipboard: { writeText } });
    render(<AdminPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Answer feedback" }));
    expect(await screen.findByText("Where is SKU-102 stored?")).toBeTruthy();
    expect(screen.getByText("wrong bin")).toBeTruthy();
    expect(fetchFeedback).toHaveBeenLastCalledWith(30, "NOT_HELPFUL", expect.anything());
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Copy as evaluation case" }));
      await Promise.resolve();
    });
    const draft = JSON.parse(String((writeText.mock.calls[0] as unknown[])[0])) as {
      question: string;
      gold: { tool: string };
    };
    expect(draft.question).toBe("Where is SKU-102 stored?");
    expect(draft.gold.tool).toBe("find_product_locations");
  });

  it("saves changed settings only after a confirmation, from the version it started on", async () => {
    render(<AdminPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    const field = await screen.findByLabelText("Dead-stock period");
    expect(screen.getByRole("button", { name: "Save settings" })).toHaveProperty("disabled", true);
    fireEvent.change(field, { target: { value: "45" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(saveSettings).not.toHaveBeenCalled();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
      await Promise.resolve();
    });
    expect(saveSettings).toHaveBeenCalledWith(2, { ...values, dead_stock_days: 45 }, null);
  });
});
