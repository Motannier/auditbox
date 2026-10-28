import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const webRoot = fileURLToPath(new URL("..", import.meta.url));
const url = process.env.WEBUI_TEST_URL || "http://127.0.0.1:5198";
const server = process.env.WEBUI_TEST_URL ? null : spawn(process.execPath, [
  fileURLToPath(new URL("../node_modules/vite/bin/vite.js", import.meta.url)),
  "--host", "127.0.0.1", "--port", "5198", "--strictPort",
], { cwd: webRoot, stdio: "ignore" });
const executablePath = [
  process.env.CHROME_PATH,
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "/usr/bin/google-chrome", "/usr/bin/chromium",
].filter(Boolean).find(existsSync);
const longName = "network_diagnostics_".repeat(18);
const markdown = [
  "### 排查结果",
  "```sh\nshow interfaces " + "GigabitEthernet1/0/48 ".repeat(30) + "\n```",
  "路径：`/var/log/" + "long_directory/".repeat(35) + "`",
  "https://example.invalid/" + "network-segment".repeat(35),
  "| 接口 | 状态 | 速率 | 描述 | 收包 | 发包 | 错包 | 丢包 |\n|---|---|---|---|---|---|---|---|\n" +
    "| GigabitEthernet1/0/48 | UP | 1000Mbps | uplink | 123456789 | 987654321 | 0 | 0 |",
  "![拓扑示意](/assistant-layout-wide.svg)",
].join("\n\n");
let browser;

async function assertContained(page, label) {
  const layout = await page.locator(".assistant-panel-view").evaluate((panel) => {
    const bounds = panel.getBoundingClientRect();
    const selectors = [
      ".assistant-header", ".assistant-messages", ".assistant-message",
      ".assistant-markdown", ".assistant-run-parts", ".assistant-run-content",
      ".assistant-run-step", ".assistant-composer", ".assistant-input-row",
      ".assistant-toolbar", ".assistant-approval-card",
      ".assistant-markdown pre", ".assistant-markdown-table", ".assistant-markdown img",
      "[data-assistant-menu-surface]",
    ];
    return {
      width: bounds.width,
      overflow: [...panel.querySelectorAll(selectors.join(","))].flatMap((el) => {
        const rect = el.getBoundingClientRect();
        if (!rect.width || !rect.height) return [];
        const localScroller = el.matches("pre, .assistant-markdown-table");
        return rect.left < bounds.left - 1 || rect.right > bounds.right + 1 ||
          (!localScroller && el.scrollWidth > el.clientWidth + 1)
          ? [{ element: el.className || el.tagName, width: rect.width,
            clientWidth: el.clientWidth, scrollWidth: el.scrollWidth, right: rect.right - bounds.right }]
          : [];
      }),
    };
  });
  assert.deepEqual(layout.overflow, [], `${label}: ${JSON.stringify(layout)}`);
}

