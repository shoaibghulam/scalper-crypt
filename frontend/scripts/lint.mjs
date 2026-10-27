/**
 * Enumerate source files and hand them to oxlint explicitly.
 *
 * oxlint directory discovery finds nothing in this checkout, so the configured
 * lint script reported success while checking zero files. A lint that silently
 * passes is worse than no lint, because it is believed.
 */

import { spawnSync } from "node:child_process";
import { readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const EXTENSIONS = [".ts", ".tsx"];

function sourceFiles(dir) {
  const found = [];
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) {
      found.push(...sourceFiles(path));
    } else if (EXTENSIONS.some((ext) => entry.endsWith(ext))) {
      found.push(path);
    }
  }
  return found;
}

const files = sourceFiles("src");
if (files.length === 0) {
  console.error("lint: no source files found, which should be impossible");
  process.exit(1);
}

console.log("lint: checking " + files.length + " files");
const result = spawnSync("npx", ["oxlint", ...files], {
  stdio: "inherit",
  shell: true,
});
process.exit(result.status ?? 1);
