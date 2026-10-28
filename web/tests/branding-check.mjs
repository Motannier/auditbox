import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";
import { DEFAULT_PRODUCT_NAME } from "../src/constants/branding.js";

const url = process.env.WEBUI_TEST_URL || "http://127.0.0.1:5200";
const server = process.env.WEBUI_TEST_URL ? null : spawn(process.execPath, [
  fileURLToPath(new URL("../node_modules/vite/bin/vite.js", import.meta.url)),
  "--host", "127.0.0.1", "--port", "5200", "--strictPort",
], { cwd: fileURLToPath(new URL("..", import.meta.url)), stdio: "ignore" });
const executablePath = [process.env.CHROME_PATH, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", "/usr/bin/chromium",
].filter(Boolean).find(existsSync);
let browser;
try {
  for (let attempt = 0; ; attempt++) {
    try { if ((await fetch(url)).ok) break; } catch { /* Wait for Vite. */ }
    assert.ok(attempt < 80, "Frontend did not start");
    await delay(100);
  }
  browser = await chromium.launch({ headless: true, executablePath });
  const cases = [
    [DEFAULT_PRODUCT_NAME, DEFAULT_PRODUCT_NAME, 1440],
    [DEFAULT_PRODUCT_NAME, DEFAULT_PRODUCT_NAME, 2048, 995],
    [DEFAULT_PRODUCT_NAME, DEFAULT_PRODUCT_NAME, 1920, 720],
    [DEFAULT_PRODUCT_NAME, DEFAULT_PRODUCT_NAME, 1024, 1100],
    [DEFAULT_PRODUCT_NAME, DEFAULT_PRODUCT_NAME, 320, 740],
    ["客户定制推理工具", "客户定制推理工具", 1280],
    ["客户定制推理工具", "客户定制推理工具", 390],
    ["专用设备智能调试与轻量化离线推理工具", "专用设备智能调试与轻量化离线推理工具", 960],
    ["x".repeat(80), "x".repeat(80), 390],
    ["<b>自定义名称</b>", "<b>自定义名称</b>", 1280],
    ["", DEFAULT_PRODUCT_NAME, 1280],
    ["x".repeat(81), DEFAULT_PRODUCT_NAME, 1280],
    ["名字\u001b[0m", DEFAULT_PRODUCT_NAME, 1280],
    [null, DEFAULT_PRODUCT_NAME, 1280],
    ["timeout", DEFAULT_PRODUCT_NAME, 1280],
  ];
  for (const [configured, expected, width, height = 900] of cases) {
    const context = await browser.newContext({ viewport: { width, height } });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      const json = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
      if (path === "/api/branding") {
        if (configured === "timeout") { await delay(2200); return route.abort(); }
        return configured === null ? json({}, 503) : json({ product_name: configured });
      }
      if (path === "/api/auth/login") return json({ username: "opsadmin", role: "admin", token: "mock" });
      if (path === "/api/devices") return json({ items: [], total: 0, page: 1, page_size: 50 });
      if (["/api/devices/status", "/api/recent-connections"].includes(path)) return json([]);
      return json({ ready: true });
    });
    await page.goto(new URL("/login", url).href);
    const title = page.locator(".login-enterprise-product-name");
    await title.waitFor({ state: "visible", timeout: 5000 });
    // React inserts the image after the config request; DOM readiness does not
    // imply that a board-served illustration has finished downloading.
    await page.waitForFunction(() => {
      const image = document.querySelector(".login-enterprise-hero-image");
      return image?.complete && image.naturalWidth > 0;
    });
    assert.equal(await title.textContent(), expected);
    assert.equal(await title.locator("*").count(), 0, "Config must be rendered as text, not HTML");
    assert.equal(await page.title(), expected);
    assert.equal(await page.locator(".login-enterprise-heading p").textContent(), `登录${expected}，开始安全运维`);
    const layout = await page.evaluate(() => {
      const title = document.querySelector(".login-enterprise-product-name");
      const frame = document.querySelector(".login-enterprise-hero-frame");
      const hero = document.querySelector(".login-enterprise-hero");
      const image = document.querySelector(".login-enterprise-hero-image");
      return { title: title.getBoundingClientRect().toJSON(), frame: frame.getBoundingClientRect().toJSON(),
        hero: hero.getBoundingClientRect().toJSON(), image: image.getBoundingClientRect().toJSON(),
        titleOverflow: title.scrollWidth > title.clientWidth + 1,
        overflow: document.documentElement.scrollWidth > innerWidth,
        imageLoaded: image.complete && image.naturalWidth > 0 };
    });
    assert.ok(layout.imageLoaded);
    assert.ok(Math.abs(layout.image.width - layout.image.height) < 1, "Image must remain square, not stretched");
    assert.ok(layout.image.x <= layout.hero.x + 1 && layout.image.right >= layout.hero.right - 1
      && layout.image.y <= layout.hero.y + 1 && layout.image.bottom >= layout.hero.bottom - 1,
      `Artwork must cover every edge, without color bars (${width}x${height})`);
    assert.ok(layout.title.x >= layout.hero.x && layout.title.right <= layout.hero.right,
      "Configurable title must remain inside the visible crop");
    assert.ok(!layout.overflow && !layout.titleOverflow, `Branding overflow: ${expected} at ${width}`);
    assert.ok(layout.title.y >= layout.frame.y && layout.title.bottom < layout.frame.y + layout.frame.height * 0.19,
      "Title must stay above the illustration subtitle");
    if (process.env.WEBUI_BRANDING_SCREENSHOT && configured === DEFAULT_PRODUCT_NAME && width === 2048) {
      await page.screenshot({ path: process.env.WEBUI_BRANDING_SCREENSHOT, fullPage: true });
    }
    await page.getByLabel("密码", { exact: true }).fill("mock-password");
    await page.getByRole("button", { name: "登录", exact: true }).click();
    await page.waitForURL("**/console");
    await page.locator(".topbar-left strong").waitFor();
    assert.equal(await page.locator(".topbar-left strong").textContent(), expected);
    assert.equal(await page.locator(".topbar .client-device-label").count(), 0);
    assert.equal(await page.title(), expected);
    assert.deepEqual(errors, []);
    await context.close();
  }
  console.log(`Branding checks passed (${cases.length} configurations/viewports)`);
} finally {
  await browser?.close();
  server?.kill();
}
