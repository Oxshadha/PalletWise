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

/** One base state per location (UX spec 5), from the semantic layer: the browser never derives it. */
export type BinState = "NORMAL" | "EMPTY" | "LOW_STOCK" | "OVER_CAPACITY";

type Zone = Layout["zones"][number];

/** What the map draws for an answer: marks on locations, and outlines on zones. */
export interface Marks {
  bins: Highlight[];
  zones: Highlight[];
}

const SEVERITY_RANK = { info: 1, warning: 2, critical: 3 } as const;

/**
 * Splits an answer's highlights for drawing (semantic spec 7.6). A rack target marks every bin
 * of the rack; a zone target stays a zone and is drawn as an outline on the floor. A location
 * named twice keeps its most severe highlight.
 */
export function splitHighlights(highlights: Highlight[], layout: Layout): Marks {
  const rackBins = new Map<string, LayoutBin[]>();
  for (const bin of layout.bins) {
    if (bin.rack_id) rackBins.set(bin.rack_id, [...(rackBins.get(bin.rack_id) ?? []), bin]);
  }
  const bins = new Map<string, Highlight>();
  const zones: Highlight[] = [];
  const mark = (h: Highlight) => {
    const known = bins.get(h.target_id);
    if (!known || SEVERITY_RANK[h.severity] > SEVERITY_RANK[known.severity])
      bins.set(h.target_id, h);
  };
  for (const h of highlights) {
    if (h.target_type === "bin") mark(h);
    else if (h.target_type === "rack") {
      for (const bin of rackBins.get(h.target_id) ?? [])
        mark({ ...h, target_type: "bin", target_id: bin.id });
    } else zones.push(h);
  }
  return { bins: [...bins.values()], zones };
}

export function zoneBounds(zone: Zone): Bounds {
  const xs = zone.outline.map(([x]) => x);
  const ys = zone.outline.map(([, y]) => y);
  return {
    minX: Math.min(...xs),
    maxX: Math.max(...xs),
    minY: Math.min(...ys),
    maxY: Math.max(...ys),
  };
}

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
  zones: Highlight[] = [],
  zoneById: Map<string, Zone> = new Map(),
): Bounds | null {
  let bounds: Bounds | null = null;
  const boxes: Bounds[] = [];
  for (const highlight of highlights) {
    const bin = highlight.target_type === "bin" ? bins.get(highlight.target_id) : undefined;
    if (!bin) continue;
    boxes.push({
      minX: bin.x - bin.width_m / 2,
      maxX: bin.x + bin.width_m / 2,
      minY: bin.y - bin.depth_m / 2,
      maxY: bin.y + bin.depth_m / 2,
    });
  }
  for (const highlight of zones) {
    const zone = zoneById.get(highlight.target_id);
    if (zone && zone.outline.length > 0) boxes.push(zoneBounds(zone));
  }
  for (const box of boxes) {
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

/**
 * Pick activity per location, scaled 0 to 1 against the busiest location for colouring only (the
 * picked units come from the semantic layer). Square-rooted so quieter locations stay visible.
 */
export function heatById(state: MapState | null): Map<string, number> {
  const bins = state?.bins ?? [];
  const top = Math.max(0, ...bins.map((b) => b.picked_7d));
  return new Map(
    top > 0
      ? bins.filter((b) => b.picked_7d > 0).map((b) => [b.bin_id, Math.sqrt(b.picked_7d / top)])
      : [],
  );
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
