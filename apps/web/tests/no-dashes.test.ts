/**
 * FR-COP-13 and engineering baseline 5.1: no em dashes or en dashes in user-facing text.
 * Scans every source file of the web app; rendered text is checked by Playwright later.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const FORBIDDEN = /[–—]/u;
const SOURCE_ROOTS = ["src", "index.html"];

function files(path: string): string[] {
  if (statSync(path).isFile()) return [path];
  return readdirSync(path).flatMap((name) => files(join(path, name)));
}

describe("user-facing text", () => {
  it("contains no em dashes or en dashes", () => {
    const offenders = SOURCE_ROOTS.flatMap(files).flatMap((file) =>
      readFileSync(file, "utf8")
        .split("\n")
        .flatMap((line, i) => (FORBIDDEN.test(line) ? [`${file}:${String(i + 1)}`] : [])),
    );
    expect(offenders).toEqual([]);
  });
});
