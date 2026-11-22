import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AdminAgent, AuditRow } from "../src/shared/api/client";

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

vi.mock("../src/shared/api/client", async (original) => ({
  ...(await original<typeof import("../src/shared/api/client")>()),
  fetchAdminAgent: () => Promise.resolve(agent),
  setAgentStatus,
  fetchAudit,
  fetchUsage: () => Promise.resolve([]),
  fetchUnanswered: () => Promise.resolve([]),
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
});
