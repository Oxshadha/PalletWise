import {
  type PointerEvent,
  type Ref,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type WheelEvent,
} from "react";

import type { Highlight, Layout, LayoutBin } from "../../shared/api/client";
import { type BinState, type Bounds, padded, siteBounds } from "./geometry";

export interface PlanHandle {
  zoom: (factor: number) => void;
  recenter: () => void;
}

interface Props {
  layout: Layout;
  states: Map<string, BinState>;
  highlights: Highlight[];
  zoneHighlights: Highlight[];
  selectedBinId: string | null;
  focus: Bounds | null;
  onSelect: (binId: string) => void;
  ref?: Ref<PlanHandle>;
}

interface View {
  x: number;
  y: number;
  w: number;
  h: number;
}

const SEVERITY_RANK = { info: 1, warning: 2, critical: 3 } as const;
const PLAN_LABEL_METRES = 1.2;
const MARKER_METRES = 1.6;

function viewOf(bounds: Bounds): View {
  return {
    x: bounds.minX,
    y: bounds.minY,
    w: bounds.maxX - bounds.minX,
    h: bounds.maxY - bounds.minY,
  };
}

function zoomed(view: View, factor: number, maxWidth: number): View {
  const w = Math.min(Math.max(view.w * factor, 4), maxWidth);
  const h = (view.h / view.w) * w;
  return { x: view.x + (view.w - w) / 2, y: view.y + (view.h - h) / 2, w, h };
}

