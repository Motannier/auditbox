import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";
import { skillTreeForDeviceProfile, guideScenarios, buildOfflineGuide, createInitialGuideForm } from "../src/lib/guide/index.js";

const profiles = [["huawei_vrp", "华为交换机", "huawei", "display version", "GigabitEthernet 0/0/1"],
  ["cisco_ios", "Cisco 交换机", "cisco", "show version", "GigabitEthernet1/0/1"],
  ["zte_zxr10", "中兴交换机", "zte", "show version", "gei-1/1/1"]];

for (const [profile, , vendor, , portName] of profiles) {
  const tree = skillTreeForDeviceProfile(profile);
  assert.equal(tree.length, 1);
  assert.deepEqual(tree[0].branches.map((branch) => branch.id), [`switch-${vendor}`]);
  assert.equal(tree[0].branches[0].groups.length, 5);
  for (const group of tree[0].branches[0].groups) {
    assert.ok(!group.referenceProfile);
    const operations = guideScenarios.filter((scenario) => scenario.categoryId === group.id);
    assert.ok(operations.length >= 3, `${group.id} should have common operations`);
    for (const scenario of operations) {
      const result = buildOfflineGuide(scenario.id, {});
      assert.equal(result.commands.length > 0, scenario.fields.length === 0);
      for (const command of result.commands) assert.match(command, /^(display|show) /);
    }
  }
  const portScenario = `switch-${vendor}-interface-detail`;
  assert.deepEqual(createInitialGuideForm(portScenario, { status: "connected", host: "192.0.2.1" }), { interfaceName: "" });
  assert.equal(buildOfflineGuide(portScenario, { interfaceName: portName }).commands.length, 1);
  for (const input of ["", "brief", "eth0;reboot", "eth0\nshutdown", "eth0 | delete", "eth0\rreload", "eth0 && x"]) {
    assert.deepEqual(buildOfflineGuide(portScenario, { interfaceName: input }).commands, []);
  }
  const vlanScenario = `switch-${vendor}-vlan-detail`;
  for (const vlanId of ["1", "100", "4094"]) assert.equal(buildOfflineGuide(vlanScenario, { vlanId }).commands.length, 1);
  for (const vlanId of ["", "0", "4095", "-1", "1-100", "100\nreload"]) assert.deepEqual(buildOfflineGuide(vlanScenario, { vlanId }).commands, []);
}
assert.equal(guideScenarios.filter((scenario) => scenario.id.startsWith("switch-h3c-")).length, 10);

