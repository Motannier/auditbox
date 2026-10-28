import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import net from "node:net";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";
import { checkClientDeviceFlow } from "./client-device-ui-check.mjs";
import { DEFAULT_PRODUCT_NAME } from "../src/constants/branding.js";
import { buildOfflineGuide, createInitialGuideForm, guideScenarios } from "../src/lib/guide/index.js";

const emptyGuideTarget = { host: "", port: 22, protocol: "SSH" };
for (const scenario of guideScenarios) {
  const result = buildOfflineGuide(scenario.id, {}, emptyGuideTarget);
  if (!result || !Array.isArray(result.commands)) {
    throw new Error(`Guide ${scenario.id} failed during its empty-form render`);
  }
}

const disconnectedGuideForm = createInitialGuideForm("port-check", {
  host: "192.168.10.1",
  port: 22,
  protocol: "SSH",
  status: "idle",
});
if (disconnectedGuideForm.host !== "" || disconnectedGuideForm.port !== "") {
  throw new Error(`Disconnected guide form was prefilled: ${JSON.stringify(disconnectedGuideForm)}`);
}
const connectedGuideForm = createInitialGuideForm("port-check", {
  host: "192.168.10.1",
  port: 2222,
  protocol: "SSH",
  status: "connected",
});
if (connectedGuideForm.host !== "192.168.10.1" || connectedGuideForm.port !== "2222") {
  throw new Error(`Connected guide form did not use its active target: ${JSON.stringify(connectedGuideForm)}`);
}
const connectedScenarioOnlyForm = createInitialGuideForm("delete-path", {
  host: "192.168.10.1",
  port: 22,
  protocol: "SSH",
  status: "connected",
});
if (connectedScenarioOnlyForm.path !== "") {
  throw new Error(`Guide form used an example as a real parameter: ${JSON.stringify(connectedScenarioOnlyForm)}`);
}

const port = Number(process.env.WEBUI_TEST_PORT || 5197);
const url = `http://127.0.0.1:${port}/`;

function chromeExecutable() {
  const candidates = [
    process.env.CHROME_PATH,
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "C:\\\\Program Files\\\\Google\\\\Chrome\\\\Application\\\\chrome.exe",
    "C:\\\\Program Files (x86)\\\\Google\\\\Chrome\\\\Application\\\\chrome.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
  ].filter(Boolean);
  return candidates.find((candidate) => existsSync(candidate));
}

async function waitForPort(portNumber, timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const ok = await new Promise((resolve) => {
      const socket = net.createConnection({ host: "127.0.0.1", port: portNumber });
      socket.once("connect", () => {
        socket.destroy();
        resolve(true);
      });
      socket.once("error", () => resolve(false));
    });
    if (ok) return;
    await delay(150);
  }
  throw new Error(`Timed out waiting for ${url}`);
}

async function checkSuggestionHighlight(page, expectedIndex) {
  await page.waitForFunction((expected) => {
    const options = [...document.querySelectorAll('.command-suggestions [role="option"]')];
    return options.length && options.every((option, index) =>
      (option.getAttribute("aria-selected") === "true") === (index === expected));
  }, expectedIndex, { timeout: 2_000 });
  const states = await page.locator('.command-suggestions [role="option"]').evaluateAll(options =>
    options.map(option => ({
      selected: option.getAttribute("aria-selected") === "true",
      active: option.classList.contains("active"),
      background: getComputedStyle(option).backgroundColor,
      shadow: getComputedStyle(option).boxShadow,
    })),
  );
  if (states.some((state, index) => {
    const highlighted = state.background !== "rgba(0, 0, 0, 0)" || state.shadow !== "none";
    return highlighted !== (index === expectedIndex) || state.active !== state.selected;
  })) {
    throw new Error(`Mouse hover and keyboard selection disagree: ${JSON.stringify({ expectedIndex, states })}`);
  }
}

async function checkSuggestionPointerNavigation(page) {
  const options = page.locator('.command-suggestions [role="option"]');
  const count = await options.count();
  if (count < 3) throw new Error("Pointer navigation regression requires at least three candidates");
  const list = page.locator(".command-suggestions-list");
  const previousMaxHeight = await list.evaluate(element => {
    const previous = element.style.maxHeight;
    element.style.maxHeight = "84px";
    element.scrollTop = 0;
    return previous;
  });
  await page.mouse.move(0, 0);
  const bounds = await options.first().boundingBox();
  if (!bounds) throw new Error("Missing first suggestion bounds");
  const point = { x: Math.round(bounds.x + 12), y: Math.round(bounds.y + bounds.height / 2) };
  await page.mouse.move(point.x, point.y);
  await checkSuggestionHighlight(page, 0);
  await page.evaluate(() => document.querySelector(".terminal-session.active .xterm-helper-textarea")?.focus());
  await page.keyboard.press("ArrowDown");
  await checkSuggestionHighlight(page, 1);
  // Layout/scroll events at unchanged coordinates must not take over selection.
  await page.evaluate(({ x, y }) => document.elementFromPoint(x, y)?.dispatchEvent(new MouseEvent("mousemove", {
    bubbles: true, clientX: x, clientY: y, movementX: 0, movementY: 0,
  })), point);
  await checkSuggestionHighlight(page, 1);
  await page.keyboard.press("ArrowUp");
  await page.keyboard.press("ArrowUp");
  await checkSuggestionHighlight(page, count - 1);
  await page.waitForFunction(() => document.querySelector(".command-suggestions-list")?.scrollTop > 0,
    null, { timeout: 2_000 });
  await list.evaluate(element => { element.scrollTop = 0; });
  await page.waitForTimeout(50);
  await checkSuggestionHighlight(page, count - 1);
  // Real movement within the same hovered row must regain control, without re-entering it.
  await page.mouse.move(point.x + 1, point.y);
  await checkSuggestionHighlight(page, 0);
  await page.keyboard.press("ArrowUp");
  await checkSuggestionHighlight(page, count - 1);
  await list.evaluate((element, maxHeight) => { element.style.maxHeight = maxHeight; }, previousMaxHeight);
}

const webRoot = fileURLToPath(new URL("..", import.meta.url));
const viteCli = fileURLToPath(new URL("../node_modules/vite/bin/vite.js", import.meta.url));
const server = spawn(process.execPath, [viteCli, "--host", "127.0.0.1", "--port", String(port)], {
  cwd: webRoot,
  stdio: ["ignore", "pipe", "pipe"],
  shell: false,
});

