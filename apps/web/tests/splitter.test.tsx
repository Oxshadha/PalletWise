import { fireEvent, render, screen } from "@testing-library/react";
import { createRef } from "react";
import { describe, expect, it } from "vitest";

import { Splitter } from "../src/app/Splitter";

describe("Splitter", () => {
  it("resizes with the keyboard within its limits and resets on double-click", () => {
    window.localStorage.clear();
    const workspace = createRef<HTMLElement>();
    render(
      <main ref={workspace}>
        <Splitter workspace={workspace} />
      </main>,
    );
    const bar = screen.getByRole("separator", { name: "Resize the map and the side panel" });
    expect(bar.getAttribute("aria-valuenow")).toBe("58");
    fireEvent.keyDown(bar, { key: "ArrowRight" });
    expect(bar.getAttribute("aria-valuenow")).toBe("60");
    expect(workspace.current?.style.getPropertyValue("--pw-split")).toBe("60%");
    fireEvent.keyDown(bar, { key: "End" });
    fireEvent.keyDown(bar, { key: "ArrowRight" });
    expect(bar.getAttribute("aria-valuenow")).toBe("75");
    fireEvent.doubleClick(bar);
    expect(bar.getAttribute("aria-valuenow")).toBe("58");
  });
});
