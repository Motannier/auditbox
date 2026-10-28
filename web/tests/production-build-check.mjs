import { readdir, readFile } from "node:fs/promises";
import { extname, join } from "node:path";
import { fileURLToPath } from "node:url";

const distRoot = fileURLToPath(new URL("../../dist/", import.meta.url));
const forbiddenDevelopmentOrigins = [
  "http://127.0.0.1:8000",
  "http://127.0.0.1:8001",
  "http://localhost:8000",
  "http://localhost:8001",
  "http://127.0.0.1:17890",
];

async function collectTextAssets(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const assets = [];
  for (const entry of entries) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) {
      assets.push(...await collectTextAssets(path));
    } else if ([".html", ".js", ".css"].includes(extname(entry.name))) {
      assets.push(path);
    }
  }
  return assets;
}

const textAssets = await collectTextAssets(distRoot);
for (const asset of textAssets) {
  const content = await readFile(asset, "utf8");
  const forbidden = forbiddenDevelopmentOrigins.find((origin) => content.includes(origin));
  if (forbidden) {
    throw new Error(`Production build contains development API origin ${forbidden} in ${asset}`);
  }
}

console.log(`Production build origin check passed (${textAssets.length} text assets)`);
