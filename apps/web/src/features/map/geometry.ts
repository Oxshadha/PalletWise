/**
 * Layout geometry for the scene (UX spec 3). Pure functions over the layout endpoint's data:
 * positions and framing only, never inventory values.
 *
 * Layout coordinates: x along the site width, y along its depth, z up; bin x, y, z are centres.
 * Three.js coordinates: x right, y up, z towards the viewer, so (x, y, z) maps to (x, z, y).
 */
import type { Highlight, Layout, LayoutBin, MapState } from "../../shared/api/client";

export interface Bounds {
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
}

export type BinState = "NORMAL" | "EMPTY";

export function scenePosition(bin: LayoutBin): [number, number, number] {
  return [bin.x, bin.z, bin.y];
}

export function sceneSize(bin: LayoutBin): [number, number, number] {
  return [bin.width_m, bin.height_m, bin.depth_m];
}

export function siteBounds(layout: Layout): Bounds {
  return { minX: 0, maxX: layout.site.width_m, minY: 0, maxY: layout.site.depth_m };
}

/** The box framing every highlighted bin (agent spec 10: focus), or null with nothing to frame. */
export function highlightBounds(
  highlights: Highlight[],
  bins: Map<string, LayoutBin>,
): Bounds | null {
  let bounds: Bounds | null = null;
  for (const highlight of highlights) {
    const bin = highlight.target_type === "bin" ? bins.get(highlight.target_id) : undefined;
    if (!bin) continue;
    const box = {
      minX: bin.x - bin.width_m / 2,
      maxX: bin.x + bin.width_m / 2,
      minY: bin.y - bin.depth_m / 2,
      maxY: bin.y + bin.depth_m / 2,
    };
    bounds = bounds
      ? {
          minX: Math.min(bounds.minX, box.minX),
          maxX: Math.max(bounds.maxX, box.maxX),
          minY: Math.min(bounds.minY, box.minY),
          maxY: Math.max(bounds.maxY, box.maxY),
        }
      : box;
  }
  return bounds;
}

/** Adds a margin so framed locations are not at the very edge of the view. */
export function padded(bounds: Bounds, margin: number): Bounds {
  return {
    minX: bounds.minX - margin,
    maxX: bounds.maxX + margin,
    minY: bounds.minY - margin,
    maxY: bounds.maxY + margin,
  };
}

export function binsById(layout: Layout): Map<string, LayoutBin> {
  return new Map(layout.bins.map((b) => [b.id, b]));
}

export function statesById(state: MapState | null): Map<string, BinState> {
  return new Map((state?.bins ?? []).map((b) => [b.bin_id, b.state]));
}

export function onHandById(state: MapState | null): Map<string, number> {
  return new Map((state?.bins ?? []).map((b) => [b.bin_id, b.on_hand]));
}

export function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

export function webglAvailable(): boolean {
  try {
    const canvas = document.createElement("canvas");
    return Boolean(canvas.getContext("webgl2") ?? canvas.getContext("webgl"));
  } catch {
    return false;
  }
}
