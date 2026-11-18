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
  "--pw-color-accent-soft",
  "--pw-color-secondary",
  "--pw-color-border",
  "--pw-color-status-info",
  "--pw-color-status-warning",
  "--pw-color-status-critical",
  "--pw-color-status-success",
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
  const dark =
    choice === "dark" ||
    (choice === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset["theme"] = dark ? "dark" : "light";
}
