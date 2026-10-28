#!/usr/bin/env node
// Package reviewed exports; this does not extract or reclassify any manuals.
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { createHash } from "node:crypto";
import { gzipSync } from "node:zlib";

const sourceDir = process.argv[2];
if (!sourceDir) throw new Error("Usage: node tools/build_switch_reference.mjs <risk_export directory>");
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const outputDir = resolve(root, "box/auditbox/reference/data");
mkdirSync(outputDir, { recursive: true });
const sha256 = (buffer) => createHash("sha256").update(buffer).digest("hex");
const manifest = { schema: 1, profiles: {} };
for (const [profile, brand] of [["huawei_vrp", "华为"], ["cisco_ios", "思科"], ["zte_zxr10", "中兴"]]) {
  const source = readFileSync(resolve(sourceDir, `${brand}.json`));
  const doc = JSON.parse(source);
  if (doc.brand !== brand || !doc.policy) throw new Error(`Invalid export for ${brand}`);
  const strings = [""];
  const indices = new Map([["", 0]]);
  const intern = (value) => {
    const text = String(value || "");
    if (!indices.has(text)) { indices.set(text, strings.length); strings.push(text); }
    return indices.get(text);
  };
  const entries = [];
  const ids = new Set();
  const names = new Set();
  const counts = { risk: 0, safe: 0 };
  for (const [sheetName, risk] of [["有风险", 1], ["无风险", 0]]) {
    const sheet = doc.sheets[sheetName];
    if (sheet.rows.length !== sheet.variants || sheet.ids.length !== sheet.rows.length) {
      throw new Error(`Variant count mismatch: ${brand}/${sheetName}`);
    }
    sheet.rows.forEach((row, index) => {
      const id = sheet.ids[index];
      const notes = sheet.notes[index] || {};
      if (!id || ids.has(id) || !row[0] || !row[1] || (risk && !row[2])) {
        throw new Error(`Incomplete/duplicate variant: ${brand}/${id}`);
      }
      ids.add(id);
      names.add(row[0]);
      counts[risk ? "risk" : "safe"]++;
      entries.push([id, intern(row[0]), intern(row[1]), risk,
        intern(risk ? notes[2] || row[2] : ""), intern(notes[3] || row[3]),
        intern(notes[4] || row[4]), intern(notes[5] || row[5])]);
    });
  }
  const metadata = { brand, profile, policy: doc.policy, command_names: names.size,
    variants: entries.length, ...counts, source_sha256: sha256(source) };
  const packed = gzipSync(Buffer.from(JSON.stringify({ schema: 1, ...metadata, strings, entries })), { level: 9 });
  writeFileSync(resolve(outputDir, `${profile}.json.gz`), packed);
  manifest.profiles[profile] = { ...metadata, bytes: packed.length, sha256: sha256(packed) };
  console.log(`${brand}: ${names.size} command names, ${entries.length} variants, ${(packed.length / 1048576).toFixed(2)} MiB`);
}
writeFileSync(resolve(outputDir, "manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`);
