import { describe, expect, it } from "vitest";

import { spoken } from "../src/shared/speech/speak";

describe("spoken answer text", () => {
  it("reads like a person, without markup or codes written for the eye", () => {
    expect(
      spoken("**SKU-102** has 12,685 units in A1-01-02 · 45% full.\n\n- Zone B\n- *LKR* 300"),
    ).toBe("SKU 102 has 12685 units in A1, 01, 02, 45 percent full. Zone B. rupees 300");
    expect(spoken("Order SO-20260401-OUT022 is late.")).toBe("Order OUT022 is late.");
  });
});
