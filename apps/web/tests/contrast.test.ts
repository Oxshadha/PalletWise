/**
 * WCAG 2.2 AA contrast of every token pair the interface uses (UX spec 8), in both themes.
 * Text needs 4.5:1; non-text marks (focus ring, map outlines, status marks) need 3:1.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const css = readFileSync(join("src", "shared", "theme", "tokens.css"), "utf8");

function block(selector: string): string {
  const start = css.indexOf(selector);
  return css.slice(start, css.indexOf("}", start));
}

function tokens(theme: "light" | "dark"): Record<string, string> {
  const base = block(":root {");
  const source = theme === "light" ? base : base + block(':root[data-theme="dark"]');
  const found: Record<string, string> = {};
  for (const match of source.matchAll(/(--pw-color-[a-z-]+):\s*(#[0-9a-fA-F]{6})/gu)) {
    const [, name, value] = match;
    if (name && value) found[name] = value;
  }
  return found;
}

function luminance(hex: string): number {
  const channels = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  const [r, g, b] = channels.map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * (r ?? 0) + 0.7152 * (g ?? 0) + 0.0722 * (b ?? 0);
}

function contrast(a: string, b: string): number {
  const [high, low] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return ((high ?? 0) + 0.05) / ((low ?? 0) + 0.05);
}

const SURFACES = ["--pw-color-surface", "--pw-color-background"];
const TEXT = ["--pw-color-text-primary", "--pw-color-text-muted", "--pw-color-accent-text"];
const MARKS = [
  "--pw-color-focus-ring",
  "--pw-color-status-info",
  "--pw-color-status-warning",
  "--pw-color-status-critical",
];

describe.each(["light", "dark"] as const)("%s theme contrast", (theme) => {
  const t = tokens(theme);
  const color = (name: string): string => {
    const value = t[name];
    if (!value) throw new Error(`${name} missing in the ${theme} theme`);
    return value;
  };

  it.each(TEXT.flatMap((text) => SURFACES.map((surface) => [text, surface])))(
    "text %s on %s is at least 4.5:1",
    (text, surface) => {
      expect(contrast(color(text), color(surface))).toBeGreaterThanOrEqual(4.5);
    },
  );

  it("labels on filled buttons are at least 4.5:1", () => {
    expect(
      contrast(color("--pw-color-text-on-accent"), color("--pw-color-accent-strong")),
    ).toBeGreaterThanOrEqual(4.5);
  });

  it.each(MARKS.flatMap((mark) => SURFACES.map((surface) => [mark, surface])))(
    "mark %s on %s is at least 3:1",
    (mark, surface) => {
      expect(contrast(color(mark), color(surface))).toBeGreaterThanOrEqual(3);
    },
  );
});
