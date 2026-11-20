import { describe, expect, it } from "vitest";

import { ALL_KINDS, apply } from "../src/shared/api/siteEvents";

describe("site stream hints (agent spec 9.2, slice 3 acceptance 2.1)", () => {
  it("refetches everything on connect and reconnect", () => {
    expect(apply(null, { type: "hello", id: 7 })).toEqual({ refetch: [...ALL_KINDS], applied: 7 });
    expect(apply(9, { type: "hello", id: 12 }).refetch).toEqual([...ALL_KINDS]);
  });

  it("refetches only the kinds that changed", () => {
    const result = apply(7, { type: "change", id: 8, kinds: ["inventory"], complete: true });
    expect(result).toEqual({ refetch: ["inventory"], applied: 8 });
  });

  it("ignores repeated and late events", () => {
    expect(apply(8, { type: "change", id: 8, kinds: ["inventory"], complete: true })).toEqual({
      refetch: [],
      applied: 8,
    });
    expect(apply(8, { type: "change", id: 5, kinds: ["clock"], complete: true }).refetch).toEqual(
      [],
    );
  });

  it("refetches everything when the server's history does not reach back", () => {
    const result = apply(3, { type: "change", id: 2000, kinds: ["clock"], complete: false });
    expect(result.refetch).toEqual([...ALL_KINDS]);
  });

  it("treats a heartbeat ahead of what was applied as a lost hint", () => {
    expect(apply(8, { type: "heartbeat", id: 9 })).toEqual({ refetch: [...ALL_KINDS], applied: 9 });
    expect(apply(9, { type: "heartbeat", id: 9 }).refetch).toEqual([]);
  });

  it("never asks for a kind it does not know", () => {
    const result = apply(1, {
      type: "change",
      id: 2,
      kinds: ["inventory", "unknown"],
      complete: true,
    });
    expect(result.refetch).toEqual(["inventory"]);
  });
});
