import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { AnswerCard } from "../src/features/copilot/AnswerCard";
import type { Answer } from "../src/shared/workspace/workspace";
import { BIN_A, BIN_B, envelope } from "./fixtures";

function answer(overrides: Partial<Answer> = {}): Answer {
  return {
    id: "a",
    runId: "r",
    question: "Where is SKU-102 stored?",
    status: "done",
    path: "B",
    outcome: "ANSWER",
    text: "SKU-102 is stored in 2 locations.",
    progress: null,
    results: [envelope([BIN_A, BIN_B], 12)],
    suggestions: [],
    error: null,
    traceId: "t",
    historical: false,
    lastSeq: 9,
    ...overrides,
  };
}

const noop = () => undefined;

afterEach(cleanup);

describe("answer card (UX spec 7)", () => {
  it("leads with the result and never shows tool internals to the Manager", () => {
    const { container } = render(
      <AnswerCard
        answer={answer()}
        siteId="s"
        active={false}
        onShowOnMap={noop}
        onSuggestion={noop}
      />,
    );
    expect(screen.getByText("SKU-102 is stored in 2 locations.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Show on map (2 locations)" })).toBeTruthy();
    expect(container.textContent).not.toContain("find_product_locations");
    expect(container.textContent).not.toContain("normalized_request");
  });

  it("shows up to 10 rows with a way to see all", () => {
    render(
      <AnswerCard
        answer={answer()}
        siteId="s"
        active={false}
        onShowOnMap={noop}
        onSuggestion={noop}
      />,
    );
    expect(screen.getAllByRole("row")).toHaveLength(11); // header and 10 rows
    expect(screen.getByRole("button", { name: "Show all 12" })).toBeTruthy();
  });

  it("marks reopened answers as historical with their data time", () => {
    render(
      <AnswerCard
        answer={answer({ historical: true })}
        siteId="s"
        active={false}
        onShowOnMap={noop}
        onSuggestion={noop}
      />,
    );
    expect(
      screen.getByText("Historical: data as of 06:00, 1 Apr 2026 (Asia/Colombo)"),
    ).toBeTruthy();
  });

  it("shows errors with their reference", () => {
    render(
      <AnswerCard
        answer={answer({
          status: "error",
          text: "",
          results: [],
          error: { code: "X", message: "It failed.", traceId: "abc123" },
        })}
        siteId="s"
        active={false}
        onShowOnMap={noop}
        onSuggestion={noop}
      />,
    );
    expect(screen.getByRole("alert").textContent).toContain("Reference: abc123");
  });

  it("formats numbers with their units and names the settings version", () => {
    const result = envelope([BIN_A], 1);
    const priced = {
      ...result,
      columns: [
        ...result.columns,
        { key: "stock_value", label: "Stock value", type: "number" as const, unit: "LKR" },
      ],
      rows: [{ ...result.rows[0], stock_value: "12500.00" }],
      evidence: { ...result.evidence, settings_version_no: 2 },
    };
    const { container } = render(
      <AnswerCard
        answer={answer({ results: [priced] })}
        siteId="s"
        active={false}
        onShowOnMap={noop}
        onSuggestion={noop}
      />,
    );
    expect(screen.getByText("Stock value (LKR)")).toBeTruthy();
    expect(screen.getByText("12,500")).toBeTruthy();
    expect(container.textContent).toContain("Settings version 2.");
  });

  it("shows live progress while the answer is prepared, and the answer once it arrives", () => {
    const { rerender } = render(
      <AnswerCard
        answer={answer({
          status: "streaming",
          text: "",
          results: [],
          progress: "Checking pick locations against their minimums",
        })}
        siteId="s"
        active={false}
        onShowOnMap={() => undefined}
        onSuggestion={() => undefined}
      />,
    );
    expect(screen.getByText("Checking pick locations against their minimums")).toBeTruthy();
    rerender(
      <AnswerCard
        answer={answer({ status: "streaming", progress: "Writing the answer" })}
        siteId="s"
        active={false}
        onShowOnMap={() => undefined}
        onSuggestion={() => undefined}
      />,
    );
    expect(screen.queryByText("Writing the answer")).toBeNull();
    expect(screen.getByText("SKU-102 is stored in 2 locations.")).toBeTruthy();
  });
});
