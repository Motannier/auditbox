#!/usr/bin/env node
// Build an independent execution-rule catalog. Never rewrite reference/graph data.
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { gzipSync } from 'node:zlib';

const sourceDir = process.argv[2];
if (!sourceDir) throw new Error('Usage: node tools/build_switch_risk.mjs <risk_export directory> [output directory]');
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const outputDir = process.argv[3] || resolve(root, 'box/auditbox/security/data');
const digest = bytes => createHash('sha256').update(bytes).digest('hex');
const needsReview = /原文尚不足|不能排除|不确定|尚未|无法确定|暂按|待核|不足以|不完整/;
const profiles = [['h3c_comware', '华三'], ['huawei_vrp', '华为'], ['cisco_ios', '思科'], ['zte_zxr10', '中兴']];
const manifest = { schema: 1, policy_version: '2026-09-15.switch-confirm.1', profiles: {} };
mkdirSync(outputDir, { recursive: true });
for (const [profile, brand] of profiles) {
  const bytes = readFileSync(resolve(sourceDir, `${brand}.json`));
  const doc = JSON.parse(bytes);
  if (doc.brand !== brand || !doc.policy) throw new Error(`Invalid source: ${brand}`);
  const strings = [''], indices = new Map([['', 0]]), ids = new Set();
  const intern = value => {
    const text = String(value || '');
    if (!indices.has(text)) { indices.set(text, strings.length); strings.push(text); }
    return indices.get(text);
  };
  const entries = [], counts = { risk: 0, safe: 0, pending: 0, source_pending: 0 };
  for (const [name, risk] of [['有风险', true], ['无风险', false]]) {
    const data = doc.sheets[name];
    if (data.rows.length !== data.variants || data.ids.length !== data.rows.length) throw new Error(`Bad counts: ${brand}`);
    data.rows.forEach((row, i) => {
      const id = data.ids[i];
      if (!id || ids.has(id) || !row[0] || !row[1] || (risk && !row[2])) throw new Error(`Bad variant: ${brand}/${id}`);
      ids.add(id);
      const originalPending = risk && needsReview.test(row[2]);
      // The review found a probable placeholder-versus-redirection error.
      // Keep it pending (confirmation required), never silently allow it.
      const ambiguousRedirect = brand === '中兴' && id === 'de475800a38123476f7d5709';
      const pending = originalPending || ambiguousRedirect;
      const reason = ambiguousRedirect
        ? '原文参数尖括号与输出重定向存在提取歧义，实际命令形式尚待核对，默认需要二次确认。'
        : (data.notes[i]?.[2] || row[2]);
      counts[risk ? 'risk' : 'safe']++;
      if (pending) counts.pending++;
      if (originalPending) counts.source_pending++;
      entries.push([id, intern(row[0]), intern(row[1]), risk ? (pending ? 2 : 1) : 0,
        intern(risk ? reason : ''), intern(row[3]), intern(row[4]), intern(row[5])]);
    });
  }
  const metadata = { profile, brand, variants: entries.length, ...counts, source_sha256: digest(bytes) };
  const packed = gzipSync(Buffer.from(JSON.stringify({ schema: 1, policy_version: manifest.policy_version, ...metadata, strings, entries })), { level: 9 });
  writeFileSync(resolve(outputDir, `${profile}.json.gz`), packed);
  manifest.profiles[profile] = { ...metadata, bytes: packed.length, sha256: digest(packed) };
  console.log(`${brand}: ${entries.length} forms; ${counts.risk} risk, ${counts.pending} pending; ${packed.length} bytes`);
}
writeFileSync(resolve(outputDir, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
