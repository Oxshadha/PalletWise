import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SearchResult } from "../src/shared/api/client";
import { useWorkspace, WorkspaceProvider } from "../src/shared/workspace/WorkspaceContext";

function result(kind: SearchResult["kind"], code: string, label: string | null): SearchResult {
  const tool = {
    product: "find_product_locations",
    bin: "get_bin_contents",
    order: "get_order_status",
  }[kind];
  const argument = { product: "sku", bin: "bin_code", order: "order_no" }[kind];
  return {
    kind,
    id: `${kind}-${code}`,
    code,
    label,
    match: "prefix",
    suggestion: { label: `Ask about ${code}`, tool, arguments: { [argument]: code } },
  };
}

const calls: string[] = [];
const RESULTS = [
  result("product", "SKU-10", "Wireless mouse"),
  result("bin", "A1-01-02", "Zone A"),
  result("order", "SO-1042", "OUT12"),
];

vi.mock("../src/shared/api/client", async (original) => ({
  ...(await original<typeof import("../src/shared/api/client")>()),
  searchSite: (_site: string, q: string) => {
    calls.push(q);
    return Promise.resolve({ query: q, results: q === "nothing" ? [] : RESULTS });
  },
}));

const { Search } = await import("../src/features/search/SearchBox");

function Pending() {
  const { state } = useWorkspace();
  return <output data-testid="pending">{JSON.stringify(state.pending)}</output>;
}

function show(onAsked = vi.fn()) {
  render(
    <WorkspaceProvider
      me={{ name: "M", role: "warehouse_manager", sites: [] }}
      siteId="s1"
      dataReady
    >
      <Search onAsked={onAsked} />
      <Pending />
    </WorkspaceProvider>,
  );
  return onAsked;
}

const box = () => screen.getByRole("combobox", { name: "Search for a SKU, location or order" });

afterEach(() => {
  cleanup();
  calls.length = 0;
});

describe("search (slice-3-acceptance step 6)", () => {
  it("results follow typing after a pause, one request per pause", async () => {
    show();
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    for (const text of ["S", "SK", "SKU"]) fireEvent.change(box(), { target: { value: text } });
    const options = await screen.findAllByRole("option");
    expect(calls).toEqual(["SKU"]);
    expect(options.map((o) => o.textContent)).toEqual([
      "ProductSKU-10Wireless mouse",
      "LocationA1-01-02Zone A",
      "OrderSO-1042OUT12",
    ]);
  });

  it("arrow keys move, Enter asks the copilot the result's fast-path question", async () => {
    const onAsked = show();
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    fireEvent.change(box(), { target: { value: "SO" } });
    await screen.findAllByRole("option");
    fireEvent.keyDown(box(), { key: "ArrowDown" });
    fireEvent.keyDown(box(), { key: "ArrowDown" });
    expect(screen.getAllByRole("option")[2]?.getAttribute("aria-selected")).toBe("true");
    fireEvent.keyDown(box(), { key: "Enter" });
    expect(onAsked).toHaveBeenCalledOnce();
    expect(JSON.parse(screen.getByTestId("pending").textContent)).toEqual({
      question: "Ask about SO-1042",
      suggestion: {
        label: "Ask about SO-1042",
        tool: "get_order_status",
        arguments: { order_no: "SO-1042" },
      },
    });
    expect(screen.queryByRole("dialog")).toBeNull(); // the box closes once chosen
  });

  it("says when nothing matches, and Escape or Ctrl K close and open it", async () => {
    show();
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    fireEvent.change(box(), { target: { value: "nothing" } });
    await waitFor(() => {
      expect(screen.getByText('Nothing matches "nothing".')).toBeTruthy();
    });
    fireEvent.keyDown(box(), { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    // Closing asks nothing, so an answer already streaming in the copilot carries on.
    expect(screen.getByTestId("pending").textContent).toBe("null");
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Search" }));
  });
});