/** Top-down plan in metres. Bins stacked in one rack bay share a footprint: one cell per footprint. */
export function Plan2D({
  layout,
  states,
  highlights,
  zoneHighlights,
  selectedBinId,
  focus,
  onSelect,
  ref,
}: Props) {
  const site = siteBounds(layout);
  const markedZones = new Set(zoneHighlights.map((h) => h.target_id));
  // The fitted view frames the highlights (or the site). Pan and zoom by the user apply on top of
  // it until the framing changes, for example when a new answer arrives.
  // Labels sit beside the markers, so framed highlights get a wider margin.
  const fitted = viewOf(padded(focus ?? site, focus ? 9 : 2));
  const fittedKey = `${String(fitted.x)}:${String(fitted.y)}:${String(fitted.w)}:${String(fitted.h)}`;
  const [user, setUser] = useState<{ basis: string; view: View } | null>(null);
  const view = user?.basis === fittedKey ? user.view : fitted;
  const drag = useRef<{ x: number; y: number; view: View } | null>(null);
  const svg = useRef<SVGSVGElement>(null);
  const maxWidth = site.maxX * 3;

  useImperativeHandle(
    ref,
    () => ({
      zoom: (factor) => {
        setUser({ basis: fittedKey, view: zoomed(view, factor, maxWidth) });
      },
      recenter: () => {
        setUser(null);
      },
    }),
    [fittedKey, view, maxWidth],
  );

  const cells = useMemo(() => {
    const byFootprint = new Map<string, LayoutBin[]>();
    for (const bin of layout.bins) {
      const key = `${String(bin.x)}:${String(bin.y)}`;
      byFootprint.set(key, [...(byFootprint.get(key) ?? []), bin]);
    }
    return [...byFootprint.values()].map((stack) =>
      stack.sort((a, b) => (a.level ?? 0) - (b.level ?? 0)),
    );
  }, [layout]);

  const highlightOf = useMemo(() => {
    const found = new Map<string, Highlight>();
    for (const h of highlights) if (h.target_type === "bin") found.set(h.target_id, h);
    return found;
  }, [highlights]);

  const onWheel = (event: WheelEvent) => {
    setUser({ basis: fittedKey, view: zoomed(view, event.deltaY < 0 ? 0.9 : 1.1, maxWidth) });
  };
  const onPointerDown = (event: PointerEvent) => {
    drag.current = { x: event.clientX, y: event.clientY, view };
  };
  const onPointerMove = (event: PointerEvent) => {
    const start = drag.current;
    const box = svg.current?.getBoundingClientRect();
    if (!start || !box) return;
    const scale = start.view.w / box.width;
    setUser({
      basis: fittedKey,
      view: {
        ...start.view,
        x: start.view.x - (event.clientX - start.x) * scale,
        y: start.view.y - (event.clientY - start.y) * scale,
      },
    });
  };
  const onPointerUp = () => {
    drag.current = null;
  };

  return (
    <svg
      ref={svg}
      className={highlights.length > 0 ? "pw-plan has-highlights" : "pw-plan"}
      viewBox={`${String(view.x)} ${String(view.y)} ${String(view.w)} ${String(view.h)}`}
      preserveAspectRatio="xMidYMid meet"
      aria-hidden="true"
      onWheel={onWheel}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerLeave={onPointerUp}
    >
      <defs>
        <pattern id="pw-empty-pattern" width="0.6" height="0.6" patternUnits="userSpaceOnUse">
          <path d="M0 0.6 L0.6 0" className="pw-plan-hatch" />
        </pattern>
        {/* Base states (UX spec 5): low stock hatched, over capacity cross hatched. */}
        <pattern id="pw-low-pattern" width="0.6" height="0.6" patternUnits="userSpaceOnUse">
          <path d="M0 0.6 L0.6 0" className="pw-plan-hatch-low" />
        </pattern>
        <pattern id="pw-over-pattern" width="0.6" height="0.6" patternUnits="userSpaceOnUse">
          <path d="M0 0.6 L0.6 0 M0 0 L0.6 0.6" className="pw-plan-hatch-over" />
        </pattern>
      </defs>
      <rect x={0} y={0} width={site.maxX} height={site.maxY} className="pw-plan-floor" />
      {layout.zones.map((zone) => (
        <g key={zone.id}>
          <polygon
            points={zone.outline.map(([x, y]) => `${String(x)},${String(y)}`).join(" ")}
            className="pw-plan-zone"
          />
          {/* Map labels are sized in metres (scene geometry), so they scale with the plan. A
              highlighted zone's mark carries the label instead, so the two never overlap. */}
          {!markedZones.has(zone.id) && (
            <text
              x={zone.outline[0]?.[0] ?? 0}
              y={(zone.outline[0]?.[1] ?? 0) - 0.4}
              className="pw-plan-label"
              fontSize={PLAN_LABEL_METRES}
            >
              {zone.name}
            </text>
          )}
        </g>
      ))}
      {cells.map((stack) => {
        const base = stack[0];
        if (!base) return null;
        const empty = stack.every((b) => states.get(b.id) === "EMPTY");
        const over = stack.some((b) => states.get(b.id) === "OVER_CAPACITY");
        const low = !over && stack.some((b) => states.get(b.id) === "LOW_STOCK");
        const highlight = stack
          .map((b) => highlightOf.get(b.id))
          .filter((h): h is Highlight => h !== undefined)
          .sort((a, b) => SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity])[0];
        const selected = stack.some((b) => b.id === selectedBinId);
        const classes = [
          "pw-plan-bin",
          empty && !low ? "is-empty" : "",
          low ? "is-low-stock" : "",
          over ? "is-over-capacity" : "",
          highlight ? `is-${highlight.severity}` : "",
          selected ? "is-selected" : "",
        ];
        return (
          <rect
            key={base.id}
            x={base.x - base.width_m / 2}
            y={base.y - base.depth_m / 2}
            width={base.width_m * 0.92}
            height={base.depth_m * 0.92}
            className={classes.filter(Boolean).join(" ")}
            onClick={() => {
              onSelect((stack.find((b) => highlightOf.has(b.id)) ?? base).id);
            }}
          />
        );
      })}
      {/* Zone highlights: the zone outline in the highlight's severity, with its code. */}
      {zoneHighlights.map((highlight) => {
        const zone = layout.zones.find((z) => z.id === highlight.target_id);
        const first = zone?.outline[0];
        if (!zone || !first) return null;
        return (
          <g
            key={highlight.target_id}
            className={`pw-plan-zone-mark is-${highlight.severity}`}
            pointerEvents="none"
          >
            <polygon points={zone.outline.map(([x, y]) => `${String(x)},${String(y)}`).join(" ")} />
            <text x={first[0]} y={first[1] - 0.4} fontSize={PLAN_LABEL_METRES}>
              {zone.name}
            </text>
          </g>
        );
      })}
      {/* A ring and the code at each highlighted location: visible at any zoom level. */}
      {highlights.map((highlight) => {
        const bin =
          highlight.target_type === "bin"
            ? layout.bins.find((b) => b.id === highlight.target_id)
            : undefined;
        if (!bin) return null;
        return (
          <g
            key={highlight.target_id}
            className={`pw-plan-marker is-${highlight.severity}`}
            pointerEvents="none"
          >
            <circle cx={bin.x} cy={bin.y} r={MARKER_METRES} />
            <text
              x={bin.x + MARKER_METRES * 1.3}
              y={bin.y + MARKER_METRES / 2}
              fontSize={PLAN_LABEL_METRES}
            >
              {bin.code}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
