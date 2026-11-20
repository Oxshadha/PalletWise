import { describe, expect, it } from "vitest";

import { highlightBounds, splitHighlights } from "../src/features/map/geometry";
import type { Highlight, Layout, LayoutBin } from "../src/shared/api/client";

const bin = (id: string, rack: string | null, x: number): LayoutBin => ({
  id,
  code: id,
  zone_id: "z1",
  rack_id: rack,
  bin_role: "PICK",
  bay: rack ? 1 : null,
  level: rack ? 1 : null,
  x,
  y: 5,
  z: 0.5,
  width_m: 1,
  depth_m: 1,
  height_m: 1,
});

const layout = {
  site: { width_m: 80, depth_m: 50 },
  zones: [
    {
      id: "z1",
      code: "B",
      name: "Zone B",
      zone_type: "RESERVE",
      is_prime: false,
      outline: [
        [10, 10],
        [30, 10],
        [30, 20],
        [10, 20],
      ],
    },
  ],
  racks: [],
  bins: [bin("b1", "r1", 1), bin("b2", "r1", 2), bin("b3", null, 3)],
  features: [],
} as unknown as Layout;

const mark = (
  target_type: Highlight["target_type"],
  target_id: string,
  severity: Highlight["severity"],
): Highlight => ({
  target_type,
  target_id,
  reason: "query",
  severity,
});

describe("answer marks (semantic spec 7.6)", () => {
  it("draws a rack as its bins and keeps zones as outlines", () => {
    const marks = splitHighlights(
      [mark("rack", "r1", "info"), mark("zone", "z1", "warning")],
      layout,
    );
    expect(marks.bins.map((h) => [h.target_type, h.target_id])).toEqual([
      ["bin", "b1"],
      ["bin", "b2"],
    ]);
    expect(marks.zones.map((h) => h.target_id)).toEqual(["z1"]);
  });

  it("keeps the most severe highlight of a location named twice", () => {
    const marks = splitHighlights(
      [mark("rack", "r1", "info"), mark("bin", "b1", "critical")],
      layout,
    );
    expect(marks.bins.find((h) => h.target_id === "b1")?.severity).toBe("critical");
  });

  it("frames highlighted zones by their outline", () => {
    const zones = new Map(layout.zones.map((z) => [z.id, z]));
    expect(highlightBounds([], new Map(), [mark("zone", "z1", "info")], zones)).toEqual({
      minX: 10,
      maxX: 30,
      minY: 10,
      maxY: 20,
    });
  });
});
