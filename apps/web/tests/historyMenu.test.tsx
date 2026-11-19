import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Me } from "../src/shared/api/client";
import { WorkspaceProvider } from "../src/shared/workspace/WorkspaceContext";

const order: string[] = [];

vi.mock("../src/shared/api/client", async (original) => ({
  ...(await original<typeof import("../src/shared/api/client")>()),
  fetchConversations: () =>
    Promise.resolve([{ id: "c1", title: "Where is SKU-102?", updated_at: "2026-04-01T06:00:00Z" }]),
  fetchConversation: (_site: string, id: string) => {
    order.push("fetch");
    return Promise.resolve({ id, title: "Where is SKU-102?", runs: [] });
  },
}));

const { HistoryMenu } = await import("../src/features/copilot/HistoryMenu");

afterEach(cleanup);

describe("HistoryMenu", () => {
  it("stops the answer in progress before opening another conversation", async () => {
    order.length = 0;
    const stop = vi.fn(() => {
      order.push("stop");
      return Promise.resolve();
    });
    render(
      <WorkspaceProvider me={{} as Me} siteId="s1" dataReady>
        <HistoryMenu stop={stop} />
      </WorkspaceProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: /History/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Where is SKU-102?" }));
    await waitFor(() => {
      expect(order).toEqual(["stop", "fetch"]);
    });
  });
});
