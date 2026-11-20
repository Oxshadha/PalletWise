import "./map.css";

import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { LayoutBin } from "../../shared/api/client";
import { Icon } from "../../shared/ui/Icon";
import { useWorkspace } from "../../shared/workspace/WorkspaceContext";
import { activeHighlights } from "../../shared/workspace/workspace";
import {
  binsById,
  highlightBounds,
  onHandById,
  splitHighlights,
  statesById,
  webglAvailable,
} from "./geometry";
import { Legend } from "./Legend";
import { LocationPopover } from "./LocationPopover";
import { LocationsList } from "./LocationsList";
import { Plan2D, type PlanHandle } from "./Plan2D";
import { useSiteMap } from "./useSiteMap";
import { useTokenColors } from "./useTokenColors";

// The 3D scene is loaded on demand so the 2D plan and the copilot never wait for WebGL code.
const Scene3D = lazy(() => import("./Scene3D").then((m) => ({ default: m.Scene3D })));

export interface CameraCommand {
  kind:
    | "recenter"
    | "zoom-in"
    | "zoom-out"
    | "rotate-left"
    | "rotate-right"
    | "tilt-up"
    | "tilt-down"
    | "pan-left"
    | "pan-right"
    | "pan-up"
    | "pan-down";
  n: number;
}

/** Game-style keys while focus is in the map (UX spec 3 extended). */
const KEYS: Record<string, CameraCommand["kind"]> = {
  "+": "zoom-in",
  "=": "zoom-in",
  "-": "zoom-out",
  "0": "recenter",
  q: "rotate-left",
  e: "rotate-right",
  PageUp: "tilt-up",
  PageDown: "tilt-down",
  ArrowLeft: "pan-left",
  ArrowRight: "pan-right",
  ArrowUp: "pan-up",
  ArrowDown: "pan-down",
};

