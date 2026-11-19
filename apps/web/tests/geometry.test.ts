import { describe, expect, it } from "vitest";

import { highlightBounds, padded, scenePosition } from "../src/features/map/geometry";
import type { Highlight, LayoutBin } from "../src/shared/api/client";
import { formatSiteTime } from "../src/shared/format";

const bin = (id: string, x: number, y: number): LayoutBin => ({
  id,
  code: id,
  zone_id: "z",
  rack_id: null,
  bin_role: "PICK",
  bay: 1,
  level: 1,
  x,
  y,
  z: 0.5,
  width_m: 1,
  depth_m: 2,
  height_m: 1,
});
const highlight = (id: string): Highlight => ({
  target_type: "bin",
  target_id: id,
  reason: "location",
  severity: "info",
});

describe("map geometry", () => {
  it("maps layout coordinates (x, y, z up) to the scene (x, y up, z)", () => {
    expect(scenePosition(bin("a", 3, 7))).toEqual([3, 0.5, 7]);
  });

  it("frames exactly the highlighted bins", () => {
    const bins = new Map([
      ["a", bin("a", 2, 2)],
      ["b", bin("b", 10, 20)],
      ["c", bin("c", 50, 50)],
    ]);
    expect(highlightBounds([highlight("a"), highlight("b")], bins)).toEqual({
      minX: 1.5,
      maxX: 10.5,
      minY: 1,
      maxY: 21,
    });
    expect(highlightBounds([], bins)).toBeNull();
    expect(highlightBounds([highlight("unknown")], bins)).toBeNull();
    expect(padded({ minX: 0, maxX: 1, minY: 0, maxY: 1 }, 2)).toEqual({
      minX: -2,
      maxX: 3,
      minY: -2,
      maxY: 3,
    });
  });
});

describe("site time", () => {
  it("formats in the site time zone like the semantic layer", () => {
    expect(formatSiteTime("2026-04-01T00:30:00Z", "Asia/Colombo")).toBe(
      "06:00, 1 Apr 2026 (Asia/Colombo)",
    );
  });
});
