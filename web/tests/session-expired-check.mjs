import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { spawn } from "node:child_process";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import fs from "node:fs/promises";
import { chromium } from "playwright-core";

const base = process.env.WEBUI_TEST_URL || "http://127.0.0.1:5232";
const artifacts = process.env.AUTH_TEST_ARTIFACTS;
const server = process.env.WEBUI_TEST_URL ? null : spawn(process.execPath, [
  fileURLToPath(new URL("../node_modules/vite/bin/vite.js", import.meta.url)),
  "--host", "127.0.0.1", "--port", "5232", "--strictPort",
], { cwd: fileURLToPath(new URL("..", import.meta.url)), stdio: "ignore" });
const executablePath = [process.env.CHROME_PATH,
  "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
].find((candidate) => candidate && existsSync(candidate));
assert.ok(executablePath, "Chrome/Chromium not found; set CHROME_PATH to its executable path");
let browser;
try {
  if (artifacts) await fs.mkdir(artifacts, { recursive: true });
  for (let n = 0; ; n++) {
    try { if ((await fetch(base)).ok) break; } catch {}
    assert.ok(n < 80, "Frontend did not start"); await delay(100);
  }
  browser = await chromium.launch({ headless: true, executablePath });

  async function setup(mode, width = 1366, height = 850, authenticated = true) {
    const page = await browser.newPage({ viewport: { width, height } });
    page.setDefaultTimeout(6000);
    const errors = []; page.on("pageerror", error => errors.push(error.message));
    if (authenticated) await page.addInitScript(() => sessionStorage.setItem("auditbox_auth", JSON.stringify({
      username: "opsadmin", role: "admin", token: "old-token",
    })));
    await page.route("**/api/**", async route => {
      const request = route.request(), path = new URL(request.url()).pathname;
      const json = (value, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
      if (path === "/api/branding") return json({ product_name: "轻量化调试推理工具" });
      if (path === "/api/device-profiles") return json({ profiles: [] });
      if (path === "/api/auth/login") {
        if (request.postDataJSON().password !== "test-only-password") return json({ detail: "用户名或密码错误" }, 401);
        mode = "healthy";
        return json({ username: "opsadmin", role: "admin", token: "new-token" });
      }
      if (path === "/api/system/clock/sync") {
        if (mode === "clock-expired") return json({ detail: "登录状态已失效，请重新登录" }, 401);
        if (mode === "clock-error") return json({ detail: "盒子时间校准失败，请检查服务校时权限" }, 503);
        return json({ synchronized: false });
      }
      if (path === "/api/audit/behavior") return mode === "audit-expired"
        ? json({ detail: "登录状态已失效，请重新登录" }, 401) : route.fulfill({ status: 204 });
      if (path === "/api/devices") return mode === "api-expired"
        ? json({ detail: "登录状态已失效，请重新登录" }, 401) : json({ items: [], total: 0, page: 1, page_size: 50 });
      if (path === "/api/sessions") return json({ detail: "SSH 用户名或密码错误，请重新输入" }, 401);
      if (path === "/api/settings/ethernet") return json({ supported: false });
      if (["/api/recent-connections", "/api/devices/status"].includes(path)) return json([]);
      return json({ ready: true, data: [] });
    });
    await page.goto(base + (authenticated ? "/console" : "/login"));
    return { page, errors };
  }

  for (const [width, height] of [[1366, 850], [780, 850], [390, 844], [320, 568], [780, 320]]) {
    const { page, errors } = await setup("clock-expired", width, height);
    const dialog = page.getByRole("alertdialog", { name: "登录已失效" });
    await dialog.waitFor();
    assert.equal(await dialog.getByRole("button").count(), 1);
    assert.equal(await page.locator(".clock-sync-notice").count(), 0);
    const box = await dialog.boundingBox();
    assert.ok(Math.abs(box.x + box.width / 2 - width / 2) < 2);
    assert.ok(Math.abs(box.y + box.height / 2 - height / 2) < 2);
    assert.ok(box.x >= 23 && box.y >= 23 && box.x + box.width <= width - 23 && box.y + box.height <= height - 23);
    await page.keyboard.press("Escape"); await page.keyboard.press("Tab");
    await page.keyboard.press("Shift+Tab");
    assert.ok(await dialog.getByRole("button", { name: "重新登录" }).evaluate(node => node === document.activeElement));
    await page.mouse.click(width - 55, 28);
    assert.equal(await page.locator(".network-settings-dialog").count(), 0, "Overlay click reached settings");
    assert.ok(await dialog.isVisible(), "Backdrop or Escape dismissed the expiry warning");
    assert.ok(await page.evaluate(() => Boolean(sessionStorage.getItem("auditbox_auth"))), "Overlay click reached logout");
    // Check the focus fallback used by browsers without native inert.
    await page.evaluate(() => {
      document.querySelector("#root").removeAttribute("inert");
      document.querySelector(".topbar button").focus();
    });
    assert.ok(await dialog.getByRole("button").evaluate(node => node === document.activeElement));
    if (artifacts) await page.screenshot({ path: `${artifacts}/session-expired-${width}x${height}.png` });
    await dialog.getByRole("button", { name: "重新登录" }).click();
    await page.waitForURL("**/login");
    assert.equal(await page.locator(".session-expired-backdrop").count(), 0);
    assert.equal(await page.evaluate(() => sessionStorage.getItem("auditbox_auth")), null);
    assert.equal(await page.locator("#root").getAttribute("aria-hidden"), null);
    assert.equal(await page.locator("#root").getAttribute("inert"), null);
    if (width === 1366) {
      await page.getByLabel("密码", { exact: true }).fill("wrong-password");
      await page.getByRole("button", { name: "登录", exact: true }).click();
      await page.getByText("用户名或密码错误", { exact: true }).waitFor();
      assert.equal(await page.getByRole("alertdialog").count(), 0, "Wrong login password opened an expiry dialog");
      await page.getByLabel("密码", { exact: true }).fill("test-only-password");
      await page.getByRole("button", { name: "登录", exact: true }).click();
      await page.waitForURL("**/console");
      await page.evaluate(() => window.dispatchEvent(new CustomEvent("auditbox-auth-expired", { detail: { token: "old-token" } })));
      assert.equal(await page.getByRole("alertdialog").count(), 0, "Old account response expired the new login");
    }
    assert.deepEqual(errors, []);
    await page.close();
    console.log(`Session expiry layout passed: ${width}x${height}, pointer/focus protection and relogin`);
  }
  for (const mode of ["api-expired", "audit-expired", "clock-error", "healthy"]) {
    const { page, errors } = await setup(mode);
    await page.locator(".topbar").waitFor();
    if (mode === "audit-expired") await page.getByRole("button", { name: "设置", exact: true }).click();
    if (mode.endsWith("expired")) await page.getByRole("alertdialog", { name: "登录已失效" }).waitFor();
    else {
      if (mode === "clock-error") await page.locator(".clock-sync-notice").waitFor();
      const error = await page.evaluate(async () => {
        const { apiRequest } = await import("/src/services/http.js");
        try { await apiRequest("/api/sessions", { method: "POST", body: "{}" }); }
        catch (error) { return { message: error.message, status: error.status }; }
      });
      assert.equal(error.status, 401);
      assert.match(error.message, /SSH 用户名或密码错误/);
      assert.equal(await page.getByRole("alertdialog").count(), 0, "Target credential or clock error expired the box login");
    }
    assert.deepEqual(errors, []); await page.close();
    console.log(`Session expiry classification passed: ${mode}`);
  }
} finally { await browser?.close(); server?.kill(); }
