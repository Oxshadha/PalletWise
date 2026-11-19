import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

/** Every file under the given roots (files are returned as they are). */
export function files(...roots: string[]): string[] {
  return roots.flatMap((path) =>
    statSync(path).isFile() ? [path] : files(...readdirSync(path).map((name) => join(path, name))),
  );
}

/** "file:line: text" for every line of `paths` that matches `test`. */
export function findLines(paths: string[], test: (line: string) => boolean): string[] {
  return paths.flatMap((file) =>
    readFileSync(file, "utf8")
      .split("\n")
      .flatMap((line, i) => (test(line) ? [`${file}:${String(i + 1)}: ${line.trim()}`] : [])),
  );
}
