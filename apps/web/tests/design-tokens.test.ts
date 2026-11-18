/**
 * Design tokens are centralized: colors, font families and font sizes are defined only in
 * src/shared/theme/tokens.css. Everything else must use var(--pw-...) or readToken().
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { TOKENS } from "../src/shared/theme/theme";
import { files, findLines } from "./scan";

const TOKENS_FILE = join("src", "shared", "theme", "tokens.css");
const LITERAL = [
  /#[0-9a-fA-F]{3,8}\b/u, // hex colors
  /\brgba?\(/u, // rgb() colors
  /\bhsla?\(/u, // hsl() colors
  /font-family\s*:(?!\s*var\()/u, // font families
  /font-size\s*:(?!\s*var\()/u, // font sizes
];

describe("design tokens", () => {
  it("are the only place where colors, fonts and sizes are defined", () => {
    const sources = files("src").filter((f) => f !== TOKENS_FILE && /\.(css|tsx?)$/u.test(f));
    const offenders = findLines(sources, (line) => LITERAL.some((re) => re.test(line)));
    expect(offenders).toEqual([]);
  });

  it("every typed token exists in tokens.css for both themes", () => {
    const css = readFileSync(TOKENS_FILE, "utf8");
    const dark = css.slice(css.indexOf(':root[data-theme="dark"]'));
    for (const token of TOKENS) {
      expect(css.includes(`${token}:`), token).toBe(true);
      expect(dark.includes(`${token}:`), `${token} (dark)`).toBe(true);
    }
  });
});