const url = process.env.WEBUI_TEST_URL || "http://127.0.0.1:5199";
const server = process.env.WEBUI_TEST_URL ? null : spawn(process.execPath, [
  fileURLToPath(new URL("../node_modules/vite/bin/vite.js", import.meta.url)),
  "--host", "127.0.0.1", "--port", "5199", "--strictPort",
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
  for (const [profile, label, vendor, overview, portName] of profiles) {
    const context = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
    await context.addInitScript(() => {
      sessionStorage.setItem("auditbox_auth", JSON.stringify({ username: "test", role: "admin", token: "mock" }));
      localStorage.setItem("auditbox-operations-panel-width", "300");
    });
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    let saved;
    let sessionPayload;
    let referenceFetchCount = 0;
    let completionProfile;
    let terminalSocket;
    const socketMessages = [];
    // Even production-asset tests never connect to real managed devices.
    await page.route("**/api/**", (route) => {
      const request = route.request();
      const path = new URL(request.url()).pathname;
      const json = (data, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(data) });
      if (path === "/api/commands/reference") { referenceFetchCount++; return json({}, 404); }
      if (path === "/api/devices") {
        if (request.method() === "POST") { saved = { ...request.postDataJSON(), id: "new-switch", status: "online" }; return json(saved, 201); }
        return json({ items: saved ? [saved] : [], total: saved ? 1 : 0, page: 1, page_size: 100 });
      }
      if (path === "/api/devices/status") return json(saved ? [{ id: saved.id, status: "online" }] : []);
      if (path === "/api/recent-connections") return json([]);
      if (path === "/api/commands/suggestions") {
        const params = new URL(request.url()).searchParams;
        completionProfile = params.get("device_profile");
        return json(params.get("prefix")?.startsWith("shu") ? [{
          command: "shutdown", source: "catalog", usage_count: 0, risk: "confirm",
          behavior_label: "接口排查", view: "接口配置视图", models: `${vendor} 测试适用型号`,
          variant_count: 2, risk_reason: "关闭接口，可能中断连接",
        }] : []);
      }
      if (path === "/api/sessions" || path === "/api/connections") {
        sessionPayload = request.postDataJSON();
        if (path === "/api/connections") saved = { ...sessionPayload, id: "new-switch", status: "online" };
        return json({ ...sessionPayload, id: "new-switch", device_id: "new-switch", session_id: "mock", status: "online",
          websocket_url: `${url.replace(/^http/, "ws")}/ws/connections/mock` }, 201);
      }
      return json({ required: false, data: [], ready: true, capabilities: {} });
    });
    await page.routeWebSocket("**/ws/connections/**", (socket) => {
      terminalSocket = socket;
      socket.send(JSON.stringify({ type: "handshake", status: "ok", session_id: "mock" }));
      socket.send("\r\nSwitch# ");
      socket.onMessage((message) => { socketMessages.push(String(message)); });
    });
    try {
      await page.goto(`${url}/console`);
      await page.locator(".left-panel").getByRole("button", { name: "新建安全连接" }).click();
      const dialog = page.getByRole("dialog", { name: "新建安全连接" });
      await dialog.getByLabel("设备大类").selectOption("switch");
      for (const [value] of profiles) assert.equal(await dialog.getByLabel("设备子类型").locator(`option[value="${value}"]`).count(), 1);
      await dialog.getByLabel("设备子类型").selectOption(profile);
      await dialog.getByLabel("地址", { exact: true }).fill("192.0.2.1");
      await dialog.getByLabel("设备名称").fill(label);
      await dialog.getByLabel("用户名", { exact: true }).fill("test");
      await dialog.getByLabel("登录密码").fill("test");
      await dialog.getByRole("button", { name: "保存并连接" }).click();
      await page.locator(".session-status-dot.connected").waitFor();
      assert.equal(saved.device_profile, profile);
      assert.equal(sessionPayload.device_profile, profile);
      const sendCriticalReview = (intent) => terminalSocket.send(JSON.stringify({
        type: "operation_review", device_profile: profile, review_source: "rules",
        status: "locked", lock: true, locked: true, risk: "critical",
        lock_policy: { allow_lock: true }, reason: "测试旧版锁定标记不再冻结盒子", intent,
        history: [{ seq: 1, command: "write erase", status: "blocked" }], evidence: [1],
        current: { seq: 1, risk: "critical", reason: "单条命令风险" },
      }));
      sendCriticalReview("严重风险仅展示");
      await page.getByText("严重风险仅展示", { exact: true }).waitFor();
      assert.equal(await page.locator(".terminal-operation-lock, .operation-lock-card").count(), 0);
      assert.equal(await page.getByRole("button", { name: "申请管理员授权" }).count(), 0);
      assert.equal(await page.getByLabel("存在高风险告警").count(), 1);
      await page.getByRole("tab", { name: "指令速查", exact: true }).click();
      const panel = page.locator(".command-panel-view");
      await panel.getByRole("button", { name: /交换机运维/ }).click();
      assert.equal(await panel.locator(".offline-skill-branch-trigger").count(), 1);
      await panel.locator(".offline-skill-branch-trigger").click();
      assert.equal(await panel.locator(".offline-skill-function-trigger").count(), 5);
      assert.equal(await panel.getByText("手册命令目录", { exact: true }).count(), 0);
      for (const name of ["系统状态", "接口检查", "VLAN 与 MAC", "二层拓扑", "三层转发"]) {
        await panel.locator(".offline-skill-function-trigger").filter({ hasText: name }).click();
      }
      await panel.locator(".offline-skill-leaf").filter({ hasText: "设备概览" }).click();
      assert.equal(await panel.locator(".guide-command-item code").first().innerText(), overview);
      const before = socketMessages.length;
      await panel.locator(".guide-run").click();
      await panel.locator(".guide-command-item.filled").waitFor();
      sendCriticalReview("队列保持填充待执行");
      await page.waitForFunction(() => document.querySelector(".operation-context-review span")?.textContent === "队列保持填充待执行");
      assert.equal(await panel.locator(".guide-command-item.filled").count(), 1);
      const sent = socketMessages.slice(before).flatMap((value) => {
        if (!value.startsWith("{")) return [value];
        const input = JSON.parse(value);
        if (input.type !== "terminal_input") return [];
        assert.equal(input.source, "guide", "Guide commands must keep their programmatic source");
        assert.equal(typeof input.data, "string");
        return [input.data];
      });
      assert.ok(sent.some((value) => value.includes(overview)));
      assert.ok(sent.every((value) => !/[\r\n]/.test(value)), "Downstream fill must wait for Enter");
      await panel.getByRole("button", { name: "停止队列" }).click();
      await panel.locator(".offline-skill-leaf").filter({ hasText: "指定端口详情" }).click();
      assert.ok(await panel.locator(".guide-run").isDisabled());
      await panel.getByLabel("接口名称", { exact: true }).fill(`${portName};reload`);
      assert.ok(await panel.locator(".guide-run").isDisabled());
      await panel.getByLabel("接口名称", { exact: true }).fill(portName);
      const expected = `${vendor === "huawei" ? "display interface" : vendor === "cisco" ? "show interfaces" : "show interface"} ${portName}`;
      assert.equal(await panel.locator(".guide-command-item code").innerText(), expected);
      assert.ok(await panel.locator(".guide-run").isEnabled());
      if (process.env.WEBUI_GUIDE_SCREENSHOT && vendor === "zte") await page.locator(".operations-panel").screenshot({ path: process.env.WEBUI_GUIDE_SCREENSHOT });
      const overflow = await panel.evaluate((element) => [...element.querySelectorAll(".guide-form, .guide-command-item, .guide-fields input")]
        .filter((child) => child.clientWidth && child.scrollWidth > child.clientWidth + 1).map((child) => child.className));
      assert.deepEqual(overflow, []);
      await panel.locator(".offline-skill-leaf").filter({ hasText: "指定 VLAN 详情" }).click();
      await panel.getByLabel("VLAN ID", { exact: true }).fill("4095");
      assert.ok(await panel.locator(".guide-run").isDisabled());
      await panel.getByLabel("VLAN ID", { exact: true }).fill("100");
      assert.equal(await panel.locator(".guide-command-item code").innerText(), vendor === "huawei" ? "display vlan 100" : "show vlan id 100");
      assert.equal(referenceFetchCount, 0);
      // Three brands use their own completion API parameter, preserve risk and
      // model hints, and filling a command never sends Enter to the device.
      await page.evaluate(() => document.querySelector(".terminal-session.active .xterm-helper-textarea")?.focus());
      await page.keyboard.press("Control+u");
      await page.keyboard.type("shu");
      const completion = page.getByRole("option", { name: /shutdown/ });
      await completion.waitFor();
      assert.equal(completionProfile, profile);
      assert.match(await completion.innerText(), /需二次确认/);
      assert.match(await completion.getAttribute("title"), /测试适用型号/);
      const beforeCompletion = socketMessages.length;
      await completion.click();
      assert.ok(socketMessages.slice(beforeCompletion).every((value) => !/[\r\n]/.test(value)));
      await page.keyboard.press("Enter");
      const confirmation = page.getByRole("dialog", { name: "高危操作确认" });
      for (const [token, confirm] of [["only-once", true], ["cancel-once", false]]) {
        terminalSocket.send(JSON.stringify({ type: "alert", action: "confirm", command: "shutdown",
          reason: "关闭接口", confirmation_id: token }));
        await confirmation.waitFor();
        sendCriticalReview(`确认中仍可操作-${token}`);
        await page.waitForFunction(intent => document.querySelector(".operation-context-review span")?.textContent === intent, `确认中仍可操作-${token}`);
        assert.equal(await confirmation.count(), 1, "A side review must not dismiss pending confirmation");
        await confirmation.getByRole("button", { name: confirm ? "再次确认执行" : "取消操作", exact: true }).click();
        await confirmation.waitFor({ state: "detached" });
        const controls = socketMessages.filter(value => value.startsWith("{")).map(value => JSON.parse(value));
        assert.equal(controls.filter(value => value.confirmation_id === token).length, 1);
        assert.ok(controls.some(value => value.type === (confirm ? "command_confirmation" : "command_confirmation_cancel") && value.confirmation_id === token));
        assert.ok(controls.every(value => value.type !== "session_unlock_debug"));
      }
      await page.evaluate(() => document.querySelector(".terminal-session.active .xterm-helper-textarea")?.focus());
      await page.keyboard.press("Control+u");
      await page.keyboard.press("Escape");
      terminalSocket.send(JSON.stringify({ type: "recommendation", suggestions: [{
        command: overview, source: "factory", risk: "normal", behavior_label: "设备与资源",
        reason: "查询设备信息", view: "查询视图", score: 1,
      }] }));
      await page.getByRole("option", { name: new RegExp(overview) }).waitFor();
      assert.match(await page.locator(".command-suggestions-header").innerText(), /智能推荐/);
      await page.getByRole("tab", { name: /^智能审查/ }).click();
      const scoped = { type: "operation_review", device_profile: profile, review_source: "rules",
        status: "ready", risk: "critical", review_revision: 2, reviewed_through_seq: 2,
        current: { seq: 2, risk: "low", reason: "当前只读查询" }, evidence: [1],
        history: [{ seq: 1, command: "shutdown", status: "cancelled" }, { seq: 2, command: overview, status: "executed" }],
        findings: [{ seq: 1, risk: "high", status: "cancelled", reason: "关闭接口" },
          { seq: 2, risk: "low", status: "executed", reason: "只读查询" }],
        intent: "历史上下文风险", reason: "保留已取消的危险操作证据" };
      terminalSocket.send(JSON.stringify(scoped));
      await page.waitForFunction(() => document.querySelector(".risk-badge")?.textContent === "低风险");
      assert.equal(await page.getByLabel("存在高风险告警").count(), 0);
      assert.match(await page.locator(".operation-history-item").last().textContent(), /低风险/);
      assert.match(await page.locator(".operation-context-review").textContent(), /严重风险/);
      terminalSocket.send(JSON.stringify({ ...scoped, review_revision: 1, current: { seq: 2, risk: "high" } }));
      terminalSocket.send(JSON.stringify({ ...scoped, status: "queued" }));
      await delay(150);
      assert.equal(await page.locator(".risk-badge").textContent(), "低风险", "Late results or queue messages overwrote a newer verdict");
      terminalSocket.send(JSON.stringify({ ...scoped, review_revision: 3, status: "queued" }));
      await page.waitForFunction(() => document.querySelector(".risk-badge")?.textContent === "待核验");
      terminalSocket.send(JSON.stringify({ ...scoped, review_revision: 3, current: { seq: 2, risk: "high", reason: "新的独立高危结论" } }));
      await page.getByLabel("存在高风险告警").waitFor();
      assert.deepEqual(errors, []);
      console.log(`Switch guide passed: ${profile} (guide, subtype, completion, recommendation, risk/model metadata, fill-only, no locks, one-time confirmation survives critical reviews)`);
    } finally { await context.close(); }
  }
} finally {
  if (browser) await browser.close();
  if (server) server.kill("SIGTERM");
}