let browser;
try {
  await waitForPort(port);
  browser = await chromium.launch({
    headless: true,
    executablePath: chromeExecutable(),
  });
  const page = await browser.newPage({ viewport: { width: 1280, height: 820 } });
  const pageErrors = [];
  const consoleErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error.stack || error.message));
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  let devices = [
    {
      id: "device-1",
      address: "192.168.10.1",
      port: 22,
      protocol: "ssh",
      device_type: "switch",
      device_profile: "h3c_comware",
      remark: "核心交换机",
      username: "admin",
      status: "probing",
      last_connected_at: "2026-07-14T07:30:00Z",
    },
  ];
  let sessionSequence = 0;
  const routedSockets = [];
  let routedSocketCloseCount = 0;
  let confirmedDangerExecutionCount = 0;
  let cancelledDangerCount = 0;
  let dangerConfirmationSequence = 0;
  const pendingDangerCommands = new Map();
  const submittedCommands = [];
  const programmaticInputs = [];
  let completionRequests = 0;
  let holdNextCompletion = false;
  let releaseCompletion = null;
  let lastExecutedCommand = "";
  let lastSuggestionDeviceType = "";
  let lastSuggestionDeviceProfile = "";
  const guideAuditEvents = [];
  let lastAssistantPayload = null;
  let lastMcpPayload = null;
  let lastDeletedCapability = null;
  let assistantCapabilities = [];
  let assistantConversationSequence = 1;
  const assistantConversationQueries = [];
  let assistantConversations = [{
    session_id: "assistant-1",
    title: "新对话",
    // Reproduce Agno's persisted post-hook input shape. The UI must prefer the
    // public display snapshot instead of losing this user's interrupted turn.
    runs: [{
      run_id: "interrupted-run",
      input: { input_content: [{ role: "user", content: "internal message shape" }] },
      content: "",
    }],
    metadata: {
      public_run_snapshots: {
        "interrupted-run": {
          run_id: "interrupted-run",
          input: "中断问题",
          content: "保留下来的半截回复",
          status: "interrupted",
          timeline: [{ kind: "content", content: "保留下来的半截回复" }],
        },
        "continued-run": {
          run_id: "continued-run",
          content: "同一轮审批后的续写",
          status: "completed",
          created_at: 1_788_200_001,
          timeline: [{ kind: "content", content: "同一轮审批后的续写" }],
        },
      },
    },
    created_at: 1_788_200_000,
    updated_at: 1_788_200_000,
  }];
  const assistantFiles = new Map([["assistant-1", []]]);
  let assistantFileSequence = 0;
  let lastExportAuthorization = "";
  const factoryEthernet = {
    interface: "enP4p65s0", mode: "auto", ip_address: "169.254.200.6", netmask: "255.255.255.0",
    addresses: ["169.254.200.6/24"], link_up: true, management_networks: ["192.168.77.0/24"],
    default_address: "169.254.200.6/24", reset_on_reboot: true,
  };
  let ethernetSettings = { ...factoryEthernet };
  let ethernetApplyCount = 0;

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const requestUrl = new URL(request.url());
    const path = requestUrl.pathname;
    const method = request.method();
    const json = (body, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

    if (path === "/api/settings/ethernet") {
      if (!request.headers().authorization) return json({ detail: "请先登录" }, 401);
      if (method === "GET") return json(ethernetSettings);
      if (method === "PUT") {
        ethernetApplyCount += 1;
        const payload = request.postDataJSON();
        if (payload.ip_address?.startsWith("192.168.77.")) {
          return json({ detail: "有线网口网段不能与 USB 管理网段重叠" }, 409);
        }
        ethernetSettings = payload.mode === "auto" ? { ...factoryEthernet } : {
          ...ethernetSettings, ...payload, addresses: [`${payload.ip_address}/24`],
        };
        return json(ethernetSettings);
      }
    }

    if (path === "/api/security/preflight" && method === "GET") {
      return json({ required: false, product: "智维卫士", platform: "windows", minimum_guardian_version: "0.2.1" });
    }
    if (path === "/api/system/clock/sync" && method === "POST") {
      return json({ synchronized: false, server_time_ms: Date.now() });
    }
    if (path === "/api/auth/login" && method === "POST") {
      const payload = request.postDataJSON();
      if (payload.username === "opsadmin" && payload.password === "admin") {
        return json({ username: "opsadmin", role: "admin", token: "token-admin" });
      }
      if (payload.username === "superadmin" && payload.password === "superadmin") {
        return json({ username: "superadmin", role: "super_admin", token: "token-super" });
      }
      return json({ detail: "用户名或密码错误" }, 401);
    }
    if (path === "/api/audit/export" && method === "GET") {
      lastExportAuthorization = request.headers().authorization || "";
      if (lastExportAuthorization !== "Bearer token-super") {
        return json({ detail: "仅超级管理员可以导出操作日志" }, 403);
      }
      return route.fulfill({
        status: 200,
        contentType: "text/csv; charset=utf-8",
        headers: {
          "Content-Disposition": 'attachment; filename="auditbox-operation-logs-test.csv"',
        },
        body: "\ufeff账号,时间,事件\r\nsuperadmin,2026-07-14T12:00:00Z,command\r\n",
      });
    }
    if (path === "/api/devices/status" && method === "GET") {
      return json(devices.map((device) => ({ id: device.id, status: "online", checked_at: new Date().toISOString() })));
    }
    if (path === "/api/devices" && method === "GET") {
      return json({ items: devices, total: devices.length, page: 1, page_size: 50 });
    }
    if (path === "/api/recent-connections" && method === "GET") {
      return json(devices
        .filter((device) => device.last_connected_at)
        .slice(0, 4)
        .map((device, index) => ({
          id: `recent-${index + 1}`,
          device_id: device.id,
          address: device.address,
          port: device.port,
          protocol: device.protocol,
          device_type: device.device_type,
          device_profile: device.device_profile,
          remark: device.remark,
          username: device.username,
          last_opened_at: device.last_connected_at,
          last_operation_at: device.last_connected_at,
          operation_result: "未发现风险",
          operation_risk: false,
        })));
    }
    if (path === "/api/devices" && method === "POST") {
      const payload = request.postDataJSON();
      const created = {
        id: `device-${devices.length + 1}`,
        ...payload,
        address: payload.address,
        status: "probing",
        last_connected_at: null,
      };
      devices = [...devices, created];
      return json(created, 201);
    }
    if (path.startsWith("/api/devices/") && method === "PATCH") {
      const deviceId = decodeURIComponent(path.split("/").at(-1));
      const payload = request.postDataJSON();
      devices = devices.map((device) =>
        device.id === deviceId ? { ...device, ...payload, address: payload.address } : device,
      );
      return json(devices.find((device) => device.id === deviceId));
    }
    if (path === "/api/sessions" && method === "POST") {
      const payload = request.postDataJSON();
      if (!["server", "switch"].includes(payload.device_type)) {
        return json({ detail: "设备类型必须是服务器或交换机" }, 422);
      }
      if (
        (payload.device_type === "server" && payload.device_profile !== "linux") ||
        (payload.device_type === "switch" && payload.device_profile !== "h3c_comware")
      ) {
        return json({ detail: "设备子类型与设备大类不匹配" }, 422);
      }
      if (payload.protocol === "ssh" && payload.target_password === "wrong-password") {
        return json({ detail: "SSH 用户名或密码错误，请重新输入" }, 401);
      }
      const saved = devices.find((device) => device.id === payload.device_id);
      const source = saved || payload;
      sessionSequence += 1;
      return json(
        {
          id: saved?.id || `direct-${sessionSequence}`,
          device_id: saved?.id || null,
          session_id: `mock-${sessionSequence}`,
          address: saved?.address || payload.address,
          port: saved?.port || payload.port,
          protocol: saved?.protocol || payload.protocol,
          device_type: saved?.device_type || payload.device_type,
          device_profile: saved?.device_profile || payload.device_profile,
          remark: saved?.remark || payload.remark || "",
          username: saved?.username || payload.username || null,
          status: "online",
          last_connected_at: new Date().toISOString(),
          websocket_url: `ws://127.0.0.1:${port}/ws/connections/mock-${sessionSequence}`,
        },
        201,
      );
    }
    if (path === "/api/assistant/status" && method === "GET") {
      return json({
        ready: true,
        capabilities: {
          skills: 0,
          toolkits: 0,
          mcp_servers: assistantCapabilities.filter((item) => item.kind === "mcp" && item.enabled).length,
        },
      });
    }
    if (path === "/api/assistant/capabilities" && method === "GET") {
      return json({ data: assistantCapabilities, summary: { skills: 0, mcp_servers: 0, toolkits: 0 } });
    }
    if (path === "/api/assistant/capabilities/mcp" && method === "POST") {
      lastMcpPayload = request.postDataJSON();
      const created = {
        id: "mcp:mock-operations",
        kind: "mcp",
        name: lastMcpPayload.name,
        enabled: lastMcpPayload.enabled,
        source: "user",
        config: {
          url: lastMcpPayload.url,
          transport: lastMcpPayload.transport,
          tools: lastMcpPayload.tools,
          headers: {},
          discovered_tools: ["search_docs", "fetch_page"],
        },
        discovered_tools: ["search_docs", "fetch_page"],
      };
      assistantCapabilities = [...assistantCapabilities, created];
      return json(created, 201);
    }
    if (path === "/api/assistant/capabilities/mcp/probe" && method === "POST") {
      return json({ status: "available", tools: ["search_docs", "fetch_page"] });
    }
    const savedMcpProbeMatch = path.match(/^\/api\/assistant\/capabilities\/(mcp%3A[^/]+|mcp:[^/]+)\/probe$/);
    if (savedMcpProbeMatch && method === "POST") {
      const capabilityId = decodeURIComponent(savedMcpProbeMatch[1]);
      const capability = assistantCapabilities.find((item) => item.id === capabilityId);
      if (!capability) return json({ detail: "MCP 连接不存在" }, 404);
      return json({ ...capability, runtime_status: "available", discovered_tools: ["search_docs", "fetch_page"] });
    }
    if (path.startsWith("/api/assistant/capabilities/") && method === "DELETE") {
      lastDeletedCapability = decodeURIComponent(path.slice("/api/assistant/capabilities/".length));
      assistantCapabilities = assistantCapabilities.filter((item) => item.id !== lastDeletedCapability);
      return json({ id: lastDeletedCapability, deleted: true });
    }
    if (path === "/api/assistant/conversations" && method === "GET") {
      const deviceId = requestUrl.searchParams.get("device_id");
      assistantConversationQueries.push(deviceId);
      return json({
        data: assistantConversations
          .filter((item) => (item.device_id || null) === deviceId)
          .map(({ runs: _runs, ...item }) => item),
      });
    }
    if (path === "/api/assistant/conversations" && method === "POST") {
      const payload = request.postDataJSON();
      assistantConversationSequence += 1;
      const conversation = {
        session_id: `assistant-${assistantConversationSequence}`,
        title: "新对话",
        device_id: payload.device_id || null,
        runs: [],
        created_at: 1_788_200_000 + assistantConversationSequence,
        updated_at: 1_788_200_000 + assistantConversationSequence,
      };
      assistantConversations.unshift(conversation);
      assistantFiles.set(conversation.session_id, []);
      return json(conversation, 201);
    }
    const assistantFilesMatch = path.match(/^\/api\/assistant\/conversations\/([^/]+)\/files$/);
    if (assistantFilesMatch && method === "GET") {
      return json({ data: assistantFiles.get(assistantFilesMatch[1]) || [] });
    }
    if (assistantFilesMatch && method === "POST") {
      assistantFileSequence += 1;
      const file = {
        id: `file-${assistantFileSequence}`,
        name: "网络排障手册.txt",
        suffix: ".txt",
        content_type: "text/plain",
        size: 50,
        sha256: "mock-sha256",
        character_count: 18,
        outline: [],
        created_at: 1_788_200_100 + assistantFileSequence,
      };
      const files = assistantFiles.get(assistantFilesMatch[1]) || [];
      assistantFiles.set(assistantFilesMatch[1], [...files, file]);
      return json(file, 201);
    }
    const assistantFileMatch = path.match(/^\/api\/assistant\/conversations\/([^/]+)\/files\/([^/]+)$/);
    if (assistantFileMatch && method === "DELETE") {
      const files = assistantFiles.get(assistantFileMatch[1]) || [];
      assistantFiles.set(
        assistantFileMatch[1],
        files.filter((file) => file.id !== assistantFileMatch[2]),
      );
      return json({ deleted: true, file_id: assistantFileMatch[2] });
    }
    const assistantConversationMatch = path.match(/^\/api\/assistant\/conversations\/([^/]+)$/);
    if (assistantConversationMatch && method === "DELETE") {
      const conversationId = assistantConversationMatch[1];
      assistantConversations = assistantConversations.filter((item) => item.session_id !== conversationId);
      assistantFiles.delete(conversationId);
      return route.fulfill({ status: 204, body: "" });
    }
    if (assistantConversationMatch && method === "GET") {
      return json(assistantConversations.find((item) => item.session_id === assistantConversationMatch[1]));
    }
    const assistantTitleMatch = path.match(/^\/api\/assistant\/conversations\/([^/]+)\/title$/);
    if (assistantTitleMatch && method === "POST") {
      const conversation = assistantConversations.find((item) => item.session_id === assistantTitleMatch[1]);
      conversation.title = "如何检查网络";
      return json({ conversation_id: conversation.session_id, title: conversation.title });
    }
    const assistantMessageMatch = path.match(/^\/api\/assistant\/conversations\/([^/]+)\/messages$/);
    if (assistantMessageMatch && method === "POST") {
      const payload = request.postDataJSON();
      lastAssistantPayload = payload;
      if (payload.message === "慢任务") await delay(700);
      if ("target_password" in (payload.target || {}) || "password" in (payload.target || {})) {
        return json({ detail: "The assistant received a target password" }, 400);
      }
      const conversation = assistantConversations.find((item) => item.session_id === assistantMessageMatch[1]);
      conversation.runs.push({
        run_id: `run-${conversation.runs.length + 1}`,
        input: { input_content: payload.message },
        content: "建议先执行 ip addr 和 ip route 检查网络状态。",
      });
      const started = JSON.stringify({ event: "RunStarted", run_id: `run-${conversation.runs.length}` });
      const reasoningStarted = JSON.stringify({ event: "ReasoningStarted", run_id: `run-${conversation.runs.length}` });
      const reasoningCompleted = JSON.stringify({ event: "ReasoningCompleted", run_id: `run-${conversation.runs.length}` });
      const toolStarted = JSON.stringify({ event: "ToolCallStarted", run_id: `run-${conversation.runs.length}`, tool_display_name: "网络状态检查", tool: { tool_call_id: "tool-1", tool_name: "network_check" } });
      const toolCompleted = JSON.stringify({ event: "ToolCallCompleted", run_id: `run-${conversation.runs.length}`, tool_display_name: "网络状态检查", tool: { tool_call_id: "tool-1", tool_name: "network_check" } });
      const chunk = JSON.stringify({
        event: "RunContent",
        run_id: `run-${conversation.runs.length}`,
        content: "建议先执行 ip addr 和 ip route 检查网络状态。",
      });
      return route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: `event: RunStarted\ndata: ${started}\n\nevent: ReasoningStarted\ndata: ${reasoningStarted}\n\nevent: ReasoningCompleted\ndata: ${reasoningCompleted}\n\nevent: ToolCallStarted\ndata: ${toolStarted}\n\nevent: ToolCallCompleted\ndata: ${toolCompleted}\n\nevent: RunContent\ndata: ${chunk}\n\n`,
      });
    }
    if (path === "/api/commands/suggestions") {
      completionRequests += 1;
      if (holdNextCompletion) {
        holdNextCompletion = false;
        await new Promise(resolve => { releaseCompletion = resolve; });
      }
      const prefix = requestUrl.searchParams.get("prefix") || "";
      lastSuggestionDeviceType = requestUrl.searchParams.get("device_type") || "";
      lastSuggestionDeviceProfile = requestUrl.searchParams.get("device_profile") || "";
      if ("system-view".startsWith(prefix) && prefix !== "system-view") {
        return json([{
          command: "system-view",
          usage_count: 0,
          last_used_at: null,
          source: "catalog",
          risk: "normal",
          behavior: "system.access",
          behavior_label: "系统访问与命令行",
          view: "用户视图",
        }]);
      }
      const displayCompletions = ["display version", "display device", "display cpu-usage"]
        .filter(command => command.startsWith(prefix) && command !== prefix);
      if (displayCompletions.length) {
        return json(displayCompletions.map(command => ({
          command,
          usage_count: 3,
          last_used_at: "2026-07-14T08:00:00Z",
          source: "catalog",
          risk: "normal",
        })));
      }
      if ("rm -rf *".startsWith(prefix) && prefix !== "rm -rf *") {
        return json([{
          command: "rm -rf *",
          source: "catalog",
          risk: "confirm",
        }]);
      }
      return json([]);
    }
    return json({ detail: `Unhandled mock API: ${method} ${path}` }, 404);
  });

  await page.routeWebSocket("**/ws/connections/**", (ws) => {
    routedSockets.push(ws);
    let currentCommand = "";
    ws.onClose(() => {
      routedSocketCloseCount += 1;
    });
    ws.send(JSON.stringify({ type: "handshake", status: "ok", session_id: "mock-proxy" }));
    ws.onMessage((message) => {
      let text = String(message);
      if (text.startsWith('{"type":"terminal_input"')) {
        const input = JSON.parse(text);
        if (input.source !== "guide") throw new Error("Unexpected programmatic input source");
        programmaticInputs.push(input);
        text = input.data;
      }
      if (text.startsWith('{"type":"guide_audit_event"')) {
        guideAuditEvents.push(JSON.parse(text));
        return;
      }
      if (text.startsWith('{"type":"command_confirmation')) {
        const control = JSON.parse(text);
        const command = pendingDangerCommands.get(control.confirmation_id);
        if (control.type === "command_confirmation" && command) {
          confirmedDangerExecutionCount += 1;
          lastExecutedCommand = command;
          pendingDangerCommands.delete(control.confirmation_id);
          ws.send(`\r\n[${DEFAULT_PRODUCT_NAME}] 已确认，正在执行高危命令\r\n# `);
        } else if (control.type === "command_confirmation_cancel" && command) {
          cancelledDangerCount += 1;
          pendingDangerCommands.delete(control.confirmation_id);
          ws.send(`\r\n[${DEFAULT_PRODUCT_NAME}] 已取消高危命令\r\n# `);
        }
        return;
      }
      ws.send(text);
      for (const character of text) {
        if (character === "\x7f" || character === "\b") {
          currentCommand = currentCommand.slice(0, -1);
          continue;
        }
        if (character === "\x15") {
          currentCommand = "";
          continue;
        }
        if (character === "\r" || character === "\n") {
          submittedCommands.push(currentCommand);
          if (currentCommand === "sudo reboot" || currentCommand === "rm -rf *") {
            dangerConfirmationSequence += 1;
            const confirmationId = `confirm-danger-${dangerConfirmationSequence}`;
            pendingDangerCommands.set(confirmationId, currentCommand);
            ws.send(JSON.stringify({
              type: "alert",
              level: "danger",
              action: "confirm",
              command: currentCommand,
              reason: currentCommand === "sudo reboot"
                ? "设备重启或关机"
                : "递归强制删除文件或目录",
              blocked: false,
              confirmation_id: confirmationId,
            }));
          } else if (currentCommand.includes("display version")) {
            lastExecutedCommand = currentCommand;
            ws.send("\r\nH3C Comware Software, Version 7.1\r\n<H3C> ");
            ws.send(JSON.stringify({
              type: "recommendation",
              suggestions: [
                { command: "display device", source: "factory", risk: "normal", behavior_label: "设备状态与资源", reason: "查看设备与板卡状态" },
                { command: "display cpu-usage", source: "factory", risk: "normal", behavior_label: "设备状态与资源", reason: "检查 CPU 使用率" },
                { command: "display memory", source: "factory", risk: "normal", behavior_label: "设备状态与资源", reason: "检查内存使用情况" },
                { command: "display environment", source: "factory", risk: "normal", behavior_label: "设备状态与资源", reason: "检查温度、电源和风扇环境" },
                { command: "display interface brief", source: "factory", risk: "normal", behavior_label: "设备状态与资源", reason: "查看接口摘要" },
              ],
            }));
          } else {
            lastExecutedCommand = currentCommand;
            ws.send(`\r\n${currentCommand}\r\n# `);
          }
          currentCommand = "";
        } else {
          currentCommand += character;
        }
      }
    });
  });
  await page.goto(url, { waitUntil: "networkidle" });
  await page.getByLabel("用户名").fill("opsadmin");
  await page.getByLabel("密码").fill("bad");
  await page.getByRole("button", { name: "登录" }).click();
  await page.getByText("用户名或密码错误").waitFor({ timeout: 5_000 });
  if ((await page.locator(".xterm").count()) !== 0) {
    throw new Error("Login accepted a wrong password");
  }
  await page.getByLabel("用户名").fill("superadmin");
  await page.getByLabel("密码").fill("superadmin");
  await page.getByRole("button", { name: "登录" }).click();

  await page.getByRole("heading", { name: "设备列表" }).waitFor({ timeout: 5_000 }).catch(async (error) => {
    const body = (await page.locator("body").innerText()).slice(0, 2_000);
    throw new Error(
      `Console did not load after login at ${page.url()}. Page text: ${body}. Browser errors: ${pageErrors.join(" | ")}. Console errors: ${consoleErrors.join(" | ")}`,
      { cause: error },
    );
  });
  const exportButton = page.getByRole("button", { name: "导出操作日志" });
  const superLogoutButton = page.getByRole("button", { name: "系统管理员" });
  await exportButton.waitFor();
  const exportBox = await exportButton.boundingBox();
  const logoutBox = await superLogoutButton.boundingBox();
  if (!exportBox || !logoutBox || exportBox.x >= logoutBox.x) {
    throw new Error(`The audit export button is not left of logout: ${JSON.stringify({ exportBox, logoutBox })}`);
  }
  const downloadPromise = page.waitForEvent("download");
  await exportButton.click();
  const download = await downloadPromise;
  if (!["auditbox-operation-logs-test.csv", "auditbox-operation-logs.csv"].includes(download.suggestedFilename())) {
    throw new Error(`Unexpected audit export filename: ${download.suggestedFilename()}`);
  }
  if (lastExportAuthorization !== "Bearer token-super") {
    throw new Error(`Audit export omitted the super-admin token: ${lastExportAuthorization}`);
  }
  await superLogoutButton.click();
  await page.getByRole("button", { name: "登录" }).waitFor();
  await page.getByLabel("用户名").fill("opsadmin");
  await page.getByLabel("密码").fill("admin");
  await page.getByRole("button", { name: "登录" }).click();

  await page.getByRole("heading", { name: "设备列表" }).waitFor({ timeout: 5_000 });
  if ((await page.getByRole("button", { name: "导出操作日志" }).count()) !== 0) {
    throw new Error("A regular admin can see the operation-log export action");
  }
  await page.getByRole("button", { name: "设置", exact: true }).click();
  const networkDialog = page.getByRole("dialog", { name: "网络设置" });
  await networkDialog.getByText("重启后默认", { exact: true }).waitFor();
  await networkDialog.getByLabel("配置方式").selectOption("static");
  await networkDialog.getByLabel("盒子网口 IP").fill("192.168.77.2");
  await networkDialog.getByRole("button", { name: "应用配置" }).click();
  await networkDialog.getByRole("alert").filter({ hasText: "USB 管理网段重叠" }).waitFor();
  if (ethernetSettings.mode !== "auto") throw new Error("Invalid IP changed the network settings");
  await networkDialog.getByLabel("盒子网口 IP").fill("192.168.10.2");
  await networkDialog.getByLabel("子网掩码").fill("255.255.255.0");
  await networkDialog.getByRole("button", { name: "应用配置" }).click();
  await networkDialog.getByRole("status").filter({ hasText: "网口 IP 已应用" }).waitFor();
  if (ethernetSettings.ip_address !== "192.168.10.2" || ethernetApplyCount !== 2) {
    throw new Error("Network settings were not submitted correctly");
  }
  if (process.env.WEBUI_NETWORK_SCREENSHOT) {
    await page.screenshot({ path: process.env.WEBUI_NETWORK_SCREENSHOT, fullPage: true });
  }
  await networkDialog.getByRole("button", { name: "关闭", exact: true }).click();
  await page.getByRole("button", { name: "设置", exact: true }).click();
  await page.waitForFunction(() => document.querySelector('.network-settings-dialog input')?.value === "192.168.10.2");
  await networkDialog.getByLabel("配置方式").selectOption("auto");
  await networkDialog.getByRole("button", { name: "应用配置" }).click();
  await networkDialog.getByRole("status").filter({ hasText: "已恢复默认网口配置" }).waitFor();
  if (ethernetSettings.ip_address !== "169.254.200.6") throw new Error("Factory IP was not restored");
  await page.keyboard.press("Escape");
  await networkDialog.waitFor({ state: "hidden" });
  await page.getByRole("heading", { name: "建立安全运维连接" }).waitFor({ timeout: 5_000 });
  await page.getByRole("button", { name: "快速连接 核心交换机" }).waitFor({ timeout: 5_000 });
  await page.locator(".left-panel").getByRole("button", { name: "新建安全连接" }).click();
  const newConnectionDialog = page.getByRole("dialog", { name: "新建安全连接" });
  const operationsResizeDuringDialog = page.getByRole("separator", { name: "调整右侧工具栏宽度" });
  if ((await operationsResizeDuringDialog.evaluate((element) => getComputedStyle(element).pointerEvents)) !== "none") {
    throw new Error("Operations panel resizer remained active behind the connection dialog");
  }
  const deviceTypeSelect = newConnectionDialog.getByLabel("设备大类");
  const deviceProfileSelect = newConnectionDialog.getByLabel("设备子类型");
  if (
    (await deviceTypeSelect.inputValue()) !== "" ||
    (await deviceTypeSelect.locator('option[value="server"]').count()) !== 1 ||
    (await deviceTypeSelect.locator('option[value="switch"]').count()) !== 1 ||
    (await deviceTypeSelect.getAttribute("required")) === null ||
    (await deviceProfileSelect.inputValue()) !== "" ||
    !(await deviceProfileSelect.isDisabled()) ||
    (await deviceProfileSelect.getAttribute("required")) === null
  ) {
    throw new Error("New connections did not require both device classification levels");
  }
  await deviceTypeSelect.selectOption("switch");
  if (
    (await deviceProfileSelect.locator('option[value="h3c_comware"]').count()) !== 1 ||
    (await deviceProfileSelect.isDisabled())
  ) {
    throw new Error("Switch selection did not expose the H3C Comware subtype");
  }
  const newConnectionLayout = await newConnectionDialog.evaluate((dialog) => {
    const dialogRect = dialog.getBoundingClientRect();
    const sections = [...dialog.querySelectorAll(".new-connection-section")].map((section) => {
      const rect = section.getBoundingClientRect();
      return { x: rect.x, y: rect.y, width: rect.width };
    });
    return {
      width: dialogRect.width,
      height: dialogRect.height,
      clientHeight: dialog.clientHeight,
      scrollHeight: dialog.scrollHeight,
      sections,
    };
  });
  if (
    newConnectionLayout.width < 760 ||
    newConnectionLayout.height > 640 ||
    newConnectionLayout.scrollHeight > newConnectionLayout.clientHeight + 1 ||
    newConnectionLayout.sections.length !== 2 ||
    Math.abs(newConnectionLayout.sections[0].y - newConnectionLayout.sections[1].y) > 2 ||
    newConnectionLayout.sections[1].x <=
      newConnectionLayout.sections[0].x + newConnectionLayout.sections[0].width
  ) {
    throw new Error(`New connection dialog did not render as a compact two-column form: ${JSON.stringify(newConnectionLayout)}`);
  }
  await newConnectionDialog.getByRole("button", { name: "取消" }).click();
  const operationsTabs = page.getByRole("tab");
  if ((await operationsTabs.count()) < 2) {
    throw new Error("Expected the right-side tool tabs to be visible");
  }
  const operationsTabState = await page.locator(".operations-tabs [role='tab']").evaluateAll((tabs) =>
    tabs.map((tab) => ({ label: tab.textContent.trim(), selected: tab.getAttribute("aria-selected") })),
  );
  if (
    operationsTabState[0]?.label !== "智能审查" ||
    operationsTabState[0]?.selected !== "true" ||
    operationsTabState[1]?.label !== "运维助手" ||
    operationsTabState[2]?.label !== "指令速查"
  ) {
    throw new Error(`Unexpected default right-side tab state: ${JSON.stringify(operationsTabState)}`);
  }
  if (
    (await page.locator(".review-process-steps").count()) !== 0 ||
    (await page.locator(".operation-review-summary").count()) !== 0
  ) {
    throw new Error("Idle review rendered a fake analysis process before any command was submitted");
  }
  await page.getByRole("tab", { name: "运维助手" }).click();
  await page.getByText("中断问题", { exact: true }).waitFor();
  await page.getByText("保留下来的半截回复", { exact: true }).waitFor();
  await page.getByText("同一轮审批后的续写", { exact: true }).waitFor();
  if ((await page.locator(".assistant-message.assistant").count()) !== 1) {
    throw new Error("One assistant turn was rendered as multiple message bubbles");
  }
  await page.getByText("本地模型已就绪 · 模型问答", { exact: true }).waitFor({ timeout: 5_000 });
  const assistantToolbar = page.getByLabel("助手能力设置");
  if ((await assistantToolbar.getByRole("button").count()) !== 4) {
    throw new Error("The assistant toolbar does not contain exactly four controls");
  }
  for (const label of [/插件 · 0/, /MCP · 0/, /^标准$/, /文件 · 0/]) {
    await assistantToolbar.getByRole("button", { name: label }).waitFor();
  }
  const assistantToolbarWidths = await assistantToolbar.getByRole("button").evaluateAll((buttons) =>
    buttons.map((button) => Math.round(button.getBoundingClientRect().width)),
  );
  if (Math.max(...assistantToolbarWidths) - Math.min(...assistantToolbarWidths) > 1) {
    throw new Error(`Assistant toolbar controls have mismatched widths: ${assistantToolbarWidths}`);
  }
  if (
    (await page.getByText("知识库", { exact: true }).count()) !== 0 ||
    (await page.getByText(/2B|0\.8B|Markdown \/ TXT \/ DOCX/).count()) !== 0
  ) {
    throw new Error("Removed assistant metadata is still visible");
  }
  await assistantToolbar.getByRole("button", { name: /插件 · 0/ }).click();
  const pluginDialog = page.getByRole("dialog", { name: "插件设置" });
  await pluginDialog.getByRole("tab", { name: /工具/ }).waitFor();
  await pluginDialog.getByRole("tab", { name: /Skill/ }).click();
  if ((await pluginDialog.getByRole("tab", { name: /Skill/ }).getAttribute("aria-selected")) !== "true") {
    throw new Error("Plugin dialog did not switch from tools to Skills");
  }
  await assistantToolbar.getByRole("button", { name: /插件 · 0/ }).click();
  await assistantToolbar.getByRole("button", { name: /^标准$/ }).click();
  await page.getByRole("menuitemradio", { name: /标准模式\s*关闭思考/ }).waitFor();
  await page.getByRole("menuitemradio", { name: /增强模式\s*开启思考/ }).click();
  await assistantToolbar.getByRole("button", { name: /^增强$/ }).waitFor();
  await assistantToolbar.getByRole("button", { name: /MCP · 0/ }).click();
  const mcpDialog = page.getByRole("dialog", { name: "MCP 设置" });
  await mcpDialog.waitFor();
  await mcpDialog.getByText("尚未添加 MCP 连接", { exact: true }).waitFor();
  await mcpDialog.getByRole("button", { name: "添加 MCP 连接" }).click();
  await mcpDialog.getByLabel("MCP 名称").waitFor();
  await mcpDialog.getByLabel("MCP 地址").waitFor();
  await mcpDialog.getByLabel("MCP 传输协议").waitFor();
  if ((await mcpDialog.getByLabel("MCP 工具白名单").count()) !== 0) {
    throw new Error("MCP advanced settings were expanded by default");
  }
  await mcpDialog.getByRole("button", { name: "更多设置" }).click();
  await mcpDialog.getByLabel("MCP 工具白名单").waitFor();
  await mcpDialog.getByLabel("MCP 名称").fill("Agno 文档");
  await mcpDialog.getByLabel("MCP 地址").fill("https://docs.agno.com/mcp");
  await mcpDialog.getByRole("button", { name: "测试连接" }).click();
  await mcpDialog.getByText("连接成功，发现 2 个工具", { exact: true }).waitFor();
  await mcpDialog.getByRole("button", { name: "添加并启用" }).click();
  await page.waitForTimeout(300);
  const mcpDialogText = await mcpDialog.innerText();
  if (!mcpDialogText.includes("Agno 文档")) {
    throw new Error(`MCP connection did not return to the list: ${JSON.stringify({ mcpDialogText, lastMcpPayload })}`);
  }
  await mcpDialog.getByText("Agno 文档", { exact: true }).waitFor();
  await mcpDialog.getByText("search_docs", { exact: true }).waitFor();
  await mcpDialog.locator(".assistant-mcp-summary").click();
  await mcpDialog.locator(".assistant-mcp-summary").click();
  await mcpDialog.getByText("fetch_page", { exact: true }).waitFor();
  if ((await page.getByRole("tab", { name: "运维助手" }).getAttribute("aria-selected")) !== "true") {
    throw new Error("Adding an MCP connection switched away from the assistant tab");
  }
  if (
    lastMcpPayload?.name !== "Agno 文档"
    || lastMcpPayload?.url !== "https://docs.agno.com/mcp"
    || lastMcpPayload?.transport !== "streamable-http"
    || lastMcpPayload?.enabled !== true
  ) {
    throw new Error(`MCP connection payload was incorrect: ${JSON.stringify(lastMcpPayload)}`);
  }
  page.once("dialog", (dialog) => dialog.accept());
  await mcpDialog.getByRole("button", { name: "删除 MCP Agno 文档" }).click();
  await mcpDialog.getByText("尚未添加 MCP 连接", { exact: true }).waitFor();
  if (lastDeletedCapability !== "mcp:mock-operations") {
    throw new Error(`MCP delete targeted the wrong capability: ${lastDeletedCapability}`);
  }
  await assistantToolbar.getByRole("button", { name: /MCP · 0/ }).waitFor();
  await page.locator(".assistant-messages").click({ position: { x: 10, y: 10 } });
  if ((await mcpDialog.count()) !== 0) {
    throw new Error("Assistant popup did not close after clicking the message area");
  }
  await assistantToolbar.getByRole("button", { name: /文件 · 0/ }).click();
  await page.getByRole("dialog", { name: "当前对话文件区" }).waitFor();
  await page.getByText("当前对话还没有文件", { exact: true }).waitFor();
  await page.locator(".assistant-file-input").setInputFiles({
    name: "网络排障手册.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("检查链路状态，再检查 IP 地址与路由。", "utf8"),
  });
  await page.getByText("网络排障手册.txt", { exact: true }).waitFor();
  await assistantToolbar.getByRole("button", { name: /文件 · 1/ }).waitFor();
  await page.getByLabel("向运维助手提问").fill("如何检查网络？");
  await page.getByRole("button", { name: "发送消息" }).click();
  await page.getByText("建议先执行 ip addr 和 ip route 检查网络状态。", { exact: true }).waitFor({
    timeout: 5_000,
  });
  await page.getByText("分析完成", { exact: true }).waitFor();
  await page.getByText("调用完成：网络状态检查", { exact: true }).waitFor();
  if (
    lastAssistantPayload?.mode !== "enhanced" ||
    "documents" in lastAssistantPayload
  ) {
    throw new Error(`Assistant mode or workspace payload was incorrect: ${JSON.stringify(lastAssistantPayload)}`);
  }
  await assistantToolbar.getByRole("button", { name: /文件 · 1/ }).click();
  await page.getByRole("button", { name: "删除 网络排障手册.txt" }).click();
  await page.getByText("当前对话还没有文件", { exact: true }).waitFor();
  await assistantToolbar.getByRole("button", { name: /文件 · 0/ }).waitFor();
  await page.getByRole("button", { name: "新会话" }).click();
  await page.getByRole("button", { name: "历史会话" }).click();
  await page.getByRole("menuitem", { name: /如何检查网络/ }).click();
  await page.getByText("建议先执行 ip addr 和 ip route 检查网络状态。", { exact: true }).waitFor();
  await page.getByRole("button", { name: "历史会话" }).click();
  page.once("dialog", (dialog) => dialog.accept());
  const deleteNewConversation = page.getByRole("button", { name: "删除会话 新对话" });
  await deleteNewConversation.click();
  await deleteNewConversation.waitFor({ state: "detached", timeout: 5_000 });
  await page.getByRole("button", { name: "历史会话" }).click();
  if ((await page.getByRole("menuitem", { name: /^新对话/ }).count()) !== 0) {
    throw new Error("Deleted assistant conversation remained in history");
  }
  await page.getByRole("button", { name: "历史会话" }).click();

  await page.getByRole("button", { name: "新会话" }).click();
  await page.getByLabel("向运维助手提问").fill("慢任务");
  await page.getByRole("button", { name: "发送消息" }).click();
  await page.getByText("慢任务", { exact: true }).waitFor();
  await page.getByRole("button", { name: "新会话" }).click();
  await page.getByLabel("向运维助手提问").fill("另一个会话");
  await page.getByRole("button", { name: "发送消息" }).click();
  await page.getByRole("button", { name: "历史会话" }).click();
  await page.getByRole("menuitem", { name: /^慢任务/ }).click();
  await page.getByText("慢任务", { exact: true }).waitFor();
  await page.getByText("建议先执行 ip addr 和 ip route 检查网络状态。", { exact: true }).last().waitFor({ timeout: 5_000 });
  await page.getByRole("tab", { name: "指令速查" }).click();
  if (
    (await page.getByText("像打开浏览器新标签页一样", { exact: false }).count()) !== 0 ||
    (await page.locator(".terminal-empty-primary").count()) !== 1
  ) {
    throw new Error("The empty terminal did not retain exactly one central create button");
  }
  if (
    (await page.getByRole("button", { name: "快速新建安全连接" }).count()) !== 0 ||
    (await page.locator(".session-tabs").count()) !== 0
  ) {
    throw new Error("The empty terminal still renders its tab bar create control");
  }
  const emptyNewConnectionButton = page.locator("button.terminal-empty-primary");
  const initialBookmarkHeight = await page.locator(".bookmark-card").evaluate((element) =>
    Math.round(element.getBoundingClientRect().height),
  );
  if (initialBookmarkHeight > 80) {
    throw new Error(`A short device list stretched its card vertically: ${initialBookmarkHeight}px`);
  }

  const workspace = page.locator(".workspace");
  const initialColumns = await workspace.evaluate((element) => getComputedStyle(element).gridTemplateColumns);
  const workspaceBox = await workspace.boundingBox();
  const expandedToggle = page.getByRole("button", { name: "收起设备列表" });
  const expandedToggleBox = await expandedToggle.boundingBox();
  if (
    !workspaceBox ||
    !expandedToggleBox ||
    Math.abs(expandedToggleBox.x - (workspaceBox.x + Number.parseFloat(initialColumns))) > 2 ||
    Math.abs(expandedToggleBox.y + expandedToggleBox.height / 2 - (workspaceBox.y + workspaceBox.height / 2)) > 2
  ) {
    throw new Error(`Expanded device toggle is not attached to the side boundary: ${JSON.stringify(expandedToggleBox)}`);
  }
  await expandedToggle.click();
  await page.locator(".left-panel.collapsed").waitFor();
  await page.waitForFunction(() =>
    getComputedStyle(document.querySelector(".workspace")).gridTemplateColumns.startsWith("1px"),
  );
  const collapsedColumns = await workspace.evaluate((element) => getComputedStyle(element).gridTemplateColumns);
  const collapsedToggle = page.getByRole("button", { name: "展开设备列表" });
  await collapsedToggle.waitFor();
  if (collapsedColumns === initialColumns || !collapsedColumns.startsWith("1px")) {
    throw new Error(`Device panel did not collapse: before=${initialColumns}; after=${collapsedColumns}`);
  }
  if (await page.locator(".left-panel").getByRole("button", { name: "新建安全连接" }).isVisible()) {
    throw new Error("The collapsed device rail still stacks the add button under the expand control");
  }
  const collapsedToggleBox = await collapsedToggle.boundingBox();
  if (
    !workspaceBox ||
    !collapsedToggleBox ||
    Math.abs(collapsedToggleBox.x - (workspaceBox.x + 1)) > 2 ||
    Math.abs(collapsedToggleBox.y + collapsedToggleBox.height / 2 - (workspaceBox.y + workspaceBox.height / 2)) > 2
  ) {
    throw new Error(
      `Collapsed device toggle is not attached to the side boundary: workspace=${JSON.stringify(workspaceBox)} toggle=${JSON.stringify(collapsedToggleBox)}`,
    );
  }
  await collapsedToggle.click();
  await page.waitForFunction(
    (expectedWidth) =>
      Math.abs(
        Number.parseFloat(getComputedStyle(document.querySelector(".workspace")).gridTemplateColumns) - expectedWidth,
      ) < 1,
    Number.parseFloat(initialColumns),
  );
  const panelResizer = page.getByRole("separator", { name: "调整设备列表宽度" });
  await panelResizer.waitFor();
  const beforeKeyboardResize = Number(await panelResizer.getAttribute("aria-valuenow"));
  await panelResizer.press("ArrowRight");
  const afterKeyboardResize = Number(await panelResizer.getAttribute("aria-valuenow"));
  if (afterKeyboardResize <= beforeKeyboardResize) {
    throw new Error(`Device panel did not resize with the keyboard: ${beforeKeyboardResize} -> ${afterKeyboardResize}`);
  }
  const resizerBox = await panelResizer.boundingBox();
  if (!resizerBox) throw new Error("Device panel resize handle is not visible");
  const resizerX = resizerBox.x + 1;
  const resizerY = resizerBox.y + resizerBox.height * 0.75;
  await page.mouse.move(resizerX, resizerY);
  await page.mouse.down();
  await page.mouse.move(resizerX + 32, resizerY, { steps: 4 });
  await page.mouse.up();
  const afterPointerResize = Number(await panelResizer.getAttribute("aria-valuenow"));
  if (afterPointerResize <= afterKeyboardResize) {
    throw new Error(`Device panel did not resize by dragging: ${afterKeyboardResize} -> ${afterPointerResize}`);
  }
  const operationsResizer = page.getByRole("separator", { name: "调整右侧工具栏宽度" });
  const beforeOperationsResize = Number(await operationsResizer.getAttribute("aria-valuenow"));
  await operationsResizer.press("ArrowLeft");
  const afterOperationsResize = Number(await operationsResizer.getAttribute("aria-valuenow"));
  if (afterOperationsResize <= beforeOperationsResize) {
    throw new Error(`Operations panel did not resize with the keyboard: ${beforeOperationsResize} -> ${afterOperationsResize}`);
  }

  const originalDevice = devices[0];
  devices = [
    originalDevice,
    ...Array.from({ length: 29 }, (_, index) => ({
      ...originalDevice,
      id: `device-long-${index + 1}`,
      address: index === 0 ? originalDevice.address : `192.168.20.${index + 1}`,
      remark: `测试设备 ${String(index + 1).padStart(2, "0")}`,
      last_connected_at: new Date(Date.parse(originalDevice.last_connected_at) - index * 60_000).toISOString(),
    })),
  ];
  const deviceSearch = page.locator(".left-panel .search-box input");
  await deviceSearch.fill("测试长列表");
  await page.waitForFunction(() => document.querySelectorAll(".bookmark-card").length === 30);
  const recentConnections = page.locator(".terminal-empty-device");
  await page.waitForFunction(() => document.querySelectorAll(".terminal-empty-device").length === 1);
  if ((await recentConnections.filter({ hasText: "核心交换机" }).count()) !== 1) {
    throw new Error("Recent connections were not deduplicated by endpoint");
  }
  await emptyNewConnectionButton.click();
  const quickConnectMenu = page.getByRole("menu", { name: "快速打开连接" });
  await quickConnectMenu.getByRole("menuitem", { name: /核心交换机/ }).waitFor();
  const longMenuLayout = await quickConnectMenu.evaluate((menu) => {
    const deviceList = menu.querySelector(".quick-connect-devices");
    const newConnection = menu.querySelector(".quick-connect-new");
    const listRect = deviceList?.getBoundingClientRect();
    const newRect = newConnection?.getBoundingClientRect();
    return {
      menuHeight: Math.round(menu.getBoundingClientRect().height),
      viewportHeight: window.innerHeight,
      listClientHeight: deviceList?.clientHeight || 0,
      listScrollHeight: deviceList?.scrollHeight || 0,
      listOverflowY: deviceList ? getComputedStyle(deviceList).overflowY : "",
      newBelowList: Boolean(listRect && newRect && newRect.top >= listRect.bottom),
    };
  });
  if (
    longMenuLayout.menuHeight >= longMenuLayout.viewportHeight ||
    longMenuLayout.listScrollHeight <= longMenuLayout.listClientHeight ||
    longMenuLayout.listOverflowY !== "auto" ||
    !longMenuLayout.newBelowList
  ) {
    throw new Error(`Quick-connect menu did not constrain its device list: ${JSON.stringify(longMenuLayout)}`);
  }
  await quickConnectMenu.getByLabel("搜索已管理设备").fill("192.168.10.1");
  await quickConnectMenu.getByRole("menuitem", { name: /核心交换机/ }).waitFor();
  await page.keyboard.press("Escape");
  devices = [originalDevice];
  await deviceSearch.fill("");
  await page.waitForFunction(() => document.querySelectorAll(".bookmark-card").length === 1);
  const coreSwitchBookmark = page.getByRole("button", { name: "连接 核心交换机", exact: true });
  await coreSwitchBookmark.waitFor({ timeout: 5_000 });
  const bookmarkCardText = await coreSwitchBookmark.locator("..").innerText();
  if (!bookmarkCardText.includes("192.168.10.1") || bookmarkCardText.includes("端口 22") || bookmarkCardText.includes("SSH")) {
    throw new Error(`Managed device did not use the requested IP and secure-access summary: ${bookmarkCardText}`);
  }
  if ((await page.locator(".xterm").count()) !== 0) {
    throw new Error("Terminal connected before a bookmark password was submitted");
  }
  const initialLinuxRoot = page.getByRole("button", { name: /Linux 运维/ });
  await initialLinuxRoot.click();
  const initialNetworkBranch = page.getByRole("button", { name: /网络检查/ });
  await initialNetworkBranch.click();
  const initialPortGuide = page.getByRole("button", { name: /端口不通/ });
  await initialPortGuide.click();
  const initialRunButton = page.locator(".guide-run").first();
  if (!(await initialRunButton.isDisabled())) {
    throw new Error("Expected 下发 to be disabled before a terminal is connected");
  }
  const disconnectedGuideInputs = page.locator(".offline-skill-leaf-shell.active .guide-fields input");
  const disconnectedGuideFields = await disconnectedGuideInputs.evaluateAll((inputs) =>
    inputs.map((input) => ({ value: input.value, placeholder: input.placeholder })),
  );
  if (
    disconnectedGuideFields.some((field) => field.value !== "") ||
    disconnectedGuideFields[0]?.placeholder !== "请填写目标地址" ||
    disconnectedGuideFields[1]?.placeholder !== "请填写端口"
  ) {
    throw new Error(`Disconnected guide parameters are not empty placeholders: ${JSON.stringify(disconnectedGuideFields)}`);
  }
  await initialPortGuide.click();
  await initialNetworkBranch.click();
  await initialLinuxRoot.click();
  await coreSwitchBookmark.click();
  const targetConnectDialog = page.getByRole("dialog", { name: "连接目标" });
  await targetConnectDialog.waitFor({ timeout: 5_000 });
  await targetConnectDialog.getByLabel("登录密码").fill("wrong-password");
  await targetConnectDialog.getByRole("button", { name: "连接" }).click();
  await targetConnectDialog.getByText("SSH 用户名或密码错误，请重新输入", { exact: true }).waitFor({
    timeout: 5_000,
  });
  if (
    (await page.locator(".session-tab").count()) !== 0 ||
    (await page.locator(".xterm").count()) !== 0 ||
    !(await targetConnectDialog.isVisible())
  ) {
    throw new Error("Wrong SSH password left the password dialog or created a terminal session");
  }
  await targetConnectDialog.getByLabel("登录密码").fill("admin");
  await targetConnectDialog.getByRole("button", { name: "连接" }).click();

  await page.locator(".xterm").waitFor({ timeout: 5_000 });
  await page.getByRole("tab", { name: "运维助手" }).click();
  await page.waitForFunction(() => document.querySelector(".assistant-context")?.textContent.includes("核心交换机"));
  if (!assistantConversationQueries.includes("device-1")) {
    throw new Error(`Assistant history did not request the connected device scope: ${JSON.stringify(assistantConversationQueries)}`);
  }
  await page.getByRole("button", { name: "历史会话" }).click();
  if ((await page.getByRole("menuitem", { name: /如何检查网络/ }).count()) !== 0) {
    throw new Error("Public assistant history remained visible after connecting a device");
  }
  await page.getByRole("button", { name: "历史会话" }).click();
  await page.getByRole("tab", { name: "指令速查" }).click();
  const quickAddButton = page.getByRole("button", { name: "快速新建安全连接" });
  await quickAddButton.waitFor({ timeout: 5_000 });
  if ((await page.locator(".terminal-empty-primary").count()) !== 0) {
    throw new Error("The central create button remained after connection tabs were created");
  }
  await page.evaluate(() => document.querySelector(".terminal-session.active .xterm-helper-textarea")?.focus());
  await page.keyboard.type("dis");
  await page.getByRole("listbox", { name: "命令补全与推荐" }).waitFor({ timeout: 5_000 });
  await page.getByText("H3C Comware 命令补全", { exact: true }).first().waitFor({ timeout: 5_000 });
  if (lastSuggestionDeviceType !== "switch") {
    throw new Error(`Switch command completion omitted its device type: ${lastSuggestionDeviceType}`);
  }
  if (lastSuggestionDeviceProfile !== "h3c_comware") {
    throw new Error(`Switch command completion omitted its profile: ${lastSuggestionDeviceProfile}`);
  }
  if ((await page.locator('[role="option"][aria-selected="true"]').count()) !== 0) {
    throw new Error("Command completions selected an item before keyboard or pointer navigation");
  }
  const submissionsBeforePointerNavigation = submittedCommands.length;
  await checkSuggestionPointerNavigation(page);
  await page.keyboard.press("Enter");
  await page.getByRole("listbox", { name: "命令补全与推荐" }).waitFor({ state: "detached", timeout: 2_000 });
  await page.waitForFunction(() => {
    const cursor = document.querySelector(".terminal-session.active .xterm-cursor");
    const row = cursor?.closest(".xterm-rows > div");
    if (!row) return false;
    const range = document.createRange();
    range.setStart(row, 0);
    range.setEndBefore(cursor);
    return range.toString().replace(/\u00a0/g, " ").endsWith("display cpu-usage");
  }, null, { timeout: 3_000 });
  if (submittedCommands.length !== submissionsBeforePointerNavigation) {
    throw new Error("Enter submitted input while accepting the keyboard-selected completion");
  }
  await page.keyboard.press("Control+U");
  await page.keyboard.type("dis");
  await page.getByRole("listbox", { name: "命令补全与推荐" }).waitFor({ timeout: 5_000 });
  await checkSuggestionHighlight(page, -1);
  const commandBeforeCompletion = lastExecutedCommand;
  const submissionsBeforeCompletion = submittedCommands.length;
  await page.keyboard.press("Enter");
  await page.getByRole("listbox", { name: "命令补全与推荐" }).waitFor({ state: "detached", timeout: 2_000 });
  await delay(50);
  if (submittedCommands.length !== submissionsBeforeCompletion || lastExecutedCommand !== commandBeforeCompletion) {
    throw new Error(`Enter submitted input instead of filling the default completion: ${JSON.stringify(submittedCommands)}`);
  }
  await page.waitForFunction(() => {
    const host = document.querySelector(".terminal-session.active .xterm");
    const cursor = host?.querySelector(".xterm-cursor, .xterm-cursor-block");
    if (!cursor || !host.contains(document.activeElement)) return false;
    const row = cursor.closest(".xterm-rows > div");
    if (!row) return false;
    const range = document.createRange();
    range.setStart(row, 0);
    range.setEndBefore(cursor);
    return range.toString().replace(/\u00a0/g, " ").endsWith("display version");
  }, null, { timeout: 3_000 });
  await page.keyboard.press("Control+U");
  await page.keyboard.type("sys");
  await page.getByRole("listbox", { name: "命令补全与推荐" }).waitFor({ timeout: 5_000 });
  await page.getByRole("option", { name: /system-view/ }).waitFor({ timeout: 5_000 });
  await page.getByText(/视图：用户视图/).waitFor({ timeout: 5_000 });
  await page.keyboard.press("Control+U");
  await page.keyboard.type("dis");
  await page.getByRole("listbox", { name: "命令补全与推荐" }).waitFor({ timeout: 5_000 });
  if ((await page.locator('[role="option"][aria-selected="true"]').count()) !== 0) {
    throw new Error("Command completions did not reset to an unselected state");
  }
  await page.keyboard.press("ArrowDown");
  await page.waitForFunction(
    () => document.querySelectorAll('[role="option"]')[0]?.getAttribute("aria-selected") === "true",
    null,
    { timeout: 2_000 },
  );
  await page.keyboard.press("Enter");
  await delay(50);
  if (submittedCommands.length !== submissionsBeforeCompletion || lastExecutedCommand !== commandBeforeCompletion) {
    throw new Error(`Selecting a completion executed it immediately: ${lastExecutedCommand}`);
  }
  await page.waitForFunction(() => {
    const host = document.querySelector(".terminal-session.active .xterm");
    const cursor = host?.querySelector(".xterm-cursor, .xterm-cursor-block");
    if (!cursor || !host.contains(document.activeElement)) return false;
    const row = cursor.closest(".xterm-rows > div");
    if (!row) return false;
    const range = document.createRange();
    range.setStart(row, 0);
    range.setEndBefore(cursor);
    return range.toString().replace(/\u00a0/g, " ").endsWith("display version");
  }, null, { timeout: 3_000 });
  await page.keyboard.press("Enter");
  await page.waitForFunction(
    () => document.body.textContent.includes("H3C Comware Software, Version 7.1"),
    null,
    { timeout: 5_000 },
  ).catch(async (error) => {
    const terminalText = await page.locator(".xterm-rows").allInnerTexts();
    throw new Error(`${error.message}; terminal=${JSON.stringify(terminalText)}`);
  });
  await page.getByText("智能推荐", { exact: true }).waitFor({ timeout: 5_000 });
  const recommendationOptions = page.getByRole("option");
  if ((await recommendationOptions.count()) !== 5) {
    throw new Error(`Expected five recommendations, got ${await recommendationOptions.count()}`);
  }
  if ((await page.locator('[role="option"][aria-selected="true"]').count()) !== 0) {
    throw new Error("Recommendations selected an item before keyboard or pointer navigation");
  }
  await checkSuggestionPointerNavigation(page);
  await page.keyboard.press("ArrowDown");
  await page.waitForFunction(
    () => document.querySelectorAll('[role="option"]')[0]?.getAttribute("aria-selected") === "true",
    null,
    { timeout: 2_000 },
  );
  await page.evaluate(() => document.querySelector(".terminal-session.active .xterm-helper-textarea")?.focus());
  await page.keyboard.press("ArrowUp");
  await page.waitForFunction(
    () => {
      const options = document.querySelectorAll('[role="option"]');
      return options[options.length - 1]?.getAttribute("aria-selected") === "true";
    },
    null,
    { timeout: 2_000 },
  ).catch(async (error) => {
    const state = await page.locator('[role="option"]').evaluateAll((options) =>
      options.map((option) => ({ selected: option.getAttribute("aria-selected"), text: option.textContent })),
    );
    throw new Error(`${error.message}; recommendation selection=${JSON.stringify(state)}`);
  });
  await page.keyboard.press("ArrowDown");
  const suggestionLayout = await page.evaluate(() => {
    const frameElement = document.querySelector(".terminal-session.active .terminal-frame");
    const panelElement = frameElement?.querySelector(".command-suggestions");
    const panel = panelElement?.getBoundingClientRect();
    const frame = frameElement?.getBoundingClientRect();
    const cursor = frameElement?.querySelector(".xterm-cursor, .xterm-cursor-block")?.getBoundingClientRect();
    const panelStyle = panelElement
      ? getComputedStyle(panelElement)
      : null;
    return panel && frame && cursor ? {
      panelLeft: panel.left,
      panelRight: panel.right,
      panelTop: panel.top,
      cursorBottom: cursor.bottom,
      panelHeight: panel.height,
      frameLeft: frame.left,
      frameRight: frame.right,
      frameHeight: frame.height,
      panelBackground: panelStyle?.backgroundColor,
      panelRadius: panelStyle?.borderRadius,
    } : null;
  });
  if (
    !suggestionLayout ||
    suggestionLayout.panelLeft < suggestionLayout.frameLeft ||
    suggestionLayout.panelRight > suggestionLayout.frameRight ||
    suggestionLayout.panelTop < suggestionLayout.cursorBottom ||
    suggestionLayout.panelHeight > 260 ||
    suggestionLayout.panelHeight > suggestionLayout.frameHeight * 0.5
  ) {
    throw new Error(`Suggestion panel escaped its compact terminal bounds: ${JSON.stringify(suggestionLayout)}`);
  }
  if (process.env.WEBUI_TEST_SCREENSHOT) {
    await page.screenshot({ path: process.env.WEBUI_TEST_SCREENSHOT, fullPage: true });
  }
  // Clicking a suggestion must restore terminal focus even after another
  // control was focused, and typing must continue at the end without Enter.
  await page.evaluate(() => document.querySelector(".topbar button")?.focus());
  const commandBeforePointerFill = lastExecutedCommand;
  await page.getByRole("option", { name: /display device/ }).click();
  await page.waitForFunction(() => {
    const host = document.querySelector(".terminal-session.active .xterm");
    const cursor = host?.querySelector(".xterm-cursor, .xterm-cursor-block");
    if (!cursor || !host.contains(document.activeElement)) return false;
    const row = cursor.closest(".xterm-rows > div");
    if (!row) return false;
    const range = document.createRange();
    range.setStart(row, 0);
    range.setEndBefore(cursor);
    return range.toString().replace(/\u00a0/g, " ").endsWith("display device");
  }, null, { timeout: 3_000 });
  if (lastExecutedCommand !== commandBeforePointerFill) {
    throw new Error("Clicking a recommendation executed it instead of filling it");
  }
  await page.keyboard.type(" verbose");
  await page.keyboard.press("Enter");
  await delay(50);
  if (lastExecutedCommand !== "display device verbose") {
    throw new Error(`Typing after pointer fill did not append at the end: ${lastExecutedCommand}`);
  }
  // Agent activity invalidates both visible and in-flight manual candidates.
  await page.keyboard.press("Control+U");
  await page.keyboard.type("dis");
  await page.getByRole("listbox", { name: "命令补全与推荐" }).waitFor();
  routedSockets.at(-1).send(JSON.stringify({ type: "terminal_input_source", input_source: "agent" }));
  await page.getByRole("listbox", { name: "命令补全与推荐" }).waitFor({ state: "hidden" });
  await page.keyboard.press("Control+U");
  holdNextCompletion = true;
  await page.keyboard.type("dis");
  for (let attempt = 0; !releaseCompletion && attempt < 50; attempt++) await delay(50);
  if (!releaseCompletion) throw new Error("Expected a held completion request");
  routedSockets.at(-1).send(JSON.stringify({ type: "terminal_input_source", input_source: "agent" }));
  await delay(50);
  releaseCompletion();
  const completionCountAfterAgent = completionRequests;
  // Even a stale legacy push without an origin must not reopen the popup.
  routedSockets.at(-1).send(JSON.stringify({ type: "recommendation", suggestions: ["display device"] }));
  routedSockets.at(-1).send("\r\nAgent command output\r\n<H3C> ");
  await delay(300);
  if (await page.getByRole("listbox", { name: "命令补全与推荐" }).count()) {
    throw new Error("Agent activity resurrected a completion/recommendation popup");
  }
  if (completionRequests !== completionCountAfterAgent) throw new Error("Agent output triggered completion");
  // Real typing and a second Enter still restore the normal human workflow.
  await page.keyboard.press("Control+U");
  await page.keyboard.type("dis");
  await page.getByRole("listbox", { name: "命令补全与推荐" }).waitFor();
  await page.keyboard.press("Enter");
  await page.keyboard.press("Enter");
  await page.getByText("智能推荐", { exact: true }).waitFor();
  await page.keyboard.press("Escape");
  await coreSwitchBookmark.locator(".status-dot.online").waitFor({ timeout: 5_000 });
  const terminalLayout = await page.evaluate(() => {
    const tabs = document.querySelector(".session-tabs")?.getBoundingClientRect();
    const frame = document.querySelector(".terminal-session.active .terminal-frame")?.getBoundingClientRect();
    return {
      fakeTitlebars: document.querySelectorAll(".terminal-titlebar").length,
      gap: tabs && frame ? Math.round(frame.top - tabs.bottom) : null,
    };
  });
  if (terminalLayout.fakeTitlebars !== 0 || terminalLayout.gap !== 0) {
    throw new Error(`Terminal is not directly below its tabs: ${JSON.stringify(terminalLayout)}`);
  }

  await page.getByRole("button", { name: "编辑 核心交换机" }).click();
  const editDialog = page.getByRole("dialog", { name: "编辑设备" });
  await editDialog.getByLabel("设备名称").fill("核心交换机-已编辑");
  await editDialog.getByRole("button", { name: "保存" }).click();
  const editedBookmark = page.getByRole("button", { name: "连接 核心交换机-已编辑" });
  await editedBookmark.waitFor({ timeout: 5_000 });
  const existingTabText = await page.locator(".session-tab.active .session-tab-main").innerText();
  if (!existingTabText.includes("核心交换机") || existingTabText.includes("已编辑")) {
    throw new Error(`Existing session changed after device edit: ${existingTabText}`);
  }

  await quickAddButton.click();
  await page
    .getByRole("menu", { name: "快速打开连接" })
    .getByRole("menuitem", { name: /核心交换机-已编辑/ })
    .click();
  const secondPasswordDialog = page.getByRole("dialog", { name: "连接目标" });
  await secondPasswordDialog.getByLabel("登录密码").fill("admin");
  await secondPasswordDialog.getByRole("button", { name: "连接" }).click();
  await page.locator(".session-tab").nth(1).waitFor({ timeout: 5_000 });
  await page.locator(".session-tab.active .session-status-dot.connected").waitFor({ timeout: 5_000 });
  if ((await page.locator(".xterm").count()) !== 2) {
    throw new Error("Expected two same-device terminal sessions to stay mounted");
  }
  const savedRecentHistory = await page.evaluate(() =>
    JSON.parse(window.localStorage.getItem("auditbox-recent-connections") || "[]"),
  );
  if (savedRecentHistory.length > 1 || (savedRecentHistory[0] && savedRecentHistory[0].host !== "192.168.10.1")) {
    throw new Error(`Same-device tabs duplicated recent history: ${JSON.stringify(savedRecentHistory)}`);
  }

  await routedSockets.at(-1).close({ code: 1011, reason: "test disconnect" });
  await page.locator(".terminal-reconnect").getByRole("button", { name: "重新连接", exact: true }).waitFor({
    timeout: 5_000,
  });
  await page.locator(".session-tab.active .session-tab-status", { hasText: "已断开" }).waitFor();
  await page.getByText("连接已断开", { exact: true }).waitFor();
  const tabReconnect = page.locator(".session-tab.active .session-tab-refresh");
  if (!(await tabReconnect.getAttribute("aria-label"))?.startsWith("重新连接 ")) {
    throw new Error("Disconnected tab did not expose its reconnect action");
  }
  const socketCountBeforeReconnect = routedSockets.length;
  await tabReconnect.click();
  await page.locator(".session-tab.active .session-status-dot.connected").waitFor({ timeout: 5_000 });
  await page.locator(".session-tab.active .session-tab-status", { hasText: "已连接" }).waitFor();
  if (routedSockets.length <= socketCountBeforeReconnect) {
    throw new Error("Reconnect did not open a new WebSocket session");
  }
  const socketCloseCountBeforeTabClose = routedSocketCloseCount;
  await page.locator(".session-tab.active .session-tab-close").click();
  if ((await page.locator(".session-tab").count()) !== 1) {
    throw new Error("Closing one tab should keep the other terminal session");
  }
  const closeDeadline = Date.now() + 2_000;
  while (routedSocketCloseCount === socketCloseCountBeforeTabClose && Date.now() < closeDeadline) {
    await delay(25);
  }
  if (routedSocketCloseCount === socketCloseCountBeforeTabClose) {
    throw new Error("Closing a tab did not actively close its WebSocket connection");
  }

  const legacyInputCount = await page.locator(".terminal-input").count();
  if (legacyInputCount !== 0) {
    throw new Error("Expected old terminal input bar to be removed");
  }

  if (await page.locator(".guide-panel-header, .guide-domain-count").count()) {
    throw new Error("Expected the duplicated command-guide heading and category count to be removed");
  }
  await page.getByRole("tree", { name: "指令速查分类" }).waitFor({ timeout: 5_000 });
  if (await page.getByText("按设备类型、厂家和功能查找", { exact: true }).count()) {
    throw new Error("Expected command tree subtitle to be removed");
  }
  const modeStripState = await page.evaluate(() => ({
    hasGuideMode: Boolean(document.querySelector(".guide-mode")),
    hasOfflineModeText: document.body.innerText.includes("全部离线"),
  }));
  if (modeStripState.hasGuideMode || modeStripState.hasOfflineModeText) {
    throw new Error(`Expected offline mode strip to be removed: ${JSON.stringify(modeStripState)}`);
  }
  const h3cRootButtons = page.locator(".offline-skill-root");
  await page.getByRole("button", { name: /交换机运维/ }).waitFor({ timeout: 5_000 });
  if (
    (await h3cRootButtons.count()) !== 1 ||
    (await page.getByRole("button", { name: /Linux 运维/ }).count()) !== 0 ||
    (await page.getByRole("button", { name: /防火墙运维/ }).count()) !== 0
  ) {
    throw new Error("The H3C session did not isolate the right-side skill tree");
  }
  await page.getByRole("button", { name: /交换机运维/ }).click();
  const h3cBranch = page.getByRole("button", { name: /H3C 交换机/ });
  await h3cBranch.waitFor();
  if (
    (await page.getByRole("button", { name: /华为交换机/ }).count()) !== 0 ||
    (await page.getByRole("button", { name: /Cisco 交换机/ }).count()) !== 0
  ) {
    throw new Error("Unsupported switch vendors leaked into the H3C skill tree");
  }
  await h3cBranch.click();
  for (const functionName of [/系统状态/, /接口检查/, /VLAN 与 MAC/, /二层拓扑/, /三层转发/]) {
    await page.getByRole("button", { name: functionName }).waitFor();
  }

  await quickAddButton.click();
  const serverQuickMenu = page.getByRole("menu", { name: "快速打开连接" });
  await serverQuickMenu.getByRole("menuitem", { name: /新建安全连接/ }).click();
  const serverDialog = page.getByRole("dialog", { name: "新建安全连接" });
  await serverDialog.getByLabel("设备大类").selectOption("server");
  await serverDialog.getByLabel("设备子类型").selectOption("linux");
  await serverDialog.getByLabel("地址").fill("192.168.10.2");
  await serverDialog.getByLabel("设备名称").fill("业务服务器");
  await serverDialog.getByLabel("用户名").fill("root");
  await serverDialog.getByLabel("登录密码").fill("admin");
  await serverDialog.getByRole("button", { name: "连接", exact: true }).click();
  await page.locator(".session-tab.active .session-status-dot.connected").waitFor({ timeout: 5_000 });

  const guideRootButtons = page.locator(".offline-skill-root");
  await page.getByRole("button", { name: /Linux 运维/ }).waitFor({ timeout: 5_000 });
  await page.waitForFunction(() => {
    const roots = document.querySelectorAll(".offline-skill-root");
    return roots.length === 1 && roots[0].textContent.includes("Linux 运维");
  }, null, { timeout: 5_000 });
  if (
    (await guideRootButtons.count()) !== 1 ||
    (await page.getByRole("button", { name: /交换机运维/ }).count()) !== 0
  ) {
    throw new Error(`The Linux session did not isolate the right-side skill tree: ${JSON.stringify({
      roots: await guideRootButtons.allTextContents(),
      switchButtons: await page.getByRole("button", { name: /交换机运维/ }).allTextContents(),
    })}`);
  }
  if ((await guideRootButtons.first().getAttribute("aria-expanded")) !== "false") {
    throw new Error("The routed Linux command root was not collapsed initially");
  }
  if ((await page.locator(".offline-skill-branch").count()) !== 0) {
    throw new Error("Command branches rendered before their root was opened");
  }
  const collapsedRootHeight = await guideRootButtons.first().evaluate((button) =>
    Math.round(button.closest(".offline-skill-group").getBoundingClientRect().height),
  );
  if (collapsedRootHeight > 80) {
    throw new Error(`Collapsed command root stretched vertically: ${collapsedRootHeight}`);
  }
  await page.getByRole("button", { name: /Linux 运维/ }).click();
  await page.getByRole("button", { name: /网络检查/ }).waitFor({ timeout: 5_000 });
  await page.getByRole("button", { name: /系统检查/ }).waitFor({ timeout: 5_000 });
  await page.getByRole("button", { name: /服务检查/ }).waitFor({ timeout: 5_000 });
  await page.getByRole("button", { name: /安全检查/ }).waitFor({ timeout: 5_000 });
  const linuxBranchButtons = page.locator(".offline-skill-group.open .offline-skill-branch-trigger");
  for (let index = 0; index < await linuxBranchButtons.count(); index += 1) {
    if ((await linuxBranchButtons.nth(index).getAttribute("aria-expanded")) !== "false") {
      throw new Error("Every Linux command branch must be collapsed initially");
    }
  }
  for (const branchName of [
    /网络检查/,
    /系统检查/,
    /进程资源/,
    /文件磁盘/,
    /服务检查/,
    /用户权限/,
    /软件计划/,
    /安全检查/,
    /高危操作/,
  ]) {
    await page.getByRole("button", { name: branchName }).click();
  }
  const skillShape = await page.evaluate(() => ({
    rootCount: document.querySelectorAll(".offline-skill-group").length,
    branchCount: document.querySelectorAll(".offline-skill-branch").length,
    leafCount: document.querySelectorAll(".offline-skill-leaf-shell").length,
    leafChevronCount: document.querySelectorAll(".offline-skill-leaf .leaf-chevron").length,
    leafTagCount: document.querySelectorAll(".offline-skill-leaf em").length,
    hasOfflineRuleLabel: document.body.innerText.includes("离线规则"),
    rootIntroFontSize: parseFloat(
      getComputedStyle(document.querySelector(".offline-skill-root small")).fontSize,
    ),
    branchIntroFontSize: parseFloat(
      getComputedStyle(document.querySelector(".offline-skill-branch-trigger small")).fontSize,
    ),
    leafIntroFontSize: parseFloat(getComputedStyle(document.querySelector(".offline-skill-leaf small")).fontSize),
  }));
  if (
    skillShape.rootCount !== 1 ||
    skillShape.branchCount < 8 ||
    skillShape.leafCount < 30 ||
    skillShape.leafChevronCount !== skillShape.leafCount ||
    skillShape.leafTagCount !== 0 ||
    skillShape.hasOfflineRuleLabel ||
    skillShape.rootIntroFontSize < 12 ||
    skillShape.branchIntroFontSize < 12 ||
    skillShape.leafIntroFontSize < 12
  ) {
    throw new Error(`Unexpected skill tree shape: ${JSON.stringify(skillShape)}`);
  }
  for (const branchName of [/文件磁盘/, /进程资源/, /用户权限/, /软件计划/, /高危操作/]) {
    await page.getByRole("button", { name: branchName }).waitFor({ timeout: 5_000 });
  }
  const telnetGuideButton = page.locator(".offline-skill-leaf").filter({
    has: page.locator("strong", { hasText: /^Telnet 连接测试$/ }),
  });
  await telnetGuideButton.click();
  await page.getByLabel("目标地址").fill("192.168.10.1");
  await page.getByLabel("端口").fill("23");
  await page.getByRole("button", { name: "下发" }).click();
  await page.waitForFunction(() =>
    [...document.querySelectorAll(".xterm-rows > div")]
      .map((row) => row.textContent)
      .join("\n")
      .includes("telnet 192.168.10.1 23"),
  );
  await page.locator(".offline-skill-leaf").filter({
    has: page.locator("strong", { hasText: /^Telnet 服务检查$/ }),
  }).waitFor();
  const networkBranchButton = page.getByRole("button", { name: /网络检查/ });
  const serviceStatusButton = page.locator(".offline-skill-leaf").filter({
    has: page.locator("strong", { hasText: /^服务状态$/ }),
  });
  await serviceStatusButton.click();
  await networkBranchButton.click();
  await page.waitForFunction(() => {
    const networkBranch = [...document.querySelectorAll(".offline-skill-branch")].find(
      (branch) => branch.querySelector(".offline-skill-branch-trigger strong")?.textContent === "网络检查",
    );
    return networkBranch && !networkBranch.classList.contains("open");
  });
  await serviceStatusButton.click();
  const closeState = await page.evaluate(() => {
    const networkBranch = [...document.querySelectorAll(".offline-skill-branch")].find(
      (branch) => branch.querySelector(".offline-skill-branch-trigger strong")?.textContent === "网络检查",
    );
    return {
      networkOpen: networkBranch?.classList.contains("open") || false,
      activeLeafCount: document.querySelectorAll(".offline-skill-leaf-shell.active").length,
    };
  });
  if (closeState.networkOpen || closeState.activeLeafCount !== 0) {
    throw new Error(`Closing a leaf reopened the network branch: ${JSON.stringify(closeState)}`);
  }
  await networkBranchButton.click();
  const portGuideButton = page.getByRole("button", { name: /端口不通/ });
  await portGuideButton.click();
  await page.locator(".offline-skill-leaf-shell.active .guide-form").waitFor({ timeout: 5_000 });
  await portGuideButton.click();
  await page.waitForFunction(
    () => !document.querySelector(".offline-skill-leaf-shell.active .guide-form"),
    null,
    { timeout: 5_000 },
  );
  await portGuideButton.click();
  await page.locator(".offline-skill-leaf-shell.active .guide-form").waitFor({ timeout: 5_000 });
  const dropdownLayout = await page.evaluate(() => {
    const panel = document.querySelector(".offline-skill-leaf-shell.active .offline-skill-leaf-panel");
    const form = panel?.querySelector(".guide-form");
    const fields = panel?.querySelector(".guide-fields");
    const commandList = panel?.querySelector(".guide-command-list");
    const actionRow = panel?.querySelector(".guide-action-row");
    const runButton = panel?.querySelector(".guide-run");
    const formStyle = form ? getComputedStyle(form) : null;
    const actionRowStyle = actionRow ? getComputedStyle(actionRow) : null;
    const runButtonStyle = runButton ? getComputedStyle(runButton) : null;
    return {
      hasPanel: Boolean(panel),
      hasFields: Boolean(fields),
      hasCommandList: Boolean(commandList),
      commandListInsideForm: Boolean(form && commandList && form.contains(commandList)),
      commandListBetweenFieldsAndAction: Boolean(
        fields &&
        commandList &&
        actionRow &&
        fields.getBoundingClientRect().bottom <= commandList.getBoundingClientRect().top &&
        commandList.getBoundingClientRect().bottom <= actionRow.getBoundingClientRect().top
      ),
      fieldCount: fields?.children.length || 0,
      sectionTitleCount: panel?.querySelectorAll(".guide-section-title").length || 0,
      guidePlanCount: panel?.querySelectorAll(".guide-plan").length || 0,
      guideResultCount: panel?.querySelectorAll(".guide-result").length || 0,
      formBorderTop: formStyle?.borderTopWidth || "",
      formBackground: formStyle?.backgroundColor || "",
      actionJustifyItems: actionRowStyle?.justifyItems || "",
      buttonMinHeight: runButtonStyle?.minHeight || "",
      buttonRadius: runButtonStyle?.borderTopLeftRadius || "",
    };
  });
  if (
    !dropdownLayout.hasPanel ||
    !dropdownLayout.hasFields ||
    !dropdownLayout.hasCommandList ||
    !dropdownLayout.commandListInsideForm ||
    !dropdownLayout.commandListBetweenFieldsAndAction ||
    dropdownLayout.fieldCount < 2 ||
    dropdownLayout.sectionTitleCount !== 0 ||
    dropdownLayout.guidePlanCount !== 0 ||
    dropdownLayout.guideResultCount !== 0 ||
    dropdownLayout.formBorderTop !== "0px" ||
    dropdownLayout.formBackground !== "rgba(0, 0, 0, 0)" ||
    dropdownLayout.actionJustifyItems !== "end" ||
    dropdownLayout.buttonMinHeight !== "32px" ||
    dropdownLayout.buttonRadius !== "999px"
  ) {
    throw new Error(`Guide layout is not embedded enough: ${JSON.stringify(dropdownLayout)}`);
  }
  const ipInput = page.getByLabel("目标地址");
  if ((await ipInput.count()) !== 1) {
    throw new Error("Expected one complete IP address input");
  }
  if ((await ipInput.inputValue()) !== "192.168.10.2" || (await page.getByLabel("端口").inputValue()) !== "22") {
    throw new Error("Connected guide parameters did not follow the active terminal target");
  }
  await ipInput.fill("");
  await ipInput.fill("266.266.266.266");
  if ((await ipInput.inputValue()) !== "") {
    throw new Error("Expected out-of-range IPv4 input to be rejected");
  }
  await ipInput.fill(" 192 . 168 . 10 . 1 ");
  if ((await ipInput.inputValue()) !== "192.168.10.1") {
    throw new Error(`Expected whitespace in IPv4 input to be removed automatically: ${await ipInput.inputValue()}`);
  }
  await page.getByLabel("端口").fill("22");
  await page.waitForFunction(() => document.querySelectorAll(".guide-command-item").length === 4);
  const lastCommandBeforeGuide = lastExecutedCommand;
  await page.getByRole("button", { name: "下发" }).click();
  await page.waitForFunction(() =>
    [...document.querySelectorAll(".xterm-rows > div")]
      .map((row) => row.textContent)
      .join("\n")
      .includes("ping -c 4 192.168.10.1"),
  );
  if (lastExecutedCommand !== lastCommandBeforeGuide) {
    throw new Error("Guide command executed before the user pressed Enter");
  }
  if ((await page.locator(".guide-command-item.filled").count()) !== 1) {
    throw new Error("The first guide command was not shown as filled");
  }
  if (!programmaticInputs.some(input => input.data === "ping -c 4 192.168.10.1")) {
    throw new Error("Guide fill lost its input source");
  }
  const completionCountBeforeGuideEdit = completionRequests;
  await page.evaluate(() => document.querySelector(".terminal-session.active .xterm-helper-textarea")?.focus());
  await page.keyboard.type(" ");
  await page.keyboard.press("Backspace");
  await delay(250);
  if (completionRequests !== completionCountBeforeGuideEdit || await page.getByRole("listbox", { name: "命令补全与推荐" }).count()) {
    throw new Error("Editing a guide-originated line triggered completion");
  }
  await page.evaluate(() => document.querySelector(".terminal-session.active .xterm-helper-textarea")?.focus());
  await page.keyboard.press("Enter");
  await page.waitForFunction(() =>
    document.querySelectorAll(".guide-command-item.executed").length === 1 &&
    document.querySelectorAll(".guide-command-item.filled").length === 1,
  );
  await page.waitForFunction(() =>
    [...document.querySelectorAll(".xterm-rows > div")]
      .map((row) => row.textContent)
      .join("\n")
      .includes("nc -zv 192.168.10.1 22"),
  );
  const filledGuideCommand = await page.locator(".guide-command-item.filled > code").textContent();
  if (filledGuideCommand !== "nc -zv 192.168.10.1 22") {
    throw new Error(`The next guide command was not filled: ${filledGuideCommand}`);
  }
  if (lastExecutedCommand !== "ping -c 4 192.168.10.1") {
    throw new Error(`The first guide command was not submitted by Enter: ${lastExecutedCommand}`);
  }
  if (guideAuditEvents.filter((event) => event.event === "guide_command_submitted").length !== 1) {
    throw new Error("The guide submission was not emitted as an audit event");
  }
  if (!programmaticInputs.some(input => input.data === "\r")) throw new Error("Guide Enter became human input");
  const result = await page.evaluate(() => ({
    hasXterm: Boolean(document.querySelector(".xterm")),
    hasCommand: [...document.querySelectorAll(".xterm-rows > div")]
      .map((row) => row.textContent)
      .join("\n")
      .includes("nc -zv 192.168.10.1 22"),
    hasInlineGuideForm: Boolean(document.querySelector(".offline-skill-leaf-shell.active .guide-form")),
    hasInlineGuideResult: Boolean(document.querySelector(".offline-skill-leaf-shell.active .guide-result")),
    floatingGuideForms: document.querySelectorAll(".guide-shell > .guide-form").length,
    floatingGuideResults: document.querySelectorAll(".guide-shell > .guide-result").length,
    bodyWidth: document.body.scrollWidth,
    bodyHeight: Math.max(document.body.scrollHeight, document.documentElement.scrollHeight),
    appShellHeight: Math.round(document.querySelector(".app-shell")?.getBoundingClientRect().height || 0),
    terminalFrameHeight: Math.round(
      document.querySelector(".terminal-frame")?.getBoundingClientRect().height || 0,
    ),
    viewportWidth: innerWidth,
    viewportHeight: innerHeight,
  }));
  if (
    !result.hasXterm ||
    !result.hasCommand ||
    !result.hasInlineGuideForm ||
    result.hasInlineGuideResult ||
    result.floatingGuideForms !== 0 ||
    result.floatingGuideResults !== 0 ||
    result.bodyWidth > result.viewportWidth ||
    result.bodyHeight > result.viewportHeight + 2 ||
    result.appShellHeight > result.viewportHeight + 2 ||
    result.terminalFrameHeight > result.viewportHeight
  ) {
    throw new Error(`Unexpected UI state: ${JSON.stringify(result)}`);
  }

  if ((await page.getByRole("button", { name: /重启系统/ }).count()) === 0) {
    await page.getByRole("button", { name: /高危操作/ }).click();
  }
  await page.getByRole("button", { name: /重启系统/ }).click();
  await page.getByRole("button", { name: "下发" }).click();
  await page.waitForFunction(() => {
    const command = document.querySelector(".offline-skill-leaf-shell.active .guide-command-item.filled > code");
    const terminalText = [...document.querySelectorAll(".xterm-rows > div")]
      .map((row) => row.textContent)
      .join("\n");
    return command?.textContent === "sudo reboot" && terminalText.includes("sudo reboot");
  });
  if ((await page.getByRole("dialog", { name: "高危操作确认" }).count()) !== 0) {
    throw new Error("A filled high-risk guide command executed without Enter");
  }
  await page.evaluate(() => document.querySelector(".terminal-session.active .xterm-helper-textarea")?.focus());
  await page.keyboard.press("Enter");
  await page.getByRole("dialog", { name: "高危操作确认" }).waitFor({ timeout: 5_000 });
  if (confirmedDangerExecutionCount !== 0) {
    throw new Error("Danger command ran before confirmation");
  }
  if ((await page.getByRole("dialog", { name: "高危操作确认" }).getByText(/秒内确认/).count()) !== 0) {
    throw new Error("Danger confirmation still contains a countdown");
  }
  const dangerSurface = await page.getByRole("dialog", { name: "高危操作确认" }).evaluate((dialog) => {
    const style = getComputedStyle(dialog);
    return { background: style.backgroundColor, radius: style.borderRadius };
  });
  if (
    dangerSurface.background !== "rgb(255, 255, 255)" ||
    dangerSurface.radius !== suggestionLayout.panelRadius ||
    suggestionLayout.panelBackground !== "rgba(255, 255, 255, 0.98)"
  ) {
    throw new Error(`Terminal overlays do not match the system surface: ${JSON.stringify({ dangerSurface, suggestionLayout })}`);
  }
  if (process.env.WEBUI_TEST_DANGER_SCREENSHOT) {
    await page.screenshot({ path: process.env.WEBUI_TEST_DANGER_SCREENSHOT, fullPage: true });
  }
  await page.getByRole("button", { name: "确认执行" }).click();
  const dangerDeadline = Date.now() + 2_000;
  while (confirmedDangerExecutionCount === 0 && Date.now() < dangerDeadline) {
    await delay(25);
  }
  if (confirmedDangerExecutionCount !== 1 || lastExecutedCommand !== "sudo reboot") {
    throw new Error("Confirmed danger command did not execute exactly once");
  }

  await page.evaluate(() => document.querySelector(".terminal-session.active .xterm-helper-textarea")?.focus());
  await page.keyboard.type("rm -rf");
  await page.getByRole("listbox", { name: "命令补全与推荐" }).waitFor({ timeout: 5_000 });
  await page.getByRole("option", { name: /rm -rf \*/ }).waitFor({ timeout: 5_000 });
  if ((await page.locator('[role="option"][aria-selected="true"]').count()) !== 0) {
    throw new Error("Danger completion should initially have no selected candidate");
  }
  const submissionsBeforeDangerFill = submittedCommands.length;
  const confirmationsBeforeDangerFill = dangerConfirmationSequence;
  await page.keyboard.press("Enter");
  await page.getByRole("listbox", { name: "命令补全与推荐" }).waitFor({ state: "detached", timeout: 2_000 });
  await delay(50);
  if (
    submittedCommands.length !== submissionsBeforeDangerFill ||
    dangerConfirmationSequence !== confirmationsBeforeDangerFill ||
    confirmedDangerExecutionCount !== 1 ||
    (await page.getByRole("dialog", { name: "高危操作确认" }).count()) !== 0
  ) {
    throw new Error("Accepting a danger completion submitted or confirmed the command before the second Enter");
  }
  await page.keyboard.press("Enter");
  await page.getByRole("dialog", { name: "高危操作确认" }).waitFor({ timeout: 5_000 });
  if (
    submittedCommands.length !== submissionsBeforeDangerFill + 1 ||
    submittedCommands.at(-1) !== "rm -rf *" ||
    dangerConfirmationSequence !== confirmationsBeforeDangerFill + 1 ||
    confirmedDangerExecutionCount !== 1
  ) {
    throw new Error("The second Enter did not submit the completed danger command for confirmation exactly once");
  }
  await page.getByRole("button", { name: "取消" }).click();
  await page.getByRole("dialog", { name: "高危操作确认" }).waitFor({ state: "detached", timeout: 5_000 });
  if (cancelledDangerCount !== 1 || confirmedDangerExecutionCount !== 1) {
    throw new Error("Cancelling a danger command did not cancel exactly that execution");
  }

  await page.evaluate(() => document.querySelector(".terminal-session.active .xterm-helper-textarea")?.focus());
  await page.keyboard.type("rm -rf *");
  await page.keyboard.press("Enter");
  await page.getByRole("dialog", { name: "高危操作确认" }).waitFor({ timeout: 5_000 });
  await page.getByRole("button", { name: "确认执行" }).click();
  const repeatedDangerDeadline = Date.now() + 2_000;
  while (confirmedDangerExecutionCount < 2 && Date.now() < repeatedDangerDeadline) {
    await delay(25);
  }
  if (confirmedDangerExecutionCount !== 2 || lastExecutedCommand !== "rm -rf *") {
    throw new Error("The same danger command did not require and receive a fresh confirmation");
  }

  await page.getByRole("dialog", { name: "高危操作确认" }).waitFor({ state: "detached", timeout: 5_000 });
  await page.evaluate(() => document.querySelector(".terminal-session.active .xterm-helper-textarea")?.focus());
  await page.keyboard.type("rm -rf *");
  await page.keyboard.press("Enter");
  await page.getByRole("dialog", { name: "高危操作确认" }).waitFor({ timeout: 5_000 });
  await page.getByRole("button", { name: "取消" }).click();
  await page.getByRole("button", { name: /删除路径/ }).click();
  await page.getByLabel("路径").fill("");
  if (!(await page.getByRole("button", { name: "下发" }).isDisabled())) {
    throw new Error("Invalid guide parameters left the dispatch button enabled");
  }
  await page.getByText("删除路径不能为空", { exact: true }).waitFor({ timeout: 5_000 });
  if ((await page.getByRole("dialog", { name: "高危操作确认" }).count()) !== 0) {
    throw new Error("An empty destructive path opened a confirmation dialog");
  }

  await page.evaluate(() => document.querySelector(".terminal-session.active .xterm-helper-textarea")?.focus());
  for (let index = 0; index < 45; index += 1) {
    await page.keyboard.type(`visibility-${index}`);
    await page.keyboard.press("Enter");
  }
  const visibilityDeadline = Date.now() + 5_000;
  while (lastExecutedCommand !== "visibility-44" && Date.now() < visibilityDeadline) {
    await delay(25);
  }
  if (lastExecutedCommand !== "visibility-44") {
    throw new Error(`Terminal input backlog did not drain: ${lastExecutedCommand}`);
  }

  const cursorState = await page.evaluate(() => {
    const host = document.querySelector(".xterm-host");
    const cursor = document.querySelector(".xterm-cursor, .xterm-cursor-block");
    if (!host || !cursor) return { hasHost: Boolean(host), hasCursor: Boolean(cursor) };
    const hostRect = host.getBoundingClientRect();
    const cursorRect = cursor.getBoundingClientRect();
    return {
      hasHost: true,
      hasCursor: true,
      hostBottom: Math.round(hostRect.bottom),
      cursorBottom: Math.round(cursorRect.bottom),
      cursorTop: Math.round(cursorRect.top),
    };
  });
  if (
    !cursorState.hasHost ||
    !cursorState.hasCursor ||
    cursorState.cursorBottom > cursorState.hostBottom - 12
  ) {
    throw new Error(`Terminal input cursor is clipped: ${JSON.stringify(cursorState)}`);
  }

  await page.evaluate(() => {
    window.__suggestionOverlapDetected = false;
    window.__suggestionPositionObserver?.disconnect();
    const frame = document.querySelector(".terminal-session.active .terminal-frame");
    const inspectPosition = () => {
      const panel = frame?.querySelector(".command-suggestions")?.getBoundingClientRect();
      const cursor = frame?.querySelector(".xterm-cursor, .xterm-cursor-block")?.getBoundingClientRect();
      if (panel && cursor && panel.top < cursor.bottom) {
        window.__suggestionOverlapDetected = true;
      }
    };
    window.__suggestionPositionObserver = new MutationObserver(() => {
      window.requestAnimationFrame(inspectPosition);
    });
    if (frame) {
      window.__suggestionPositionObserver.observe(frame, {
        subtree: true,
        childList: true,
        attributes: true,
        attributeFilter: ["class", "style"],
      });
    }
  });
  await page.keyboard.type("display version");
  await page.keyboard.press("Enter");
  await page.getByText("智能推荐", { exact: true }).waitFor({ timeout: 5_000 });
  await page.waitForFunction(() => {
    const frame = document.querySelector(".terminal-session.active .terminal-frame");
    const panel = frame?.querySelector(".command-suggestions")?.getBoundingClientRect();
    const cursor = frame?.querySelector(".xterm-cursor, .xterm-cursor-block")?.getBoundingClientRect();
    const frameRect = frame?.getBoundingClientRect();
    return Boolean(
      frame?.classList.contains("has-suggestion-space") &&
      panel && cursor && frameRect &&
      panel.top >= cursor.bottom &&
      panel.bottom <= frameRect.bottom
    );
  }, null, { timeout: 3_000 });
  const bottomSuggestionLayout = await page.evaluate(() => {
    const frameElement = document.querySelector(".terminal-session.active .terminal-frame");
    const panel = frameElement?.querySelector(".command-suggestions")?.getBoundingClientRect();
    const cursor = frameElement?.querySelector(".xterm-cursor, .xterm-cursor-block")?.getBoundingClientRect();
    const frame = frameElement?.getBoundingClientRect();
    const overlapDetected = Boolean(window.__suggestionOverlapDetected);
    window.__suggestionPositionObserver?.disconnect();
    return panel && cursor && frame ? {
      reserved: frameElement.classList.contains("has-suggestion-space"),
      overlapDetected,
      panelTop: Math.round(panel.top),
      panelBottom: Math.round(panel.bottom),
      cursorBottom: Math.round(cursor.bottom),
      frameBottom: Math.round(frame.bottom),
    } : null;
  });
  if (
    !bottomSuggestionLayout ||
    !bottomSuggestionLayout.reserved ||
    bottomSuggestionLayout.overlapDetected ||
    bottomSuggestionLayout.panelTop < bottomSuggestionLayout.cursorBottom ||
    bottomSuggestionLayout.panelBottom > bottomSuggestionLayout.frameBottom
  ) {
    throw new Error(`Bottom-edge suggestions were clipped or covered the command: ${JSON.stringify(bottomSuggestionLayout)}`);
  }
  await page.keyboard.press("Escape");

  await checkClientDeviceFlow(browser, url);
} finally {
  if (browser) await browser.close();
  server.kill();
}
