/**
 * Design tokens are centralized: colors, font families and font sizes are defined only in
 * src/shared/theme/tokens.css. Everything else must use var(--pw-...) or readToken().
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { TOKENS } from "../src/shared/theme/theme";

const TOKENS_FILE = join("src", "shared", "theme", "tokens.css");
const LITERAL = [
  /#[0-9a-fA-F]{3,8}\b/u, // hex colors
  /\brgba?\(/u, // rgb() colors
  /\bhsla?\(/u, // hsl() colors
  /font-family\s*:(?!\s*var\()/u, // font families
  /font-size\s*:(?!\s*var\()/u, // font sizes
];

function files(path: string): string[] {
  if (statSync(path).isFile()) return [path];
  return readdirSync(path).flatMap((name) => files(join(path, name)));
}

describe("design tokens", () => {
  it("are the only place where colors, fonts and sizes are defined", () => {
    const offenders = files("src")
      .filter((f) => f !== TOKENS_FILE && /\.(css|tsx?)$/u.test(f))
      .flatMap((file) =>
        readFileSync(file, "utf8")
          .split("\n")
          .flatMap((line, i) =>
            LITERAL.some((re) => re.test(line)) ? [`${file}:${String(i + 1)}: ${line.trim()}`] : [],
          ),
      );
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
