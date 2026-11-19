/**
 * The warehouse as a 3D model, generated from the layout endpoint only (FR-MAP-01): concrete floor
 * with painted zone lines, low walls, docks and the office from site features, pallet racking from
 * racks, a pallet with cartons for every location that holds stock. The browser computes geometry
 * and framing only; stock values come from the map state and answers (UX spec 1).
 *
 * One scene, two views (FR-MAP-02): 3D is an orbiting camera; 2D is the same scene seen from
 * straight above with panning only.
 */
import {
  CameraControls,
  CameraControlsImpl,
  ContactShadows,
  Environment,
  Html,
  Lightformer,
  Line,
} from "@react-three/drei";
import { Canvas, type ThreeEvent } from "@react-three/fiber";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  CanvasTexture,
  Color,
  type InstancedMesh,
  Object3D,
  type PerspectiveCamera,
  RepeatWrapping,
} from "three";

import type { Highlight, Layout, LayoutBin } from "../../shared/api/client";
import { type BinState, type Bounds, prefersReducedMotion, siteBounds } from "./geometry";
import type { CameraCommand } from "./MapPanel";
import type { SceneColors } from "./useTokenColors";

const SEVERITY_TOKEN = {
  info: "--pw-color-status-info",
  warning: "--pw-color-status-warning",
  critical: "--pw-color-status-critical",
} as const;

const WALL_HEIGHT = 1.6;
const WALL_THICKNESS = 0.25;
const PAINT_WIDTH = 0.12;
const PIN_HEIGHT = 2.6;
const UPRIGHT = 0.12;
const BEAM_HEIGHT = 0.14;
const DECK_THICKNESS = 0.03;
const ISO_POLAR = 0.78; // about 45 degrees from straight down, as an isometric model
const ISO_AZIMUTH = -0.55; // from the front left corner

interface Props {
  layout: Layout;
  mode: "3d" | "2d";
  states: Map<string, BinState>;
  onHand: Map<string, number>;
  highlights: Highlight[];
  zoneHighlights: Highlight[];
  bins: Map<string, LayoutBin>;
  selectedBinId: string | null;
  focus: Bounds | null;
  command: CameraCommand;
  colors: SceneColors;
  onSelect: (binId: string) => void;
}

export function Scene3D(props: Props) {
  const site = siteBounds(props.layout);
  return (
    <Canvas
      camera={{
        position: [site.maxX * 0.2, site.maxX * 0.9, site.maxY * 1.9],
        fov: 32,
        near: 0.5,
        far: 1500,
      }}
      dpr={[1, 2]}
      // No tone mapping: materials show the token colors as the theme defines them.
      flat
      aria-hidden="true"
    >
      <ambientLight intensity={1.15} />
      <directionalLight position={[site.maxX * 0.2, 60, site.maxY * 1.2]} intensity={1.6} />
      <Environment resolution={128}>
        <Lightformer
          intensity={1.4}
          position={[0, 30, 0]}
          rotation-x={Math.PI / 2}
          scale={[80, 80, 1]}
        />
        <Lightformer
          intensity={0.6}
          position={[-40, 10, 20]}
          rotation-y={Math.PI / 2}
          scale={[40, 10, 1]}
        />
      </Environment>
      <Floor layout={props.layout} colors={props.colors} site={site} />
      <Racking layout={props.layout} colors={props.colors} />
      <StatePlates {...props} />
      <Stock {...props} />
      <ZoneOutlines {...props} />
      <HitTargets {...props} />
      <Pins {...props} />
      <ContactShadows
        position={[site.maxX / 2, 0.01, site.maxY / 2]}
        scale={[site.maxX + 4, site.maxY + 4]}
        opacity={0.32}
        blur={2.4}
        far={8}
        resolution={1024}
      />
      <CameraRig mode={props.mode} focus={props.focus} command={props.command} site={site} />
    </Canvas>
  );
}

// ---------------------------------------------------------------- floor, walls, docks, office

function strip(from: [number, number], to: [number, number], width: number) {
  const dx = to[0] - from[0];
  const dz = to[1] - from[1];
  const length = Math.hypot(dx, dz);
  return {
    position: [(from[0] + to[0]) / 2, 0, (from[1] + to[1]) / 2] as [number, number, number],
    rotation: -Math.atan2(dz, dx),
    size: [length + width, width] as [number, number],
  };
}

