/**
 * Scenery around the data: two trucks backed onto the docks, a marked apron outside, and a few
 * forklifts with their operators. Decoration only: placed from the dock features, never clickable,
 * never highlighted, and none of it is data.
 */
import type { Layout } from "../../shared/api/client";
import type { Bounds } from "./geometry";
import type { SceneColors } from "./useTokenColors";

type Vec = [number, number, number];

/** Scenery colors from the map tokens (tokens.css), light and dark. */
interface Palette {
  truck: string;
  cab: string;
  glass: string;
  tyre: string;
  forklift: string;
  steel: string;
  vest: string;
  skin: string;
  apron: string;
  paint: string;
}

function palette(colors: SceneColors): Palette {
  return {
    truck: colors["--pw-color-map-truck"],
    cab: colors["--pw-color-map-truck-cab"],
    glass: colors["--pw-color-map-glass"],
    tyre: colors["--pw-color-map-tyre"],
    forklift: colors["--pw-color-map-forklift"],
    steel: colors["--pw-color-map-steel"],
    vest: colors["--pw-color-map-vest"],
    skin: colors["--pw-color-map-skin"],
    apron: colors["--pw-color-map-apron"],
    paint: colors["--pw-color-map-yard-paint"],
  };
}

function Box({ at, size, color }: { at: Vec; size: Vec; color: string }) {
  return (
    <mesh position={at} raycast={() => null}>
      <boxGeometry args={size} />
      <meshStandardMaterial color={color} roughness={0.6} />
    </mesh>
  );
}

function Wheel({ at, color, radius = 0.45 }: { at: Vec; color: string; radius?: number }) {
  return (
    <mesh position={at} rotation={[Math.PI / 2, 0, 0]} raycast={() => null}>
      <cylinderGeometry args={[radius, radius, 0.3, 16]} />
      <meshStandardMaterial color={color} roughness={0.9} />
    </mesh>
  );
}

/** A truck along local +x: trailer rear at x = 0 (against the dock), cab at the far end. */
function Truck({ c }: { c: Palette }) {
  return (
    <group>
      <Box at={[4, 2.15, 0]} size={[8, 2.6, 2.5]} color={c.truck} />
      <Box at={[4, 1.2, 1.26]} size={[8, 0.25, 0.02]} color={c.cab} />
      <Box at={[4, 1.2, -1.26]} size={[8, 0.25, 0.02]} color={c.cab} />
      <Box at={[8.3, 0.7, 0]} size={[0.6, 0.3, 1.8]} color={c.steel} />
      <Box at={[9.6, 1.55, 0]} size={[2.2, 2.3, 2.4]} color={c.cab} />
      <Box at={[10.72, 2.05, 0]} size={[0.04, 0.9, 2.1]} color={c.glass} />
      {[1.2, 2.4, 9.8].map((x) =>
        [1.05, -1.05].map((z) => (
          <Wheel color={c.tyre} key={`${String(x)}${String(z)}`} at={[x, 0.45, z]} />
        )),
      )}
    </group>
  );
}

/** A forklift with its operator, forks along local +x. */
function Forklift({ c }: { c: Palette }) {
  return (
    <group>
      <Box at={[0, 0.55, 0]} size={[1.6, 0.7, 1.0]} color={c.forklift} />
      <Box at={[-0.7, 0.75, 0]} size={[0.35, 0.9, 0.95]} color={c.steel} />
      {[
        [0.55, 0.42],
        [0.55, -0.42],
        [-0.55, 0.42],
        [-0.55, -0.42],
      ].map(([x = 0, z = 0]) => (
        <Box
          key={`${String(x)}${String(z)}`}
          at={[x, 1.55, z]}
          size={[0.06, 1.3, 0.06]}
          color={c.steel}
        />
      ))}
      <Box at={[0, 2.22, 0]} size={[1.2, 0.06, 0.95]} color={c.steel} />
      <Box at={[0.88, 1.25, 0.3]} size={[0.1, 2.2, 0.1]} color={c.steel} />
      <Box at={[0.88, 1.25, -0.3]} size={[0.1, 2.2, 0.1]} color={c.steel} />
      <Box at={[1.4, 0.12, 0.25]} size={[1.0, 0.06, 0.12]} color={c.steel} />
      <Box at={[1.4, 0.12, -0.25]} size={[1.0, 0.06, 0.12]} color={c.steel} />
      {[0.55, -0.5].map((x) =>
        [0.5, -0.5].map((z) => (
          <Wheel color={c.tyre} key={`${String(x)}${String(z)}`} at={[x, 0.25, z]} radius={0.25} />
        )),
      )}
      {/* The operator: hi-vis vest and a head, seated under the guard. */}
      <mesh position={[-0.15, 1.2, 0]} raycast={() => null}>
        <cylinderGeometry args={[0.2, 0.24, 0.6, 12]} />
        <meshStandardMaterial color={c.vest} roughness={0.7} />
      </mesh>
      <mesh position={[-0.15, 1.65, 0]} raycast={() => null}>
        <sphereGeometry args={[0.15, 16, 12]} />
        <meshStandardMaterial color={c.skin} roughness={0.7} />
      </mesh>
    </group>
  );
}

