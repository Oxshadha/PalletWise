import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RichText } from "../src/shared/ui/RichText";

describe("RichText", () => {
  it("bolds key figures and identifiers, keeps markup as text, and makes lists", () => {
    const { container } = render(
      <RichText
        text={
          "SKU-102 has 48 units in A1-01-02 and Zone B.\n\n- **first** item\n- *second* <b>x</b>"
        }
      />,
    );
    const bold = [...container.querySelectorAll("strong")].map((n) => n.textContent);
    expect(bold).toEqual(["SKU-102", "48 units", "A1-01-02", "Zone B", "first"]);
    expect(container.querySelectorAll("li")).toHaveLength(2);
    expect(container.querySelector("em")?.textContent).toBe("second");
    expect(container.querySelector("b")).toBeNull();
    expect(container.textContent).toContain("<b>x</b>");
  });
});
