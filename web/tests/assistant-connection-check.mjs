import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const url = process.env.WEBUI_TEST_URL || "http://127.0.0.1:5204";
const server = process.env.WEBUI_TEST_URL ? null : spawn(process.execPath, [
  fileURLToPath(new URL("../node_modules/vite/bin/vite.js", import.meta.url)),
  "--host", "127.0.0.1", "--port", "5204", "--strictPort",
], { cwd: fileURLToPath(new URL("..", import.meta.url)), stdio: "ignore" });
const executablePath = [process.env.CHROME_PATH, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", "/usr/bin/chromium",
].filter(Boolean).find(existsSync);
const scopeError = "设备连接成功后才能访问该设备的助手会话";
let browser;

try {
  for (let attempt = 0; ; attempt++) {
    try { if ((await fetch(url)).ok) break; } catch { /* Wait for Vite. */ }
    assert.ok(attempt < 80, "Frontend did not start");
    await delay(100);
  }
  browser = await chromium.launch({ headless: true, executablePath });
  for (const [protocol, port, mode] of [
    ["console", 9600, "save"], ["console", 115200, "save"],
    ["ssh", 22, "save"], ["telnet", 23, "save"], ["console", 9600, "direct"],
    ["console", 9600, "quick"], ["console", 115200, "quick-saved"],
    ["ssh", 22, "quick"], ["telnet", 23, "quick"], ["console", 9600, "quick-hidden"],
    ["console", 9600, "direct-existing"], ["console", 9600, "direct-save-later"],
  ]) {
    const isQuick = mode.startsWith("quick");
    const hasSavedAtStart = isQuick || mode === "direct-existing";
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    context.setDefaultTimeout(10_000);
    await context.addInitScript(() => sessionStorage.setItem("auditbox_auth",
      JSON.stringify({ username: "connection-test", role: "admin", token: "mock" })));
    const page = await context.newPage();
    const pageErrors = [];
    page.on("pageerror", error => pageErrors.push(error.message));
    const devices = [];
    const tickets = new Map();
    const active = new Map();
    const conversationCreates = [];
    const messages = [];
    const connectionRequests = [];
    const deniedScopes = [];
    let failNextConnection = mode === "save";
    let currentSocket;
    const conversations = [{ session_id: "public-history", device_id: null,
      title: "公共会话", created_at: 1789430000, updated_at: 1789430000, runs: [] }];
    const createDevice = payload => {
      const { target_password: _password, ...fields } = payload;
      const device = { ...fields, id: "saved-device", status: "online" };
      devices.push(device);
      conversations.push({ session_id: "device-history", device_id: device.id,
        title: "设备历史会话", created_at: 1789430000, updated_at: 1789430000,
        runs: [{ run_id: "past", input: "历史问题", content: "设备历史内容" }] });
      return device;
    };
    const savedFields = {
      protocol, port, address: protocol === "console" ? "/dev/ttyUSB0" : "192.0.2.1",
      device_type: protocol === "ssh" ? "server" : "switch",
      device_profile: protocol === "ssh" ? "linux" : "h3c_comware",
      remark: "会话绑定测试", username: "saved-user", notes: "已保存设备备注",
    };
    if (hasSavedAtStart) createDevice(savedFields);
    const recent = [{ ...savedFields, id: "old-direct-record", device_id: null,
      device_type: "server", device_profile: "linux", remark: "旧临时连接",
      username: "stale-user", last_opened_at: "2026-09-16T08:00:00Z" }];
    if (mode === "quick-saved") Object.assign(recent[0], savedFields, { device_id: devices[0].id });
    const connected = id => !id || [...active.values()].some(session => session.device_id === id);
    // All device, Agent and WebSocket calls are simulated, even against a production UI URL.
    await page.route("**/api/**", route => {
      const request = route.request();
      const parsed = new URL(request.url());
      const path = parsed.pathname;
      const method = request.method();
      const json = (body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
      const checkScope = id => {
        if (connected(id)) return null;
        deniedScopes.push(id);
        return json({ detail: scopeError }, 409);
      };
      if (path === "/api/devices") {
        if (method === "POST") return json(createDevice(request.postDataJSON()), 201);
        const search = parsed.searchParams.get("search") || "";
        const items = devices.filter(device => device.remark.includes(search));
        return json({ items, total: items.length, page: 1, page_size: 50 });
      }
      if (path === "/api/devices/status") return json(devices.map(device => ({ id: device.id, status: "online" })));
      // Deliberately return stale/null-ID recents to exercise server resolution
      // even if a card was loaded before the device was saved or edited.
      if (path === "/api/recent-connections") return json(isQuick ? recent : []);
      if (path === "/api/console/ports") return json([{ path: "/dev/ttyUSB0", label: "测试 Console 线" }]);
      if (path === "/api/connections" || path === "/api/sessions") {
        const payload = request.postDataJSON();
        if (failNextConnection) {
          failNextConnection = false;
          return json({ detail: "模拟连接失败，请重试" }, protocol === "console" ? 409 : 401);
        }
        const saved = path === "/api/connections" ? createDevice(payload)
          : devices.find(device => payload.device_id ? device.id === payload.device_id
            : device.protocol === payload.protocol && device.address === payload.address && device.port === payload.port);
        const token = `ticket-${tickets.size + 1}`;
        const session = { ...payload, ...saved, id: saved?.id || "", device_id: saved?.id || null,
          session_id: String(tickets.size + 1).padStart(12, "0"),
          websocket_url: `${url.replace(/^http/, "ws")}/ws/connections/${token}` };
        tickets.set(token, session);
        connectionRequests.push({ path, payload, session });
        return json(session, 201);
      }
      if (path === "/api/assistant/status") return json({ ready: true, capabilities: {} });
      if (path === "/api/assistant/capabilities" || path.endsWith("/files")) return json({ data: [] });
      if (path === "/api/assistant/conversations") {
        const payload = method === "POST" ? request.postDataJSON() : null;
        const id = payload?.device_id || parsed.searchParams.get("device_id") || null;
        const denied = checkScope(id);
        if (denied) return denied;
        if (payload) {
          conversationCreates.push(payload);
          const conversation = { session_id: `created-${conversations.length}`, device_id: id,
            title: payload.title, created_at: 1789430100, updated_at: 1789430100, runs: [] };
          conversations.push(conversation);
          return json(conversation, 201);
        }
        return json({ data: conversations.filter(conversation => conversation.device_id === id) });
      }
      if (path.endsWith("/messages")) {
        const payload = request.postDataJSON();
        const denied = checkScope(payload.target?.device_id);
        if (denied) return denied;
        messages.push(payload);
        const events = [{ event: "RunStarted", run_id: "mock-run" },
          { event: "RunContent", content: "助手连接验证成功" }, { event: "RunCompleted", run_id: "mock-run" }];
        return route.fulfill({ contentType: "text/event-stream",
          body: events.map(event => `event: ${event.event}\ndata: ${JSON.stringify(event)}\n\n`).join("") });
      }
      if (path.endsWith("/title")) return json({ title: "连接验证" });
      if (path.startsWith("/api/assistant/conversations/")) {
        return json(conversations.find(conversation => conversation.session_id === path.split("/").at(-1)) || {});
      }
      return json({});
    });
    await page.routeWebSocket("**/ws/**", socket => {
      currentSocket = socket;
      const session = tickets.get(new URL(socket.url()).pathname.split("/").at(-1));
      assert.ok(session, "Unexpected unbound WebSocket");
      active.set(session.session_id, session);
      socket.onClose(() => active.delete(session.session_id));
      socket.onMessage(() => {});
      socket.send(JSON.stringify({ type: "handshake", status: "ok", session_id: session.session_id }));
      socket.send("\r\nSwitch# ");
    });
    const enterCredentials = async () => {
      if (protocol === "console") return;
      const dialog = page.getByRole("dialog", { name: "连接目标", exact: true });
      await dialog.getByLabel("登录密码").fill("mock-only");
      await dialog.getByRole("button", { name: "连接", exact: true }).click();
    };
    try {
      await page.goto(`${url}/console`);
      if (isQuick) {
        if (mode === "quick-hidden") {
          await page.locator(".left-panel").getByRole("button", { name: "连接 会话绑定测试", exact: true }).waitFor();
          await page.getByPlaceholder("搜索设备", { exact: true }).fill("不匹配任何已保存设备");
          await page.getByText("暂无已管理设备", { exact: true }).waitFor();
        }
        await page.getByRole("button", { name: /^快速连接 / }).click();
        await enterCredentials();
      } else {
        await page.locator(".left-panel").getByRole("button", { name: "新建安全连接" }).click();
        const dialog = page.getByRole("dialog", { name: "新建安全连接" });
        await dialog.getByLabel("设备大类").selectOption(protocol === "ssh" ? "server" : "switch");
        await dialog.getByLabel("设备子类型").selectOption(protocol === "ssh" ? "linux" : "h3c_comware");
        await dialog.getByLabel("设备名称").fill("会话绑定测试");
        if (protocol === "console") {
          await dialog.getByRole("radio", { name: "USB Console 接入" }).click();
          await dialog.getByLabel("USB-A Console 设备").selectOption("/dev/ttyUSB0");
          await dialog.getByLabel("波特率").fill(String(port));
        } else {
          await dialog.getByRole("radio", { name: protocol === "ssh" ? "SSH" : "Telnet", exact: true }).click();
          await dialog.getByLabel("地址", { exact: true }).fill("192.0.2.1");
          await dialog.getByLabel("用户名", { exact: true }).fill("test");
          await dialog.getByLabel("登录密码").fill("mock-only");
        }
        const connect = dialog.getByRole("button", { name: mode === "save" ? "保存并连接" : "连接", exact: true });
        if (mode === "save") {
          await connect.click();
          await dialog.getByText("模拟连接失败，请重试", { exact: true }).waitFor();
          assert.equal(devices.length, 0, "Failed verification must not save a device");
          assert.equal(tickets.size, 0);
        }
        await connect.click();
      }
      await page.locator(".session-status-dot.connected").waitFor();
      const expectedId = mode === "save" || hasSavedAtStart ? devices[0]?.id : null;
      assert.equal([...active.values()][0]?.device_id, expectedId,
        "Browser-saved device must match the device ID registered by the terminal handshake");
      assert.equal(connectionRequests.length, 1, "Do not open a second unbound session while saving");
      assert.equal(connectionRequests[0].path, mode === "save" ? "/api/connections" : "/api/sessions");
      assert.equal(devices.length, expectedId ? 1 : 0);
      if (isQuick) assert.equal(connectionRequests[0].payload.device_id || null,
        mode === "quick-saved" ? expectedId : null);
      await page.getByRole("tab", { name: "运维助手", exact: true }).click();
      await page.getByText("当前设备：会话绑定测试", { exact: true }).waitFor();
      if (expectedId) await page.getByText("设备历史内容", { exact: true }).waitFor();
      assert.equal(await page.getByText(scopeError, { exact: true }).count(), 0);
      await page.getByRole("button", { name: "新会话", exact: true }).click();
      await page.getByLabel("向运维助手提问").fill("检查当前连接");
      await page.getByRole("button", { name: "发送消息", exact: true }).click();
      await page.getByText("助手连接验证成功", { exact: true }).waitFor();
      assert.equal(conversationCreates.at(-1).device_id, expectedId);
      assert.equal(messages.at(-1).target.device_id, expectedId);
      assert.equal(messages.at(-1).target.session_id, [...active.keys()][0]);
      assert.equal(messages.at(-1).target.protocol, protocol);
      assert.equal(messages.at(-1).target.port, port);
      if (hasSavedAtStart) {
        assert.equal(messages.at(-1).target.device_profile, savedFields.device_profile);
      }
      assert.deepEqual(deniedScopes, []);
      if (mode !== "direct") {
        if (mode === "direct-save-later") createDevice(savedFields);
        const savedId = devices[0].id;
        await currentSocket.close({ code: 1011, reason: "模拟 Console 拔线/网络断开" });
        await page.locator(".session-tab.active .session-tab-status", { hasText: "已断开" }).waitFor();
        const oldSessionId = connectionRequests[0].session.session_id;
        active.delete(oldSessionId);
        assert.equal(await page.evaluate(async id => (await fetch(
          `/api/assistant/conversations?device_id=${id}`)).status, savedId), 409,
        "Disconnected devices must still fail the scope gate");
        await page.locator(".session-tab.active .session-tab-refresh").click();
        await page.locator(".session-status-dot.connected").waitFor();
        assert.equal(connectionRequests.at(-1).payload.device_id || null, expectedId);
        if (hasSavedAtStart) assert.equal(connectionRequests.at(-1).payload.username, savedFields.username);
        assert.equal([...active.values()][0].device_id, savedId);
        await page.getByText("当前设备：会话绑定测试", { exact: true }).waitFor();
        await page.getByRole("button", { name: "历史会话", exact: true }).click();
        await page.getByRole("menuitem", { name: /设备历史会话/ }).waitFor();
        await page.getByRole("menuitem", { name: /设备历史会话/ }).click();
        await page.getByText("设备历史内容", { exact: true }).waitFor();
        assert.equal(devices.length, 1, "Reconnect must not create another managed device");
      }
      if (isQuick) {
        await page.locator(".session-tab.active .session-tab-close").click();
        if (mode === "quick-hidden") await page.getByPlaceholder("搜索设备", { exact: true }).fill("");
        await page.locator(".left-panel").getByRole("button", { name: "连接 会话绑定测试", exact: true }).click();
        await enterCredentials();
        await page.locator(".session-status-dot.connected").waitFor();
        assert.equal(connectionRequests.at(-1).payload.device_id, expectedId);
        await page.getByText("当前设备：会话绑定测试", { exact: true }).waitFor();
        await page.getByRole("button", { name: "历史会话", exact: true }).click();
        await page.getByRole("menuitem", { name: /设备历史会话/ }).click();
        await page.getByText("设备历史内容", { exact: true }).waitFor();
        assert.equal(devices.length, 1, "Quick open and sidebar must reuse one saved device");
      }
      assert.deepEqual(pageErrors, []);
      console.log(`Assistant connection passed: ${protocol}/${port}/${mode} (stable scope, history, messages, failure/reconnect isolation)`);
    } finally {
      await context.close();
    }
  }
} finally {
  if (browser) await browser.close();
  if (server) server.kill("SIGTERM");
}