/** Where a dock's truck stands: just outside the wall it is on, facing away from the building. */
function dockPlacement(outline: readonly (readonly number[])[], site: Bounds) {
  const xs = outline.map((p) => p[0] ?? 0);
  const zs = outline.map((p) => p[1] ?? 0);
  const cx = (Math.min(...xs) + Math.max(...xs)) / 2;
  const cz = (Math.min(...zs) + Math.max(...zs)) / 2;
  const sides = [
    { d: Math.abs(site.maxX - cx), at: [site.maxX + 0.6, cz] as const, turn: 0 },
    { d: Math.abs(cx - site.minX), at: [site.minX - 0.6, cz] as const, turn: Math.PI },
    { d: Math.abs(site.maxY - cz), at: [cx, site.maxY + 0.6] as const, turn: -Math.PI / 2 },
    { d: Math.abs(cz - site.minY), at: [cx, site.minY - 0.6] as const, turn: Math.PI / 2 },
  ].sort((a, b) => a.d - b.d);
  return sides[0] ?? null;
}

/** Forklift spots: in the staging zone and the cross aisle when the layout has them. */
function forkliftSpots(layout: Layout): { at: [number, number]; turn: number }[] {
  const spots: { at: [number, number]; turn: number }[] = [];
  const staging = layout.zones.find((z) => /dock|staging/i.test(z.name));
  if (staging) {
    const xs = staging.outline.map((p) => p[0]);
    const zs = staging.outline.map((p) => p[1]);
    const x = (Math.min(...xs) + Math.max(...xs)) / 2;
    const z0 = Math.min(...zs);
    const z1 = Math.max(...zs);
    spots.push({ at: [x, z0 + (z1 - z0) * 0.3], turn: -Math.PI / 2 });
    spots.push({ at: [x + 1.5, z0 + (z1 - z0) * 0.72], turn: 0 });
  }
  const aisle = layout.features.find((f) => f.feature_type === "AISLE");
  if (aisle) {
    const xs = aisle.outline.map((p) => p[0]);
    const zs = aisle.outline.map((p) => p[1]);
    spots.push({
      at: [(Math.min(...xs) + Math.max(...xs)) / 2, (Math.min(...zs) + Math.max(...zs)) / 2],
      turn: Math.PI / 2,
    });
  }
  return spots.slice(0, 3);
}

export function Scenery({
  layout,
  site,
  colors,
}: {
  layout: Layout;
  site: Bounds;
  colors: SceneColors;
}) {
  const c = palette(colors);
  const docks = layout.features
    .filter((f) => f.feature_type === "SHIPPING_DOCK" || f.feature_type === "RECEIVING_DOCK")
    .sort(
      (a, b) =>
        (a.feature_type === "SHIPPING_DOCK" ? -1 : 0) -
        (b.feature_type === "SHIPPING_DOCK" ? -1 : 0),
    )
    .map((f) => dockPlacement(f.outline, site))
    .filter((p): p is NonNullable<typeof p> => p !== null)
    .slice(0, 2);
  const apronSide = docks[0];
  return (
    <group>
      {apronSide && apronSide.turn === 0 && (
        // The yard beside the docks: an apron with painted truck bays.
        <group>
          <mesh position={[site.maxX + 8.6, -0.29, site.maxY / 2]} raycast={() => null}>
            <boxGeometry args={[16, 0.6, site.maxY + 1.5]} />
            <meshStandardMaterial color={c.apron} roughness={0.95} />
          </mesh>
          {docks
            .flatMap((d) => [d.at[1] - 1.8, d.at[1] + 1.8])
            .map((z) => (
              <mesh
                key={z}
                rotation={[-Math.PI / 2, 0, 0]}
                position={[site.maxX + 7, 0.02, z]}
                raycast={() => null}
              >
                <planeGeometry args={[12, 0.12]} />
                <meshBasicMaterial color={c.paint} />
              </mesh>
            ))}
        </group>
      )}
      {docks.map((d, i) => (
        <group key={i} position={[d.at[0], 0, d.at[1]]} rotation={[0, -d.turn, 0]}>
          <Truck c={c} />
        </group>
      ))}
      {forkliftSpots(layout).map((s, i) => (
        <group key={i} position={[s.at[0], 0, s.at[1]]} rotation={[0, s.turn, 0]}>
          <Forklift c={c} />
        </group>
      ))}
    </group>
  );
}
