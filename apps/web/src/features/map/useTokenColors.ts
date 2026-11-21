import { useEffect, useState } from "react";

import { readToken, type Token } from "../../shared/theme/theme";

const SCENE_TOKENS = [
  "--pw-color-background",
  "--pw-color-surface",
  "--pw-color-border",
  "--pw-color-secondary",
  "--pw-color-accent-soft",
  "--pw-color-text-muted",
  "--pw-color-focus-ring",
  "--pw-color-status-info",
  "--pw-color-status-warning",
  "--pw-color-status-critical",
  "--pw-color-map-slab",
  "--pw-color-map-slab-side",
  "--pw-color-map-floor-line",
  "--pw-color-map-rack",
  "--pw-color-map-carton",
  "--pw-color-map-carton-alt",
  "--pw-color-map-floor",
  "--pw-color-map-aisle-paint",
  "--pw-color-map-upright",
  "--pw-color-map-beam",
  "--pw-color-map-pallet",
  "--pw-color-map-wall",
  "--pw-color-map-dock",
  "--pw-color-map-truck",
  "--pw-color-map-truck-cab",
  "--pw-color-map-glass",
  "--pw-color-map-tyre",
  "--pw-color-map-forklift",
  "--pw-color-map-steel",
  "--pw-color-map-vest",
  "--pw-color-map-skin",
  "--pw-color-map-apron",
  "--pw-color-map-yard-paint",
] as const satisfies readonly Token[];

export type SceneColors = Record<(typeof SCENE_TOKENS)[number], string>;

function read(): SceneColors {
  return Object.fromEntries(SCENE_TOKENS.map((t) => [t, readToken(t)])) as SceneColors;
}

/** Token values for WebGL materials, re-read whenever the theme changes. */
export function useTokenColors(): SceneColors {
  const [colors, setColors] = useState(read);
  useEffect(() => {
    const observer = new MutationObserver(() => {
      setColors(read());
    });
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });
    return () => {
      observer.disconnect();
    };
  }, []);
  return colors;
}