function Floor({ layout, colors, site }: { layout: Layout; colors: SceneColors; site: Bounds }) {
  const cx = site.maxX / 2;
  const cz = site.maxY / 2;
  const walls: [number, number, number, number][] = [
    // x, z, width, depth of each wall segment around the site edge
    [cx, -WALL_THICKNESS / 2, site.maxX + WALL_THICKNESS * 2, WALL_THICKNESS],
    [cx, site.maxY + WALL_THICKNESS / 2, site.maxX + WALL_THICKNESS * 2, WALL_THICKNESS],
    [-WALL_THICKNESS / 2, cz, WALL_THICKNESS, site.maxY],
    [site.maxX + WALL_THICKNESS / 2, cz, WALL_THICKNESS, site.maxY],
  ];
  return (
    <group>
      {/* Concrete slab with a visible edge. */}
      <mesh position={[cx, -0.3, cz]}>
        <boxGeometry args={[site.maxX + 1.5, 0.6, site.maxY + 1.5]} />
        <meshStandardMaterial color={colors["--pw-color-map-wall"]} roughness={0.95} />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[cx, 0.001, cz]}>
        <planeGeometry args={[site.maxX, site.maxY]} />
        <meshStandardMaterial color={colors["--pw-color-map-floor"]} roughness={0.9} />
      </mesh>
      {walls.map(([x, z, w, d], i) => (
        <mesh key={i} position={[x, WALL_HEIGHT / 2, z]}>
          <boxGeometry args={[w, WALL_HEIGHT, d]} />
          <meshStandardMaterial color={colors["--pw-color-map-wall"]} roughness={0.8} />
        </mesh>
      ))}
      {/* Painted zone lines and zone name tags. */}
      {layout.zones.map((zone) => {
        const corners = zone.outline;
        const first = corners[0];
        if (!first) return null;
        return (
          <group key={zone.id}>
            {corners.map((corner, i) => {
              const next = corners[(i + 1) % corners.length] ?? first;
              const s = strip(corner, next, PAINT_WIDTH);
              return (
                <mesh
                  key={i}
                  position={[s.position[0], 0.012, s.position[2]]}
                  rotation={[-Math.PI / 2, 0, s.rotation]}
                >
                  <planeGeometry args={s.size} />
                  <meshBasicMaterial color={colors["--pw-color-map-aisle-paint"]} />
                </mesh>
              );
            })}
            <Html
              position={[first[0] + 0.6, 0.05, first[1] + 0.6]}
              zIndexRange={[10, 0]}
              pointerEvents="none"
            >
              <span className="pw-zone-tag">{zone.name}</span>
            </Html>
          </group>
        );
      })}
      {layout.features.map((feature) => {
        const xs = feature.outline.map((p) => p[0]);
        const zs = feature.outline.map((p) => p[1]);
        const minX = Math.min(...xs);
        const maxX = Math.max(...xs);
        const minZ = Math.min(...zs);
        const maxZ = Math.max(...zs);
        const w = maxX - minX;
        const d = maxZ - minZ;
        const centre: [number, number] = [(minX + maxX) / 2, (minZ + maxZ) / 2];
        if (feature.feature_type === "OFFICE") {
          return (
            <mesh key={feature.id} position={[centre[0], 1.4, centre[1]]}>
              <boxGeometry args={[w, 2.8, d]} />
              <meshStandardMaterial color={colors["--pw-color-map-wall"]} roughness={0.6} />
            </mesh>
          );
        }
        if (feature.feature_type === "AISLE") {
          return (
            <mesh
              key={feature.id}
              rotation={[-Math.PI / 2, 0, 0]}
              position={[centre[0], 0.008, centre[1]]}
            >
              <planeGeometry args={[w, d]} />
              <meshBasicMaterial
                color={colors["--pw-color-map-aisle-paint"]}
                transparent
                opacity={0.18}
              />
            </mesh>
          );
        }
        if (feature.feature_type === "RECEIVING_DOCK" || feature.feature_type === "SHIPPING_DOCK") {
          // Dock doors: dark panels standing in the wall.
          return (
            <mesh key={feature.id} position={[centre[0], 1.5, centre[1]]}>
              <boxGeometry args={[Math.max(w, 0.4), 3, Math.max(d, 0.4)]} />
              <meshStandardMaterial
                color={colors["--pw-color-map-dock"]}
                roughness={0.5}
                metalness={0.2}
              />
            </mesh>
          );
        }
        return null;
      })}
    </group>
  );
}