/** Warehouse panel (UX spec 3): one layout, two views, the same state in both. */
export function MapPanel() {
  const { state, dispatch, me, siteId } = useWorkspace();
  const { layout, state: mapState, retry } = useSiteMap(siteId);
  const colors = useTokenColors();
  const [webgl] = useState(webglAvailable);
  const [command, setCommand] = useState<CameraCommand>({ kind: "recenter", n: 0 });
  const site = me.sites.find((s) => s.id === siteId);

  const ready = layout.kind === "ready" ? layout.layout : null;
  const bins = useMemo(() => (ready ? binsById(ready) : new Map<string, LayoutBin>()), [ready]);
  const states = useMemo(() => statesById(mapState), [mapState]);
  const onHand = useMemo(() => onHandById(mapState), [mapState]);
  const answer = activeHighlights(state);
  // Racks become their bins; zones stay zones and are drawn as floor outlines (semantic spec 7.6).
  const marks = useMemo(
    () => (ready ? splitHighlights(answer, ready) : { bins: answer, zones: [] }),
    [answer, ready],
  );
  const highlights = marks.bins;
  const zoneHighlights = marks.zones;
  const zoneById = useMemo(() => new Map((ready?.zones ?? []).map((z) => [z.id, z])), [ready]);
  const focus = useMemo(
    () => highlightBounds(highlights, bins, zoneHighlights, zoneById),
    [highlights, bins, zoneHighlights, zoneById],
  );
  const selected = state.selectedBinId ? bins.get(state.selectedBinId) : undefined;
  // With WebGL, 2D is the same scene seen from above; the SVG plan is the fallback without it.
  const view = webgl ? state.view : "2d";

  const plan = useRef<PlanHandle>(null);
  const send = useCallback((kind: CameraCommand["kind"]) => {
    setCommand((c) => ({ kind, n: c.n + 1 }));
    if (kind === "recenter") plan.current?.recenter();
    else if (kind === "zoom-in" || kind === "zoom-out")
      plan.current?.zoom(kind === "zoom-in" ? 0.8 : 1.25);
  }, []);
  const select = (binId: string) => {
    dispatch({ type: "select-bin", binId });
  };
  const stage = useRef<HTMLElement>(null);
  useEffect(() => {
    const element = stage.current;
    if (!element) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLInputElement)
        return;
      const kind = KEYS[event.key.length === 1 ? event.key.toLowerCase() : event.key];
      if (kind) {
        event.preventDefault();
        send(kind);
      }
    };
    element.addEventListener("keydown", onKey);
    return () => {
      element.removeEventListener("keydown", onKey);
    };
  }, [send]);

  const zoneCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const bin of ready?.bins ?? [])
      counts.set(bin.zone_id, (counts.get(bin.zone_id) ?? 0) + 1);
    return counts;
  }, [ready]);
  const highlightedZones = useMemo(() => {
    const counts = new Map<string, number>();
    for (const h of highlights) {
      const bin = bins.get(h.target_id);
      if (bin) counts.set(bin.zone_id, (counts.get(bin.zone_id) ?? 0) + 1);
    }
    return counts;
  }, [highlights, bins]);

  return (
    <section className="pw-panel pw-map-panel" aria-labelledby="pw-warehouse-title">
      <header className="pw-panel-header">
        <div>
          <h2 id="pw-warehouse-title" tabIndex={-1}>
            Warehouse
          </h2>
          <p className="pw-muted">
            {site ? `${site.name} (${site.code})` : ""}
            {ready
              ? ` · ${String(ready.bins.length)} locations in ${String(ready.zones.length)} zones`
              : ""}
          </p>
        </div>
        <div className="pw-segmented" role="group" aria-label="Map view">
          {(["3d", "2d"] as const).map((v) => (
            <button
              key={v}
              type="button"
              aria-pressed={view === v}
              disabled={v === "3d" && !webgl}
              onClick={() => {
                dispatch({ type: "set-view", view: v });
              }}
            >
              {v.toUpperCase()}
            </button>
          ))}
        </div>
      </header>

      {!webgl && (
        <p className="pw-notice">
          3D is not available in this browser, so the map shows the 2D plan.
        </p>
      )}

      <div className="pw-map-body">
        <aside className="pw-map-side">
          {ready && (
            <div className="pw-side-section">
              <p className="pw-eyebrow">Zones</p>
              <ul className="pw-zone-list">
                {ready.zones.map((zone) => {
                  const lit = highlightedZones.get(zone.id) ?? 0;
                  return (
                    <li key={zone.id} className={lit > 0 ? "is-lit" : ""}>
                      <span className="pw-zone-dot" aria-hidden="true" />
                      <span className="pw-zone-name">{zone.name}</span>
                      <span className="pw-muted pw-zone-count">
                        {lit > 0
                          ? `${String(lit)} of ${String(zoneCounts.get(zone.id) ?? 0)}`
                          : String(zoneCounts.get(zone.id) ?? 0)}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
          <div className="pw-side-section">
            <LocationsList
              highlights={highlights}
              zoneHighlights={zoneHighlights}
              zones={zoneById}
              bins={bins}
              selectedBinId={state.selectedBinId}
              onSelect={select}
            />
            {answer.length > 0 && (
              <button
                type="button"
                className="pw-link-button pw-clear"
                onClick={() => {
                  dispatch({ type: "clear-highlights" });
                }}
              >
                Clear highlights
              </button>
            )}
          </div>
          <div className="pw-side-section">
            <Legend />
          </div>
        </aside>

        {/* + and - zoom, 0 recenters while focus is in the map (its controls or the popover; UX spec 3).
            The scene is hidden from assistive technology; the locations list reaches every location. */}
        <section
          ref={stage}
          className="pw-map-stage"
          aria-label="Warehouse map. Arrow keys pan, Q and E rotate, Page Up and Page Down tilt, plus and minus zoom, zero recenters."
        >
          {layout.kind === "loading" && (
            <div className="pw-map-skeleton" aria-label="Loading the warehouse map" />
          )}
          {layout.kind === "empty" && (
            <p className="pw-map-message">No layout is available for this site.</p>
          )}
          {layout.kind === "failed" && (
            <div className="pw-map-message" role="alert">
              <p>{layout.message}</p>
              {layout.traceId && <p className="pw-muted">Reference: {layout.traceId}</p>}
              <button type="button" className="pw-button" onClick={retry}>
                Try again
              </button>
            </div>
          )}
          {ready && webgl && (
            <div className="pw-scene">
              <Suspense
                fallback={
                  <div className="pw-map-skeleton" aria-label="Loading the warehouse model" />
                }
              >
                <Scene3D
                  layout={ready}
                  mode={view}
                  states={states}
                  onHand={onHand}
                  highlights={highlights}
                  zoneHighlights={zoneHighlights}
                  bins={bins}
                  selectedBinId={state.selectedBinId}
                  focus={focus}
                  command={command}
                  colors={colors}
                  onSelect={select}
                />
              </Suspense>
            </div>
          )}
          {ready && !webgl && (
            <Plan2D
              ref={plan}
              layout={ready}
              states={states}
              highlights={highlights}
              zoneHighlights={zoneHighlights}
              selectedBinId={state.selectedBinId}
              focus={focus}
              onSelect={select}
            />
          )}
          <div className="pw-map-controls" role="group" aria-label="Map controls">
            <button
              type="button"
              className="pw-icon-button"
              aria-label="Zoom in"
              onClick={() => {
                send("zoom-in");
              }}
            >
              <Icon name="plus" />
            </button>
            <button
              type="button"
              className="pw-icon-button"
              aria-label="Zoom out"
              onClick={() => {
                send("zoom-out");
              }}
            >
              <Icon name="minus" />
            </button>
            {view === "3d" && webgl && (
              <>
                <button
                  type="button"
                  className="pw-icon-button"
                  aria-label="Rotate left"
                  onClick={() => {
                    send("rotate-left");
                  }}
                >
                  <Icon name="rotate-left" />
                </button>
                <button
                  type="button"
                  className="pw-icon-button"
                  aria-label="Rotate right"
                  onClick={() => {
                    send("rotate-right");
                  }}
                >
                  <Icon name="rotate-right" />
                </button>
                <button
                  type="button"
                  className="pw-icon-button"
                  aria-label="Tilt up"
                  onClick={() => {
                    send("tilt-up");
                  }}
                >
                  <Icon name="tilt-up" />
                </button>
                <button
                  type="button"
                  className="pw-icon-button"
                  aria-label="Tilt down"
                  onClick={() => {
                    send("tilt-down");
                  }}
                >
                  <Icon name="tilt-down" />
                </button>
              </>
            )}
            <button
              type="button"
              className="pw-icon-button"
              aria-label="Recenter the map"
              onClick={() => {
                send("recenter");
              }}
            >
              <Icon name="recenter" />
            </button>
          </div>
          <ul className="pw-map-key" aria-hidden="true">
            <li>
              <span className="pw-dot is-stock" /> Stock
            </li>
            <li>
              <span className="pw-dot is-empty" /> Empty
            </li>
            <li>
              <span className="pw-dot is-low-stock" /> Low stock
            </li>
            <li>
              <span className="pw-dot is-over-capacity" /> Over capacity
            </li>
            <li>
              <span className="pw-dot is-info" /> Highlighted
            </li>
          </ul>
          {selected && (
            <LocationPopover
              key={selected.id}
              siteId={siteId}
              bin={selected}
              onClose={() => {
                dispatch({ type: "select-bin", binId: null });
              }}
              onAsk={(bin) => {
                dispatch({
                  type: "ask",
                  pending: {
                    question: `What is in location ${bin.code}?`,
                    pageContext: { selected_bin: bin.code },
                  },
                });
              }}
            />
          )}
        </section>
      </div>
    </section>
  );
}
