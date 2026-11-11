import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { spawn } from "node:child_process";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const base = "http://127.0.0.1:5222";
const server = spawn(process.execPath, [fileURLToPath(new URL("../node_modules/vite/bin/vite.js", import.meta.url)),
  "--host", "127.0.0.1", "--port", "5222", "--strictPort"], {
  cwd: fileURLToPath(new URL("..", import.meta.url)), stdio: "ignore",
});
const executablePath = [process.env.CHROME_PATH,
  "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
].find((candidate) => candidate && existsSync(candidate));
assert.ok(executablePath, "Chrome/Chromium not found; set CHROME_PATH to its executable path");
let browser;
try {
  for (let n = 0; ; n++) {
    try { if ((await fetch(base)).ok) break; } catch {}
    assert.ok(n < 100, "Vite did not start"); await delay(100);
  }
  browser = await chromium.launch({ headless: true, executablePath });
  const page = await browser.newPage();
  const events = [], tokens = [], errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(() => sessionStorage.setItem("auditbox_auth", JSON.stringify({
    username: "alice", role: "admin", token: "actor-token",
  })));
  await page.route("**/api/audit/behavior", async route => {
    const request = route.request();
    if (request.headers().authorization === "Bearer expired-token") return route.fulfill({ status: 401 });
    tokens.push(request.headers().authorization);
    events.push(...request.postDataJSON().events);
    await route.fulfill({ status: 204 });
  });
  await page.route("**/tests/fixtures/user-audit.html", route => route.fulfill({ contentType: "text/html", body: `
    <meta charset="utf-8"><main><section role="dialog" aria-label="新建安全连接">
    <label><span>设备名称</span><input id="name"></label>
    <label><span>登录密码</span><input id="password" type="password"></label>
    <textarea id="mcp-headers" aria-label="MCP 请求头"></textarea>
    <form class="terminal-clipboard-dialog"><textarea id="clipboard-draft"></textarea></form>
    <label><span>设备品牌</span><select id="brand" size="2"><option>华为</option><option>H3C</option></select></label>
    <button id="open">展开指令速查</button><button id="logout" data-audit-label="退出登录">运维管理员</button>
    <input id="automatic"><div class="xterm"><textarea id="terminal"></textarea></div>
    </section></main><script type="module">
      import { installUserAudit, recordUserAction, flushUserActions } from '/src/lib/userAudit.js';
      installUserAudit(); installUserAudit();
      window.auditTest = { recordUserAction, flushUserActions };
      document.querySelector('#logout').onclick = () => sessionStorage.clear();
    </script>` }));
  await page.goto(`${base}/tests/fixtures/user-audit.html`);
  await page.waitForFunction(() => window.auditTest);
  await delay(800);
  assert.equal(events.length, 0, "Initial render created user actions");
  await page.locator("#name").fill("核心交换机");
  await page.locator("#password").fill("test-only-password");
  await page.locator("#mcp-headers").fill('{"Authorization":"Bearer unfinished-header-secret');
  await page.locator("#clipboard-draft").fill("unmarked-clipboard-secret");
  await page.getByRole("option", { name: "H3C", exact: true }).click();
  await page.locator("#open").click();
  await delay(200);
  assert.ok(events.some(e => e.field === "name" && e.value === "核心交换机"));
  assert.ok(events.some(e => e.field === "password" && e.redacted));
  assert.ok(events.some(e => e.value === "H3C"), JSON.stringify({events, brand: await page.locator("#brand").inputValue()}));
  assert.ok(events.some(e => e.action === "ui_click" && e.label === "展开指令速查" && e.section === "新建安全连接"));
  assert.ok(events.some(e => e.field === "MCP 请求头" && e.redacted));
  for (const secret of ["test-only-password", "unfinished-header-secret", "unmarked-clipboard-secret"]) {
    assert.ok(!JSON.stringify(events).includes(secret));
  }

  const before = events.length;
  await page.evaluate(() => {
    const input = document.querySelector('#automatic'); input.value = 'automatic';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    document.querySelector('#open').click();
  });
  await page.locator("#terminal").fill("not-a-form-command");
  await delay(900);
  assert.equal(events.length, before, "Programmatic events or xterm keystrokes created UI actions");

  // Keyboard-selected completion uses the same explicit hook as mouse selection.
  await page.evaluate(() => {
    window.auditTest.recordUserAction('terminal_suggestion_applied', { field: 'command', value: 'display version', session_id: 'session1' });
    window.auditTest.flushUserActions();
  });
  await page.locator("#logout").click();
  await delay(250);
  assert.ok(events.some(e => e.action === "terminal_suggestion_applied" && e.value === "display version"));
  assert.ok(events.some(e => e.label === "退出登录"));
  assert.ok(tokens.every(token => token === "Bearer actor-token"), "Logout lost or replaced the audit actor");
  await page.evaluate(async () => {
    sessionStorage.setItem('auditbox_auth', JSON.stringify({ username:'alice',role:'admin',token:'expired-token' }));
    window.auditTest.recordUserAction('ui_click', { label:'expired-login-event' });
    await window.auditTest.flushUserActions();
    sessionStorage.setItem('auditbox_auth', JSON.stringify({ username:'bob',role:'admin',token:'new-login-token' }));
    window.auditTest.recordUserAction('ui_click', { label:'new-login-event' });
    await window.auditTest.flushUserActions();
  });
  assert.ok(events.some(e => e.label === 'new-login-event'), 'Expired login blocked the next account audit');
  assert.ok(!events.some(e => e.label === 'expired-login-event'));
  assert.deepEqual(errors, []);
  console.log("用户行为日志检查通过：点击、输入、密码脱敏、下拉选择、补全选用、退出、自动事件过滤。");
} finally {
  await browser?.close(); server.kill();
}