// ---------------------------------------------------------------- racking

function useInstances(
  count: number,
  place: (dummy: Object3D, index: number) => void,
  deps: unknown[],
) {
  const mesh = useRef<InstancedMesh>(null);
  useLayoutEffect(() => {
    const target = mesh.current;
    if (!target) return;
    const dummy = new Object3D();
    for (let i = 0; i < count; i += 1) {
      place(dummy, i);
      dummy.updateMatrix();
      target.setMatrixAt(i, dummy.matrix);
    }
    target.instanceMatrix.needsUpdate = true;
    target.computeBoundingSphere();
    // place is recreated on every render; the listed inputs decide when the matrices change
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return mesh;
}

/** Pallet racking: blue uprights at every bay boundary, orange beams at every level. */
function Racking({ layout, colors }: { layout: Layout; colors: SceneColors }) {
  const parts = useMemo(() => {
    const uprights: [number, number, number, number][] = [];
    const beams: [number, number, number, number][] = [];
    // Wire decking on every shelf level, so racking reads as solid shelves, not a frame.
    const decks: [number, number, number, number, number][] = [];
    for (const rack of layout.racks) {
      const height = rack.level_count * rack.level_height_m;
      for (let level = 1; level < rack.level_count; level += 1) {
        decks.push([
          rack.origin_x + (rack.bay_count * rack.bay_width_m) / 2,
          level * rack.level_height_m,
          rack.origin_y + rack.depth_m / 2,
          rack.bay_count * rack.bay_width_m,
          rack.depth_m,
        ]);
      }
      for (const side of [0, rack.depth_m]) {
        for (let bay = 0; bay <= rack.bay_count; bay += 1) {
          uprights.push([
            rack.origin_x + bay * rack.bay_width_m,
            height / 2,
            rack.origin_y + side,
            height,
          ]);
        }
        for (let level = 1; level <= rack.level_count; level += 1) {
          for (let bay = 0; bay < rack.bay_count; bay += 1) {
            beams.push([
              rack.origin_x + (bay + 0.5) * rack.bay_width_m,
              level * rack.level_height_m - BEAM_HEIGHT / 2,
              rack.origin_y + side,
              rack.bay_width_m,
            ]);
          }
        }
      }
    }
    return { uprights, beams, decks };
  }, [layout]);

  const uprights = useInstances(
    parts.uprights.length,
    (dummy, i) => {
      const [x, y, z, h] = parts.uprights[i] ?? [0, 0, 0, 0];
      dummy.position.set(x, y, z);
      dummy.scale.set(UPRIGHT, h, UPRIGHT);
    },
    [parts],
  );
  const beams = useInstances(
    parts.beams.length,
    (dummy, i) => {
      const [x, y, z, w] = parts.beams[i] ?? [0, 0, 0, 0];
      dummy.position.set(x, y, z);
      dummy.scale.set(w, BEAM_HEIGHT, 0.06);
    },
    [parts],
  );

  const decks = useInstances(
    parts.decks.length,
    (dummy, i) => {
      const [x, y, z, w, d] = parts.decks[i] ?? [0, 0, 0, 0, 0];
      dummy.position.set(x, y, z);
      dummy.scale.set(w, DECK_THICKNESS, d);
    },
    [parts],
  );

  return (
    <>
      <instancedMesh
        ref={decks}
        args={[undefined, undefined, parts.decks.length]}
        raycast={() => null}
      >
        <boxGeometry args={[1, 1, 1]} />
        <meshStandardMaterial
          color={colors["--pw-color-map-rack"]}
          roughness={0.7}
          metalness={0.4}
          transparent
          opacity={0.85}
        />
      </instancedMesh>
      <instancedMesh
        ref={uprights}
        args={[undefined, undefined, parts.uprights.length]}
        raycast={() => null}
      >
        <boxGeometry args={[1, 1, 1]} />
        <meshStandardMaterial
          color={colors["--pw-color-map-upright"]}
          roughness={0.45}
          metalness={0.35}
        />
      </instancedMesh>
      <instancedMesh
        ref={beams}
        args={[undefined, undefined, parts.beams.length]}
        raycast={() => null}
      >
        <boxGeometry args={[1, 1, 1]} />
        <meshStandardMaterial
          color={colors["--pw-color-map-beam"]}
          roughness={0.45}
          metalness={0.3}
        />
      </instancedMesh>
    </>
  );
}

// ---------------------------------------------------------------- stock: pallets and cartons

function slotBase(bin: LayoutBin): number {
  return bin.z - bin.height_m / 2 + (bin.rack_id ? 0.02 : 0);
}

/**
 * Every location holding stock shows a wooden pallet with cartons (blue totes on pick faces).
 * Empty locations show nothing on the shelf: the racking stays visible. While an answer is
 * shown, highlighted stock takes its severity color and the rest fades towards the floor.
 */
function Stock({ layout, onHand, highlights, colors }: Props) {
  // Drawn from what the location holds: a low stock pick location can be empty.
  const stocked = useMemo(
    () => layout.bins.filter((b) => (onHand.get(b.id) ?? 0) > 0),
    [layout, onHand],
  );
  const highlighted = useMemo(
    () => new Map(highlights.map((h) => [h.target_id, h.severity])),
    [highlights],
  );

  const pallets = useInstances(
    stocked.length,
    (dummy, i) => {
      const bin = stocked[i];
      if (!bin) return;
      dummy.position.set(bin.x, slotBase(bin) + 0.07, bin.y);
      dummy.scale.set(bin.width_m * 0.86, 0.14, bin.depth_m * 0.84);
    },
    [stocked],
  );
  const cartons = useRef<InstancedMesh>(null);

  useLayoutEffect(() => {
    const target = cartons.current;
    if (!target) return;
    const dummy = new Object3D();
    const kraft = new Color(colors["--pw-color-map-carton"]);
    const tote = new Color(colors["--pw-color-map-carton-alt"]);
    const floor = new Color(colors["--pw-color-map-floor"]);
    const focusing = highlighted.size > 0;
    stocked.forEach((bin, i) => {
      const height = Math.min(bin.height_m * 0.62, 1.4);
      dummy.position.set(bin.x, slotBase(bin) + 0.14 + height / 2, bin.y);
      dummy.scale.set(bin.width_m * 0.8, height, bin.depth_m * 0.78);
      dummy.updateMatrix();
      target.setMatrixAt(i, dummy.matrix);
      const severity = highlighted.get(bin.id);
      const base = bin.bin_role === "PICK" ? tote : kraft;
      const color = severity
        ? new Color(colors[SEVERITY_TOKEN[severity]])
        : focusing
          ? base.clone().lerp(floor, 0.3)
          : base;
      target.setColorAt(i, color);
    });
    target.instanceMatrix.needsUpdate = true;
    if (target.instanceColor) target.instanceColor.needsUpdate = true;
    target.computeBoundingSphere();
  }, [stocked, highlighted, colors]);

  return (
    <>
      <instancedMesh
        ref={pallets}
        args={[undefined, undefined, stocked.length]}
        raycast={() => null}
      >
        <boxGeometry args={[1, 1, 1]} />
        <meshStandardMaterial color={colors["--pw-color-map-pallet"]} roughness={0.9} />
      </instancedMesh>
      <instancedMesh
        ref={cartons}
        args={[undefined, undefined, stocked.length]}
        raycast={() => null}
      >
        <boxGeometry args={[1, 1, 1]} />
        <meshStandardMaterial roughness={0.85} />
      </instancedMesh>
    </>
  );
}

/** The base state as people read it, with the units held (UX spec 5). */
function stateText(state: BinState | undefined, onHand: number): string {
  const held = `${String(onHand)} on hand`;
  if (state === "OVER_CAPACITY") return `Over capacity, ${held}`;
  if (state === "LOW_STOCK") return onHand > 0 ? `Low stock, ${held}` : "Low stock, empty";
  return state === "EMPTY" ? "Empty" : held;
}

/** A repeating hatch in one status color: diagonal for low stock, crossed for over capacity. */
function hatchTexture(color: string, crossed: boolean): CanvasTexture {
  const size = 64;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  if (ctx) {
    ctx.globalAlpha = 0.28;
    ctx.fillStyle = color;
    ctx.fillRect(0, 0, size, size);
    ctx.globalAlpha = 0.9;
    ctx.strokeStyle = color;
    ctx.lineWidth = 7;
    const stripe = (from: [number, number], to: [number, number]) => {
      ctx.beginPath();
      ctx.moveTo(...from);
      ctx.lineTo(...to);
      ctx.stroke();
    };
    for (const offset of [-size, 0, size]) {
      stripe([offset, size], [offset + size, 0]);
      if (crossed) stripe([offset, 0], [offset + size, size]);
    }
  }
  const texture = new CanvasTexture(canvas);
  texture.wrapS = RepeatWrapping;
  texture.wrapT = RepeatWrapping;
  texture.repeat.set(2, 2);
  return texture;
}

/**
 * The base state layer (UX spec 5): low stock and over capacity locations get a hatched plate on
 * their shelf, under the pallet, so the state stays readable around the stock and under any answer
 * highlight. Color is never the only signal: the hatch differs.
 */
function StatePlates({ layout, states, colors }: Props) {
  const kinds = [
    { state: "LOW_STOCK", token: "--pw-color-status-warning", crossed: false },
    { state: "OVER_CAPACITY", token: "--pw-color-status-critical", crossed: true },
  ] as const;
  return (
    <>
      {kinds.map((kind) => (
        <StatePlate
          key={kind.state}
          bins={layout.bins.filter((b) => states.get(b.id) === kind.state)}
          color={colors[kind.token]}
          crossed={kind.crossed}
        />
      ))}
    </>
  );
}

function StatePlate({
  bins,
  color,
  crossed,
}: {
  bins: LayoutBin[];
  color: string;
  crossed: boolean;
}) {
  const texture = useMemo(() => hatchTexture(color, crossed), [color, crossed]);
  useEffect(
    () => () => {
      texture.dispose();
    },
    [texture],
  );
  const mesh = useInstances(
    bins.length,
    (dummy, i) => {
      const bin = bins[i];
      if (!bin) return;
      dummy.position.set(bin.x, slotBase(bin) + 0.012, bin.y);
      dummy.rotation.set(-Math.PI / 2, 0, 0);
      dummy.scale.set(bin.width_m * 0.98, bin.depth_m * 0.98, 1);
    },
    [bins],
  );
  if (bins.length === 0) return null;
  return (
    <instancedMesh ref={mesh} args={[undefined, undefined, bins.length]} raycast={() => null}>
      <planeGeometry args={[1, 1]} />
      <meshBasicMaterial map={texture} transparent depthWrite={false} />
    </instancedMesh>
  );
}

/** Zone highlights (semantic spec 7.6): an outline painted round the zone, with its code. */
function ZoneOutlines({ layout, zoneHighlights, colors }: Props) {
  const zones = new Map(layout.zones.map((z) => [z.id, z]));
  return (
    <>
      {zoneHighlights.map((highlight) => {
        const zone = zones.get(highlight.target_id);
        const first = zone?.outline[0];
        if (!zone || !first || zone.outline.length < 3) return null;
        const points = [...zone.outline, first].map(
          ([x, y]) => [x, 0.06, y] as [number, number, number],
        );
        const cx = zone.outline.reduce((sum, [x]) => sum + x, 0) / zone.outline.length;
        const cy = zone.outline.reduce((sum, [, y]) => sum + y, 0) / zone.outline.length;
        return (
          <group key={highlight.target_id}>
            <Line
              points={points}
              color={colors[SEVERITY_TOKEN[highlight.severity]]}
              lineWidth={highlight.severity === "critical" ? 6 : 4}
            />
            <Html position={[cx, 0.4, cy]} center zIndexRange={[20, 10]} pointerEvents="none">
              <span className={`pw-pin-tag is-${highlight.severity}`}>Zone {zone.code}</span>
            </Html>
          </group>
        );
      })}
    </>
  );
}

/** Invisible boxes, one per location, for hover and click, empty locations included. */
function HitTargets({ layout, onHand, states, onSelect }: Props) {
  const bins = layout.bins;
  const [hovered, setHovered] = useState<number | null>(null);
  const mesh = useInstances(
    bins.length,
    (dummy, i) => {
      const bin = bins[i];
      if (!bin) return;
      dummy.position.set(bin.x, bin.z, bin.y);
      dummy.scale.set(bin.width_m * 0.96, bin.height_m * 0.96, bin.depth_m * 0.96);
    },
    [bins],
  );
  const bin = hovered === null ? undefined : bins[hovered];
  const onPointer = (event: ThreeEvent<PointerEvent>) => {
    event.stopPropagation();
    setHovered(event.instanceId ?? null);
    document.body.style.cursor = "pointer";
  };

  return (
    <>
      <instancedMesh
        ref={mesh}
        args={[undefined, undefined, bins.length]}
        onPointerMove={onPointer}
        onPointerOut={() => {
          setHovered(null);
          document.body.style.cursor = "";
        }}
        onClick={(event) => {
          event.stopPropagation();
          const target = event.instanceId === undefined ? undefined : bins[event.instanceId];
          if (target) onSelect(target.id);
        }}
      >
        <boxGeometry args={[1, 1, 1]} />
        <meshBasicMaterial transparent opacity={0} depthWrite={false} />
      </instancedMesh>
      {bin && (
        <Html
          position={[bin.x, bin.z + bin.height_m / 2 + 0.4, bin.y]}
          center
          pointerEvents="none"
          zIndexRange={[30, 20]}
        >
          <span className="pw-tooltip">
            <strong>{bin.code}</strong>
            {stateText(states.get(bin.id), onHand.get(bin.id) ?? 0)}
          </span>
        </Html>
      )}
    </>
  );
}

/** A pin with the location code above each highlighted location, visible from any angle. */
function Pins({ highlights, bins, selectedBinId, colors }: Props) {
  const selected = selectedBinId ? bins.get(selectedBinId) : undefined;
  return (
    <>
      {highlights.map((highlight) => {
        const bin = highlight.target_type === "bin" ? bins.get(highlight.target_id) : undefined;
        if (!bin) return null;
        const top = bin.z + bin.height_m / 2;
        const color = colors[SEVERITY_TOKEN[highlight.severity]];
        return (
          <group key={highlight.target_id}>
            <mesh position={[bin.x, top + PIN_HEIGHT / 2, bin.y]} raycast={() => null}>
              <cylinderGeometry args={[0.045, 0.045, PIN_HEIGHT, 8]} />
              <meshBasicMaterial color={color} />
            </mesh>
            <mesh position={[bin.x, top + PIN_HEIGHT, bin.y]} raycast={() => null}>
              <sphereGeometry args={[highlight.severity === "critical" ? 0.42 : 0.32, 24, 24]} />
              <meshBasicMaterial color={color} />
            </mesh>
            <Html
              position={[bin.x, top + PIN_HEIGHT + 0.8, bin.y]}
              center
              zIndexRange={[20, 10]}
              pointerEvents="none"
            >
              <span className={`pw-pin-tag is-${highlight.severity}`}>{bin.code}</span>
            </Html>
          </group>
        );
      })}
      {selected && (
        <mesh position={[selected.x, selected.z, selected.y]} raycast={() => null}>
          <boxGeometry
            args={[selected.width_m * 1.08, selected.height_m * 1.04, selected.depth_m * 1.08]}
          />
          <meshBasicMaterial color={colors["--pw-color-focus-ring"]} wireframe />
        </mesh>
      )}
    </>
  );
}

// ---------------------------------------------------------------- camera

/**
 * Game-like camera: eased movement, zoom towards the pointer, orbit and tilt in 3D, pan only in
 * 2D. It flies to the highlighted locations when an answer arrives (FR-MAP-05).
 */
function CameraRig({
  mode,
  focus,
  command,
  site,
}: {
  mode: "3d" | "2d";
  focus: Bounds | null;
  command: CameraCommand;
  site: Bounds;
}) {
  const controls = useRef<CameraControls>(null);
  const smooth = !prefersReducedMotion();

  // camera-controls' fitToBox snaps to the nearest axis (a flat overhead view), so framing is our
  // own: it keeps the isometric tilt and the user's current rotation, and fits width and depth.
  const frame = (bounds: Bounds, polar = mode === "2d" ? 0.0001 : ISO_POLAR) => {
    const rig = controls.current;
    if (!rig) return;
    const margin = bounds === site ? 1 : 5;
    const width = Math.max(bounds.maxX - bounds.minX + margin * 2, 14);
    const depth = Math.max(bounds.maxY - bounds.minY + margin * 2, 14);
    const camera = rig.camera as PerspectiveCamera;
    const vertical = (camera.fov / 2) * (Math.PI / 180);
    const horizontal = Math.atan(Math.tan(vertical) * camera.aspect);
    // Seen at a tilt, the floor's depth is foreshortened by cos(polar).
    const distance =
      Math.max(
        (depth * Math.cos(polar)) / 2 / Math.tan(vertical),
        width / 2 / Math.tan(horizontal),
      ) * 1.12;
    const azimuth = mode === "2d" ? 0 : rig.azimuthAngle;
    const cx = (bounds.minX + bounds.maxX) / 2;
    const cz = (bounds.minY + bounds.maxY) / 2;
    const px = cx + distance * Math.sin(polar) * Math.sin(azimuth);
    const py = distance * Math.cos(polar);
    const pz = cz + distance * Math.sin(polar) * Math.cos(azimuth);
    void rig.setLookAt(px, py, pz, cx, 0, cz, smooth);
  };

  // View mode: 3D orbits from the front corner; 2D looks straight down and only pans.
  useEffect(() => {
    const rig = controls.current;
    if (!rig) return;
    const { ACTION } = CameraControlsImpl;
    if (mode === "2d") {
      rig.minPolarAngle = 0;
      rig.maxPolarAngle = 0;
      rig.mouseButtons.left = ACTION.TRUCK;
      rig.touches.one = ACTION.TOUCH_TRUCK;
    } else {
      rig.minPolarAngle = 0.12;
      rig.maxPolarAngle = 1.3;
      rig.mouseButtons.left = ACTION.ROTATE;
      rig.touches.one = ACTION.TOUCH_ROTATE;
      if (rig.azimuthAngle === 0) rig.azimuthAngle = ISO_AZIMUTH;
    }
    frame(focus ?? site);
    // frame reads the latest focus and site; mode decides when this runs
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode]);

  useEffect(() => {
    frame(focus ?? site);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus]);

  useEffect(() => {
    const rig = controls.current;
    if (!rig || command.n === 0) return;
    const step = Math.PI / 6;
    switch (command.kind) {
      case "recenter":
        frame(focus ?? site);
        break;
      case "zoom-in":
        void rig.dolly(rig.distance * 0.25, smooth);
        break;
      case "zoom-out":
        void rig.dolly(-rig.distance * 0.33, smooth);
        break;
      case "rotate-left":
        void rig.rotate(step, 0, smooth);
        break;
      case "rotate-right":
        void rig.rotate(-step, 0, smooth);
        break;
      case "tilt-up":
        void rig.rotate(0, -0.18, smooth);
        break;
      case "tilt-down":
        void rig.rotate(0, 0.18, smooth);
        break;
      case "pan-left":
        void rig.truck(-rig.distance * 0.08, 0, smooth);
        break;
      case "pan-right":
        void rig.truck(rig.distance * 0.08, 0, smooth);
        break;
      case "pan-up":
        void rig.forward(rig.distance * 0.08, smooth);
        break;
      case "pan-down":
        void rig.forward(-rig.distance * 0.08, smooth);
        break;
    }
    // Each command is applied once, when its counter changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [command.n]);

  return (
    <CameraControls
      ref={controls}
      makeDefault
      smoothTime={smooth ? 0.35 : 0}
      dollyToCursor
      minDistance={6}
      maxDistance={260}
      minPolarAngle={0.12}
      maxPolarAngle={1.3}
    />
  );
}
