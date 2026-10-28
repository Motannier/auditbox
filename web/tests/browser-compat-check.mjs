import assert from "node:assert/strict";
import vm from "node:vm";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import postcss from "postcss";
import { chrome80Styles } from "../browser-compat-css.mjs";

const config = await readFile(new URL("../vite.config.js", import.meta.url), "utf8");
assert.match(config, /target: "chrome80"/);
assert.match(config, /cssTarget: "chrome80"/);
const bundled = await build({
  entryPoints: [fileURLToPath(new URL("../src/lib/browserCompat.js", import.meta.url))],
  bundle: true, write: false, platform: "browser", format: "iife", target: "chrome80",
});
const features = ["Object.hasOwn", "Array.prototype.at", "Array.prototype.findLast",
  "Array.prototype.findLastIndex", "String.prototype.replaceAll"];
const old = vm.createContext({});
vm.runInContext(features.map((feature) => `delete ${feature};`).join("\n"), old);
vm.runInContext(bundled.outputFiles[0].text, old);
assert.equal(vm.runInContext("Object.hasOwn(Object.create({inherited: 1}), 'inherited')", old), false);
assert.equal(vm.runInContext("Object.hasOwn(Object.assign(Object.create(null), {x: 1}), 'x')", old), true);
assert.equal(vm.runInContext("Object.hasOwn({hasOwnProperty: null}, 'hasOwnProperty')", old), true);
assert.equal(vm.runInContext("[1, 2, 3].at(-1)", old), 3);
assert.equal(vm.runInContext("[1].at(-2)", old), undefined);
assert.equal(vm.runInContext("[1, 2, 1].findLastIndex(x => x === 1)", old), 2);
assert.equal(vm.runInContext("[1, 2, 1].findLast(x => x === 2)", old), 2);
assert.equal(vm.runInContext(String.raw`'a\r\nb\r\n'.replaceAll('\r\n', '\n')`, old), "a\nb\n");
assert.throws(() => vm.runInContext("'aa'.replaceAll(/a/, 'x')", old), /global/);
const modern = vm.createContext({});
vm.runInContext("globalThis.originals = [Object.hasOwn, Array.prototype.at, Array.prototype.findLast, Array.prototype.findLastIndex, String.prototype.replaceAll]", modern);
vm.runInContext(bundled.outputFiles[0].text, modern);
assert.equal(vm.runInContext(features.map((feature, index) => `${feature} === originals[${index}]`).join(" && "), modern), true);

const source = ".row, .other { display:flex; gap:8px; margin:12px; background:red } .column {display:flex;flex-direction:column;gap:5px} .grid {display:grid;gap:10px} button:hover, button:focus-visible {outline:2px solid teal}";
const css = await postcss([chrome80Styles()]).process(source + ".viewport {height:100vh;height:100dvh} .no-button-layout .row {display:block;padding-left:68px}", { from: undefined });
const row = css.root.nodes.find((rule) => rule.selector === ".row, .other");
assert.ok(row, "Modern selectors must remain unchanged");
assert.equal(row.nodes.find((decl) => decl.prop === "gap").value, "8px");
assert.equal(row.nodes.find((decl) => decl.prop === "margin").value, "12px");
let emulated = 0;
css.root.walkRules((rule) => {
  if (rule.nodes.some((decl) => decl.prop?.startsWith("--fgp"))) {
    emulated++;
    assert.ok(rule.selectors.every((selector) => selector.includes(".no-flex-gap") || selector.includes(".no-button-layout")),
      `Legacy styles escaped their feature-detection scope: ${rule.selector}`);
  }
});
assert.ok(emulated > 0);
assert.ok(css.css.includes(".no-flex-gap.no-button-layout .row"), "Both flags live on the same root element");
assert.ok(!css.css.includes(".no-flex-gap .no-button-layout .row"));
css.root.walkRules((rule) => {
  if (rule.selector.includes(".no-flex-gap")) {
    rule.walkDecls((decl) => assert.ok(!/\d(?:dvh|cqw|cqh)/.test(decl.value), "Unsupported units must not override the old viewport fallback"));
  }
});
assert.ok(!css.css.includes("pointer-events: var(--has-fgp)"), "Flex containers, including text-only buttons, must stay clickable");
assert.ok(css.css.includes("button:focus"), "Keyboard focus needs a Chrome 80 selector");
console.log("Chrome 80 compatibility passed: build target, missing APIs, native preservation, isolated flex-gap and focus fallbacks");
