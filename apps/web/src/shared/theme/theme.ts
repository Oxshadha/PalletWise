/**
 * Typed access to the design tokens defined in tokens.css (the single source of truth).
 * TypeScript never holds color or size values; it reads them, so a theme change in CSS
 * reaches canvas and WebGL code (the 3D map) too.
 */

export const TOKENS = [
  "--pw-color-background",
  "--pw-color-surface",
  "--pw-color-text-primary",
  "--pw-color-text-muted",
  "--pw-color-text-on-accent",
  "--pw-color-accent",
  "--pw-color-accent-text",
  "--pw-color-accent-strong",
  "--pw-color-accent-soft",
  "--pw-color-focus-ring",
  "--pw-color-secondary",
  "--pw-color-border",
  "--pw-color-status-info",
  "--pw-color-status-warning",
  "--pw-color-status-critical",
  "--pw-color-status-success",
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
] as const;

export type Token = (typeof TOKENS)[number];
export type ThemeChoice = "light" | "dark" | "system";

const STORAGE_KEY = "pw-theme";

/** Current value of a token, e.g. for Three.js materials. */
export function readToken(token: Token): string {
  return getComputedStyle(document.documentElement).getPropertyValue(token).trim();
}

export function storedThemeChoice(): ThemeChoice {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

/** Applies and remembers a theme choice; same rules as public/theme-init.js. */
export function setThemeChoice(choice: ThemeChoice): void {
  try {
    localStorage.setItem(STORAGE_KEY, choice);
  } catch {
    /* storage unavailable: the choice applies to this page only */
  }
  applyTheme(choice);
}

/** The one rule mapping a choice to the rendered theme (public/theme-init.js mirrors it). */
export function applyTheme(choice: ThemeChoice): void {
  const dark =
    choice === "dark" ||
    (choice === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset["theme"] = dark ? "dark" : "light";
}

/** While the choice is "system", follow the operating system when it switches light/dark. */
export function followSystemTheme(choice: ThemeChoice): () => void {
  if (choice !== "system") return () => undefined;
  const query = window.matchMedia("(prefers-color-scheme: dark)");
  const apply = () => {
    applyTheme("system");
  };
  query.addEventListener("change", apply);
  return () => {
    query.removeEventListener("change", apply);
  };
}
