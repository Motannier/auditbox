import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const url = process.env.WEBUI_TEST_URL || "http://127.0.0.1:5210";
const server = process.env.WEBUI_TEST_URL ? null : spawn(process.execPath, [
  fileURLToPath(new URL("../node_modules/vite/bin/vite.js", import.meta.url)),
  "--host", "127.0.0.1", "--port", "5210", "--strictPort",
], { cwd: fileURLToPath(new URL("..", import.meta.url)), stdio: "ignore" });
const executablePath = [process.env.CHROME_PATH,
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/usr/bin/chromium", "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
].find((path) => path && existsSync(path));
let browser;

try {
  for (let attempt = 0; ; attempt++) {
    try { if ((await fetch(url)).ok) break; } catch { /* Wait for Vite. */ }
    assert.ok(attempt < 80, "Frontend did not start");
    await delay(100);
  }
  browser = await chromium.launch({ headless: true, executablePath });
  for (const scenario of ["qa", "direct", "content", "reasoning", "repeat", "reject", "restore-missing", "error"]) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    context.setDefaultTimeout(8000);
    await context.addInitScript(() => sessionStorage.setItem("auditbox_auth",
      JSON.stringify({ username: "approval-test", role: "admin", token: "mock" })));
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    const device = { id: "scope-device", address: "/dev/ttyUSB0", port: 9600, protocol: "console",
      device_type: "switch", device_profile: "h3c_comware", remark: "审批范围测试", status: "online" };
    const conversation = { session_id: "approval-test", device_id: null, title: "审批测试",
      created_at: 1789430000, runs: [] };
    const deviceConversation = { session_id: "device-test", device_id: device.id, title: "设备会话",
      created_at: 1789430000, runs: [{ run_id: "device-run", input: "设备问题", content: "设备独立回复" }] };
    const run = { run_id: "approval-run", input: { input_content: "请执行 pwd" }, content: "" };
    const tool = { tool_name: "send_terminal_command", tool_call_id: "first-tool", requires_confirmation: true,
      tool_args: { command: "pwd", target_session_id: "mock-terminal" } };
    const secondTool = { ...tool, tool_call_id: "second-tool" };
    const answer = `审批后回复-${scenario}`;
    const approvals = [];
    const switchUpdates = [];
    let executionEnabled = scenario !== "qa";
    // All API/terminal traffic is mocked: no device commands, model calls or clock changes.
    await page.route("**/api/**", (route) => {
      const request = route.request(), parsed = new URL(request.url()), path = parsed.pathname;
      const json = (body) => route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
      const sse = (events) => route.fulfill({ contentType: "text/event-stream",
        body: events.map((event) => `event: ${event.event}\ndata: ${JSON.stringify(event)}\n\n`).join("") });
      if (path === "/api/devices") return json({ items: [device], total: 1 });
      if (path === "/api/devices/status") return json([{ id: device.id, status: "online" }]);
      if (path === "/api/recent-connections") return json([]);
      if (path === "/api/sessions") return json({ ...device, device_id: device.id,
        session_id: "abcdef123456", websocket_url: `${url.replace(/^http/, "ws")}/ws/approval-test` });
      if (path === "/api/assistant/status") return json({ ready: true,
        capabilities: { execution_enabled: executionEnabled } });
      if (path === "/api/assistant/capabilities") return json({ data: [{
        id: "control:agent-execution", kind: "control", name: "Agent 执行", enabled: executionEnabled,
      }] });
      if (path === "/api/assistant/capabilities/control%3Aagent-execution") {
        executionEnabled = request.postDataJSON().enabled;
        switchUpdates.push(executionEnabled);
        return json({ id: "control:agent-execution", kind: "control", enabled: executionEnabled });
      }
      if (path.endsWith("/files")) return json({ data: [] });
      if (path === "/api/assistant/conversations") return json({
        data: [parsed.searchParams.get("device_id") ? deviceConversation : conversation], has_more: false,
      });
      if (path === "/api/assistant/conversations/approval-test") return json(conversation);
      if (path === "/api/assistant/conversations/device-test") return json(deviceConversation);
      if (path.endsWith("/messages")) {
        conversation.runs = [run];
        const events = [{ event: "RunStarted", run_id: run.run_id }];
        if (scenario === "qa") {
          assert.equal(executionEnabled, false);
          run.content = "请手动执行 pwd，输出当前工作目录。";
          return sse([...events, { event: "RunContent", content: run.content },
            { event: "RunCompleted", run_id: run.run_id }]);
        }
        if (scenario === "content") events.push({ event: "RunContent", content: "先查询设备。" });
        if (scenario === "reasoning") events.push({ event: "ReasoningStarted" }, { event: "ReasoningCompleted" });
        events.push({ event: "RunPaused", run_id: run.run_id, tools: [tool] });
        return sse(events);
      }
      if (path.endsWith("/continue")) {
        const payload = request.postDataJSON();
        approvals.push(payload);
        assert.equal(payload.approved, scenario !== "reject");
        assert.equal(payload.tools[0].tool_call_id, approvals.length === 1 ? tool.tool_call_id : secondTool.tool_call_id);
        if (scenario === "repeat" && approvals.length === 1) {
          // Repeated approval can also pause without any renderable content/tool events.
          return sse([{ event: "RunContinued", run_id: run.run_id },
            { event: "RunPaused", run_id: run.run_id, tools: [secondTool] }]);
        }
        if (scenario === "error") return sse([{ event: "RunError", error: "模拟审批执行失败" }]);
        run.content = answer;
        const events = [{ event: "RunContinued", run_id: run.run_id }];
        if (payload.approved) events.push({ event: "ToolCallStarted", tool }, { event: "ToolCallCompleted", tool });
        events.push({ event: "RunContent", content: answer.slice(0, 4) },
          { event: "RunContent", content: answer.slice(4) }, { event: "RunCompleted", run_id: run.run_id });
        return sse(events);
      }
      return json({});
    });
    await page.routeWebSocket("**/ws/**", (socket) => {
      socket.onMessage(() => {});
      socket.send(JSON.stringify({ type: "handshake", status: "ok", session_id: "abcdef123456" }));
      socket.send("Switch# ");
    });

    try {
      await page.goto(`${url}/console`);
      await page.getByRole("tab", { name: "运维助手", exact: true }).click();
      if (scenario === "qa") {
        await page.getByRole("button", { name: /^插件/ }).click();
        const toggle = page.getByRole("switch", { name: "Agent 执行", exact: true });
        await toggle.waitFor();
        assert.equal(await toggle.isChecked(), false);
        await toggle.click();
        await page.getByText("本地模型已就绪 · Agent 执行已开启", { exact: true }).waitFor();
        assert.equal(await toggle.isChecked(), true);
        await toggle.click();
        await page.getByText("本地模型已就绪 · 模型问答", { exact: true }).waitFor();
        assert.equal(await toggle.isChecked(), false);
        assert.deepEqual(switchUpdates, [true, false]);
        if (process.env.WEBUI_TEST_QA_SCREENSHOT) {
          await page.screenshot({ path: process.env.WEBUI_TEST_QA_SCREENSHOT, fullPage: true });
        }
        await page.reload();
        await page.getByRole("tab", { name: "运维助手", exact: true }).click();
        await page.getByText("本地模型已就绪 · 模型问答", { exact: true }).waitFor();
      }
      await page.getByLabel("向运维助手提问").fill("请执行 pwd");
      await page.getByRole("button", { name: "发送消息", exact: true }).click();
      if (scenario === "qa") {
        await page.getByText(run.content, { exact: true }).waitFor();
        assert.equal(await page.locator(".assistant-approval-card").count(), 0);
        assert.equal(await page.locator(".assistant-run-step.tool").count(), 0);
        assert.deepEqual(approvals, []);
        assert.deepEqual(errors, []);
        console.log("Assistant Q&A default and execution switch passed");
        continue;
      }
      await page.getByRole("button", { name: "确认执行", exact: true }).waitFor();
      await page.getByRole("button", { name: "停止生成", exact: true }).waitFor({ state: "detached" });

      if (scenario === "restore-missing") {
        // Reload this scope's history while paused. Persisted runs use different
        // message IDs (or no reply yet), but approval still holds the live ID.
        await page.getByRole("button", { name: "连接 审批范围测试", exact: true }).click();
        await page.getByText("设备独立回复", { exact: true }).waitFor();
        assert.equal(await page.locator(".assistant-approval-card").count(), 0);
        await page.locator(".session-tab.active .session-tab-close").click();
        await page.waitForFunction(() => document.querySelectorAll(".assistant-message.assistant").length === 0);
        await page.getByRole("button", { name: "确认执行", exact: true }).waitFor();
      }

      const rounds = scenario === "repeat" ? 2 : 1;
      for (let round = 0; round < rounds; round++) {
        const responseReady = page.waitForResponse((response) => new URL(response.url()).pathname.endsWith("/continue"));
        await page.getByRole("button", { name: scenario === "reject" ? "拒绝执行" : "确认执行", exact: true }).click();
        await (await responseReady).finished();
        await page.getByRole("button", { name: "停止生成", exact: true }).waitFor({ state: "detached" });
        if (round < rounds - 1) {
          await page.getByRole("button", { name: "确认执行", exact: true }).waitFor();
          assert.equal(await page.locator(".assistant-message.assistant").count(), 2, "Keep reply across a second empty pause");
        }
      }
      assert.equal(approvals.length, rounds);
      if (scenario === "error") {
        await page.getByText("模拟审批执行失败", { exact: true }).waitFor();
        assert.equal(await page.locator(".assistant-message.assistant").count(), 1, "Remove empty failed reply, keep welcome");
      } else {
        // A refresh must not be necessary, and deltas must not be duplicated.
        await page.getByText(answer, { exact: true }).waitFor();
        assert.equal(await page.locator(".assistant-messages").getByText(answer, { exact: true }).count(), 1);
        if (scenario === "content") await page.getByText("先查询设备。", { exact: true }).waitFor();
        if (scenario === "reasoning") await page.getByText("分析完成", { exact: true }).waitFor();
        if (scenario === "reject") assert.equal(await page.locator(".assistant-run-step.tool").count(), 0);
        await page.reload();
        await page.getByRole("tab", { name: "运维助手", exact: true }).click();
        await page.getByText(answer, { exact: true }).waitFor();
      }
      assert.deepEqual(errors, []);
      console.log(`Assistant approval passed: ${scenario}`);
    } finally {
      await context.close();
    }
  }
} finally {
  if (browser) await browser.close();
  server?.kill("SIGTERM");
}
