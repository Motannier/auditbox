import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { chromium } from "playwright-core";

const bundle = await build({
  entryPoints: [fileURLToPath(new URL("../src/lib/browserCompat.js", import.meta.url))],
  bundle: true, write: false, platform: "browser", format: "iife", target: "chrome80",
});
const executablePath = [process.env.CHROME_PATH,
  "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
].find((candidate) => candidate && existsSync(candidate));
assert.ok(executablePath, "Chrome/Chromium not found; set CHROME_PATH to its executable path");
const browser = await chromium.launch({ headless: true, executablePath });
try {
  const page = await browser.newPage();
  await page.setContent("<main>original</main>");
  await page.evaluate(() => {
    delete Element.prototype.replaceChildren;
    delete DocumentFragment.prototype.replaceChildren;
    delete Object.hasOwn;
    delete Array.prototype.at;
  });
  await page.addScriptTag({ content: bundle.outputFiles[0].text });
  const result = await page.evaluate(() => {
    const element = document.querySelector("main");
    const child = document.createElement("span");
    child.textContent = "node";
    element.replaceChildren("text", child);
    const replace = element.innerHTML === "text<span>node</span>" && element.lastChild === child;
    element.replaceChildren(child, child, "tail");
    const move = element.childNodes.length === 2 && element.firstChild === child;
    let hierarchy = false;
    try { element.replaceChildren(element); } catch (error) { hierarchy = error.name === "HierarchyRequestError"; }
    const intact = element.parentNode === document.body && element.firstChild === child;
    const fragment = document.createDocumentFragment();
    fragment.replaceChildren("fragment", child);
    const fragments = fragment.textContent === "fragmentnode" && fragment.lastChild === child;
    fragment.replaceChildren();
    element.replaceChildren();
    return { replace, move, hierarchy, intact, fragments,
      empty: fragment.childNodes.length === 0 && element.childNodes.length === 0,
      api: Object.hasOwn({x: 1}, "x") && [1, 2].at(-1) === 2 };
  });
  assert.ok(Object.values(result).every(Boolean), JSON.stringify(result));
  const native = await browser.newPage();
  await native.setContent("<main></main>");
  await native.evaluate(() => window.originalReplaceChildren = Element.prototype.replaceChildren);
  await native.addScriptTag({ content: bundle.outputFiles[0].text });
  assert.ok(await native.evaluate(() => window.originalReplaceChildren === Element.prototype.replaceChildren));
  console.log("Browser DOM compatibility passed: replaceChildren strings, nodes, moves, fragments, hierarchy validation, clear and native preservation");
} finally {
  await browser.close();
}
