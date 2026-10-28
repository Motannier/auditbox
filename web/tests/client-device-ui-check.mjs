import assert from "node:assert/strict";
import { deviceCases } from "./client-device-check.mjs";

export async function checkClientDeviceFlow(browser, url) {
  for (const [index, example] of deviceCases.entries()) {
    const context = await browser.newContext({
      userAgent: example.userAgent,
      viewport: { width: index === 5 ? 390 : 1280, height: 820 },
    });
    context.setDefaultTimeout(10_000);
    await context.addInitScript((device) => {
      Object.defineProperty(navigator, "platform", { get: () => device.platform });
      Object.defineProperty(navigator, "maxTouchPoints", { get: () => device.maxTouchPoints || 0 });
      Object.defineProperty(navigator, "userAgentData", { get: () => undefined });
      // Upgrades must ignore both old valid and expired Guardian cache entries.
      sessionStorage.setItem("auditbox_security_preflight", JSON.stringify({
        admissionToken: "old-guardian-token",
        expiresAt: device.label.startsWith("Windows") ? Date.now() + 60_000 : 1,
        config: { required: true, local_url: "http://127.0.0.1:17890" },
      }));
      // A previous login/version may have left a permanently collapsed sidebar.
      localStorage.setItem("auditbox-device-panel-collapsed", "true");
      if (localStorage.getItem("auditbox-device-panel-width") === null) {
        localStorage.setItem("auditbox-device-panel-width", "320");
      }
    }, example);
    const page = await context.newPage();
    const guardianRequests = [];
    const loginPayloads = [];
    page.on("request", (request) => {
      if (request.url().includes(":17890/") || request.url().includes("/api/security/")) {
        guardianRequests.push(request.url());
      }
    });
    await page.route("**/api/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      const json = (body, status = 200) => route.fulfill({
        status, contentType: "application/json", body: JSON.stringify(body),
      });
      if (path === "/api/system/clock/sync") return json({ synchronized: false, server_time_ms: Date.now() });
      if (path === "/api/auth/login") {
        const payload = route.request().postDataJSON();
        loginPayloads.push(payload);
        return payload.password === "correct-password"
          ? json({ username: "opsadmin", role: "admin", token: "device-test-token" })
          : json({ detail: "用户名或密码错误" }, 401);
      }
      if (path === "/api/devices") return json({ items: [], total: 0, page: 1, page_size: 50 });
      if (path === "/api/devices/status" || path === "/api/recent-connections") return json([]);
      if (path === "/api/assistant/status") return json({ ready: true, model: "test" });
      return json({ detail: "unavailable" }, 503);
    });
    await page.route("http://127.0.0.1:17890/**", (route) => route.abort());

    try {
      await page.goto(new URL("/login", url).href, { waitUntil: "domcontentloaded" });
      await page.getByText(`接入设备：${example.label}`, { exact: true }).waitFor();
      assert.equal(await page.getByText("安全运行环境已就绪", { exact: true }).count(), 0);
      await page.getByLabel("密码", { exact: true }).fill("wrong-password");
      await page.getByRole("button", { name: "登录", exact: true }).click();
      await page.getByRole("alert").filter({ hasText: "用户名或密码错误" }).waitFor();
      await page.getByLabel("密码", { exact: true }).fill("correct-password");
      await page.getByRole("button", { name: "登录", exact: true }).click();
      await page.waitForURL("**/console");
      await page.locator(".topbar").waitFor();
      assert.equal(await page.locator(".topbar .client-device-label").count(), 0);
      const sidebarToggle = page.locator(".device-panel-edge-toggle");
      const sidebarResizer = page.locator(".device-panel-resizer");
      assert.equal(await sidebarToggle.getAttribute("aria-expanded"), "true",
        "Login must ignore a previously persisted collapsed sidebar");
      assert.equal(await sidebarResizer.getAttribute("aria-valuenow"), "320");
      if (index === 0) await sidebarResizer.press("ArrowRight");
      const expectedSidebarWidth = index === 0 ? "336" : "320";
      if (index === 0) await page.waitForTimeout(5_500); // Covers the retired health poll.
      await page.reload({ waitUntil: "domcontentloaded" });
      await page.getByRole("heading", { name: "设备列表" }).waitFor();
      assert.equal(await sidebarResizer.getAttribute("aria-valuenow"), expectedSidebarWidth,
        "The preferred sidebar width must survive a page reload");
      assert.equal(await page.locator(".topbar .client-device-label").count(), 0);
      if (index === 5) {
        const header = await page.locator(".topbar").boundingBox();
        const workspace = await page.locator(".workspace").boundingBox();
        assert.ok(header && workspace && header.y + header.height <= workspace.y + 1,
          "Narrow-screen toolbar must not overlap the workspace");
        await page.getByRole("button", { name: "设置", exact: true }).click();
        const dialog = page.getByRole("dialog", { name: "网络设置" });
        await dialog.getByRole("alert").filter({ hasText: "unavailable" }).waitFor();
        await dialog.getByRole("button", { name: "关闭", exact: true }).click();
        await dialog.waitFor({ state: "hidden" });
      }
      await page.getByRole("button", { name: "收起设备列表", exact: true }).click();
      await page.locator(".left-panel.collapsed").waitFor();
      assert.equal(await sidebarToggle.getAttribute("aria-expanded"), "false");
      await page.getByRole("button", { name: "运维管理员", exact: true }).click();
      await page.waitForURL("**/login");
      await page.getByText(`接入设备：${example.label}`, { exact: true }).waitFor();
      await page.getByLabel("密码", { exact: true }).fill("correct-password");
      await page.getByRole("button", { name: "登录", exact: true }).click();
      await page.waitForURL("**/console");
      assert.equal(await sidebarToggle.getAttribute("aria-expanded"), "true",
        "Collapsing then logging out must not hide the sidebar on the next login");
      await page.getByRole("heading", { name: "设备列表", exact: true }).waitFor();
      assert.equal(await sidebarResizer.getAttribute("aria-valuenow"), expectedSidebarWidth,
        "A new login must preserve the preferred width, not the collapsed state");
      await page.getByRole("button", { name: "收起设备列表", exact: true }).click();
      await page.locator(".left-panel.collapsed").waitFor();
      await page.getByRole("button", { name: "展开设备列表", exact: true }).click();
      await page.getByRole("heading", { name: "设备列表", exact: true }).waitFor();
      await page.getByRole("button", { name: "运维管理员", exact: true }).click();
      await page.waitForURL("**/login");
      assert.deepEqual(guardianRequests, [], `${example.label}: Guardian was contacted`);
      assert.equal(loginPayloads.length, 3);
      for (const payload of loginPayloads) {
        assert.deepEqual(Object.keys(payload).sort(), ["client_time_ms", "password", "username"]);
        assert.ok(Number.isInteger(payload.client_time_ms));
        assert.ok(Math.abs(payload.client_time_ms - Date.now()) < 60_000);
      }
      if (process.env.WEBUI_DEVICE_SCREENSHOT && index === 0) {
        await page.screenshot({ path: process.env.WEBUI_DEVICE_SCREENSHOT, fullPage: true });
      }
      console.log(`Client flow passed: ${example.label} (${index + 1}/${deviceCases.length})`);
    } finally {
      await context.close();
    }
  }
}
