/**
 * FR-COP-13 and engineering baseline 5.1: no em dashes or en dashes in user-facing text.
 * Scans every source file of the web app; rendered text is checked by Playwright later.
 */
import { describe, expect, it } from "vitest";

import { files, findLines } from "./scan";

const FORBIDDEN = /[\u2013\u2014]/u;

describe("user-facing text", () => {
  it("contains no em dashes or en dashes", () => {
    expect(findLines(files("src", "index.html"), (line) => FORBIDDEN.test(line))).toEqual([]);
  });
});
