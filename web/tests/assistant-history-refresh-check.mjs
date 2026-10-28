import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const url = "http://127.0.0.1:5207";
const server = spawn(process.execPath, [fileURLToPath(new URL("../node_modules/vite/bin/vite.js", import.meta.url)),
  "--host", "127.0.0.1", "--port", "5207", "--strictPort"],
{ cwd: fileURLToPath(new URL("..", import.meta.url)), stdio: "ignore" });
let browser;
try {
  for (let i = 0; ; i++) {
    try { if ((await fetch(url)).ok) break; } catch { /* startup */ }
    assert.ok(i < 80); await delay(100);
  }
  const executablePath = [process.env.CHROME_PATH, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/chromium", "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"].find((p) => p && existsSync(p));
  browser = await chromium.launch({ headless: true, executablePath });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  context.setDefaultTimeout(10000);
  await context.addInitScript(() => {
    if (!sessionStorage.getItem("auditbox_auth")) sessionStorage.setItem("auditbox_auth",
      JSON.stringify({ username: "history-test", role: "admin", token: "mock-token" }));
  });
  const page = await context.newPage();
  const errors = [], requests = [], details = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const device = { id: "saved-device", address: "/dev/ttyUSB0", port: 9600, protocol: "console",
    device_type: "switch", device_profile: "h3c_comware", remark: "历史恢复测试", status: "online" };
  const publicSession = { session_id: "public", device_id: null, title: "公共会话", created_at: 1789430000, runs: [] };
  const sessions = Array.from({ length: 55 }, (_, i) => ({ session_id: `old-${String(i).padStart(3, "0")}`,
    title: `历史记录${i}`, device_id: device.id, created_at: 1789430000 + i, updated_at: 1789430000 + i,
    runs: [{ run_id: `run-${i}`, input: { input_content: "旧问题" }, content: `旧回复${i}` }] }));
  let latest, connections = 0, clockCalls = 0;
  await page.route("**/api/**", async (route) => {
    const req = route.request(), u = new URL(req.url()), p = u.pathname;
    const json = (value) => route.fulfill({ contentType: "application/json", body: JSON.stringify(value) });
    if (p === "/api/system/clock/sync") { clockCalls++; return json({ synchronized: false }); }
    if (p === "/api/devices") return json({ items: [device], total: 1 });
    if (p === "/api/devices/status") return json([{ id: device.id, status: "online" }]);
    if (p === "/api/recent-connections") return json([]);
    if (p === "/api/assistant/status") return json({ ready: true });
    if (p === "/api/assistant/capabilities") return json({ data: [] });
    if (p === "/api/sessions") return json({ ...device, device_id: device.id,
      session_id: `terminal-${++connections}`, websocket_url: `ws://127.0.0.1:5207/ws/${connections}` });
    if (p === "/api/assistant/conversations") {
      if (req.method() === "POST") {
        assert.equal(req.postDataJSON().device_id, device.id);
        latest = { session_id: "new-with-wrong-clock", title: "新对话", device_id: device.id,
          created_at: 1780670000, updated_at: 1780670000, runs: [] };
        sessions.push(latest); return json(latest);
      }
      const scope = u.searchParams.get("device_id"), number = Number(u.searchParams.get("page") || 1);
      requests.push({ scope, page: number });
      const sorted = (scope ? sessions : [publicSession]).toSorted((a, b) => b.updated_at - a.updated_at);
      const start = (number - 1) * 50;
      return json({ data: sorted.slice(start, start + 50).map(({ runs: _runs, ...s }) => s),
        has_more: sorted.length > start + 50, page: number, limit: 50 });
    }
    if (p.endsWith("/files")) return json({ data: [] });
    if (p.endsWith("/messages")) {
      assert.equal(req.postDataJSON().target.device_id, device.id);
      latest.runs = [{ run_id: "new-reply", input: { input_content: "刷新后还在吗" }, content: "回复已保存，可以刷新验证。" }];
      const events = [{ event: "RunStarted", run_id: "new-reply" },
        { event: "RunContent", content: "回复已保存，可以刷新验证。" }, { event: "RunCompleted", run_id: "new-reply" }];
      return route.fulfill({ contentType: "text/event-stream", body: events.map((e) => `event: ${e.event}\ndata: ${JSON.stringify(e)}\n\n`).join("") });
    }
    if (p.endsWith("/title")) { latest.title = "刷新后还在吗"; return json({ title: latest.title }); }
    if (p.startsWith("/api/assistant/conversations/")) {
      const id = p.split("/").at(-1); details.push(id);
      return json(id === "public" ? publicSession : sessions.find((s) => s.session_id === id));
    }
    return json({});
  });
  await page.routeWebSocket("**/ws/**", (ws) => {
    ws.onMessage(() => {});
    ws.send(JSON.stringify({ type: "handshake", status: "ok", session_id: `terminal-${connections}` }));
    ws.send("Switch# ");
  });
  async function connect() {
    await page.getByRole("button", { name: "连接 历史恢复测试", exact: true }).click();
    await page.locator(".session-status-dot.connected").waitFor();
    await page.getByRole("tab", { name: "运维助手", exact: true }).click();
  }
  await page.goto(`${url}/console`);
  await connect();
  await page.getByText("旧回复54", { exact: true }).waitFor();
  await page.getByRole("button", { name: "新会话", exact: true }).click();
  await page.getByLabel("向运维助手提问").fill("刷新后还在吗");
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  await page.getByText("回复已保存，可以刷新验证。", { exact: true }).waitFor();
  await page.reload();
  await connect();
  await page.getByText("回复已保存，可以刷新验证。", { exact: true }).waitFor();
  assert.ok(details.filter((id) => id === latest.session_id).length >= 2);
  await page.getByRole("button", { name: "历史会话", exact: true }).click();
  assert.equal(await page.getByRole("menuitem").count(), 51, "First page plus remembered older session");
  await page.getByRole("button", { name: "加载更早会话", exact: true }).click();
  await page.waitForFunction(() => document.querySelectorAll('.assistant-history-menu [role="menuitem"]').length === 56);
  assert.equal(await page.getByRole("button", { name: "加载更早会话" }).count(), 0);
  assert.match(await page.getByRole("menuitem").first().innerText(), /历史记录54/);
  assert.match(await page.getByRole("menuitem").last().innerText(), /刷新后还在吗/);
  await page.getByRole("menuitem", { name: /历史记录0\s/ }).click();
  await page.getByText("旧回复0", { exact: true }).waitFor();
  await page.getByRole("button", { name: "历史会话", exact: true }).click();
  assert.match(await page.getByRole("menuitem").first().innerText(), /历史记录54/, "Viewing must not touch activity time");
  assert.equal(await page.getByRole("menuitem", { name: /公共会话/ }).count(), 0);
  assert.ok(requests.some((r) => r.scope === device.id && r.page === 2));
  assert.ok(clockCalls >= 2, "A reload must calibrate before protected API calls");
  assert.deepEqual(errors, []);
  console.log("History refresh passed: completed reply, refresh/reconnect, scope isolation, restore beyond page 1, all 56 rows, descending order, read-only selection, clock sync");
} finally {
  if (browser) await browser.close();
  server.kill("SIGTERM");
}