try {
  for (let attempt = 0; ; attempt += 1) {
    try { if ((await fetch(url)).ok) break; } catch { /* Wait for Vite. */ }
    assert.ok(attempt < 80, "Frontend did not start");
    await delay(100);
  }
  browser = await chromium.launch({ headless: true, executablePath });
  for (const [viewportWidth, panelWidth] of [[1280, 300], [1280, 340], [1920, 640], [780, 340], [390, 300]]) {
    const context = await browser.newContext({ viewport: { width: viewportWidth, height: 1000 } });
    await context.addInitScript((width) => {
      sessionStorage.setItem("auditbox_auth", JSON.stringify({
        username: "layout-test", role: "admin", token: "mock-token",
      }));
      localStorage.setItem("auditbox-operations-panel-width", String(width));
    }, panelWidth);
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const conversation = {
      session_id: "layout-test", title: longName, created_at: 1_788_200_000,
      runs: [{ run_id: "history", input: { input_content: longName }, content: markdown }],
    };
    await page.route("**/assistant-layout-wide.svg", (route) => route.fulfill({
      contentType: "image/svg+xml",
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="100"><rect width="1600" height="100" fill="#d2e8e6"/></svg>',
    }));
    // No real device/model calls, including when testing the board's production assets.
    await page.route("**/api/**", (route) => {
      const path = new URL(route.request().url()).pathname;
      const json = (data) => route.fulfill({ contentType: "application/json", body: JSON.stringify(data) });
      if (path === "/api/devices") return json({ items: [], total: 0, page: 1, page_size: 100 });
      if (path === "/api/devices/status" || path === "/api/recent-connections") return json([]);
      if (path === "/api/assistant/status") return json({ ready: true, capabilities: {} });
      if (path === "/api/assistant/capabilities") return json({ data: [], summary: {} });
      if (path === "/api/assistant/conversations") return json({ data: [conversation] });
      if (path.endsWith("/files")) return json({ data: [] });
      if (path === "/api/assistant/conversations/layout-test") return json(conversation);
      if (path.endsWith("/messages")) {
        const paused = route.request().postDataJSON().message === "approval";
        const events = [
          { event: "RunStarted", run_id: "stream" },
          { event: "CapabilityWarning", id: "warning", message: longName },
          { event: "ToolCallCompleted", tool_display_name: longName,
            tool: { tool_name: longName, tool_call_id: "tool" } },
          { event: "RunContent", content: markdown },
          paused ? { event: "RunPaused", run_id: "stream", tools: [{
            tool_name: longName, tool_call_id: "approval", requires_confirmation: true,
            tool_args: { path: "/var/log/" + longName },
          }] } : { event: "RunCompleted", run_id: "stream" },
        ];
        return route.fulfill({ contentType: "text/event-stream",
          body: events.map((e) => `event: ${e.event}\ndata: ${JSON.stringify(e)}\n\n`).join("") });
      }
      return json({});
    });
    try {
      await page.goto(new URL("/console", url).href);
      await page.getByRole("tab", { name: "运维助手", exact: true }).click();
      await page.locator(".assistant-markdown pre").waitFor();
      await page.locator(".assistant-markdown img").evaluate((img) => img.decode());
      if (process.env.WEBUI_TEST_SCREENSHOT && viewportWidth === 1280 && panelWidth === 300) {
        await page.locator(".assistant-messages").evaluate((el) => { el.scrollTop = 0; });
        await page.locator(".operations-panel").screenshot({ path: process.env.WEBUI_TEST_SCREENSHOT });
      }
      await assertContained(page, `history ${viewportWidth}/${panelWidth}`);
      for (const selector of [".assistant-markdown pre", ".assistant-markdown-table"]) {
        const scrolled = await page.locator(selector).first().evaluate((el) => {
          el.scrollLeft = el.scrollWidth;
          return el.scrollLeft > 0 && getComputedStyle(el).overflowX === "auto";
        });
        if (panelWidth <= 340 && viewportWidth > 1120) assert.ok(scrolled, `${selector} must scroll locally`);
      }
      for (const prompt of ["stream", "approval"]) {
        await page.getByLabel("向运维助手提问").fill(prompt);
        await page.getByRole("button", { name: "发送消息", exact: true }).click();
        await page.locator(".assistant-message.streaming").waitFor({ state: "detached" });
        await assertContained(page, `${prompt} ${viewportWidth}/${panelWidth}`);
      }
      await page.getByRole("button", { name: "历史会话", exact: true }).click();
      await assertContained(page, `history menu ${viewportWidth}/${panelWidth}`);
      await page.keyboard.press("Escape");
      await page.getByRole("button", { name: /文件 · 0/ }).click();
      await assertContained(page, `file menu ${viewportWidth}/${panelWidth}`);
      assert.deepEqual(errors, []);
      console.log(`Assistant layout passed: viewport=${viewportWidth}, panel=${panelWidth}`);
    } finally {
      await context.close();
    }
  }
} finally {
  if (browser) await browser.close();
  if (server) server.kill();
}
