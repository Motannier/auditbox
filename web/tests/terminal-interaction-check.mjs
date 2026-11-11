import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { spawn } from "node:child_process";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const port = 5220;
const base = `http://127.0.0.1:${port}`;
const server = spawn(process.execPath, [fileURLToPath(new URL("../node_modules/vite/bin/vite.js", import.meta.url)),
  "--host", "127.0.0.1", "--port", String(port), "--strictPort"], {
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
    assert.ok(n < 100, "Vite did not start");
    await delay(100);
  }
  browser = await chromium.launch({ headless: true, executablePath });
  const page = await browser.newPage({ viewport: { width: 1100, height: 850 } });
  page.setDefaultTimeout(5000);
  const errors = [], sockets = new Map(), inputs = [];
  page.on("pageerror", e => errors.push(e.message));
  let requests = 0, slow = false, version = 1;
  await page.route("**/api/**", async route => {
    requests++;
    if (slow) await delay(250);
    await route.fulfill({ contentType: "application/json", body: JSON.stringify([
      { command: "display version", source: "completion", risk: "normal" },
    ]) });
  });
  await page.routeWebSocket("**/ws/clipboard/**", socket => {
    const id = Number(new URL(socket.url()).pathname.split("/").at(-1));
    sockets.set(id, socket);
    socket.onMessage(data => inputs.push(String(data)));
    socket.send(JSON.stringify({ type: "handshake", status: "ok", session_id: `state-${id}` }));
    socket.send(JSON.stringify({ type: "terminal_state", mode: "command", version: 1 }));
    socket.send("\r\n<HUAWEI>");
  });
  await page.goto(`${base}/tests/fixtures/terminal-clipboard.html`);
  const panel = page.locator(".terminal-session.active");
  const focus = () => panel.locator(".xterm-helper-textarea").focus();
  const suggestions = panel.locator(".command-suggestions");
  const send = event => sockets.get(0).send(JSON.stringify(event));
  const state = (mode, row) => {
    send({ type: "terminal_state", mode, version: ++version });
    if (row) sockets.get(0).send(`\r\n${row}`);
  };
  await panel.getByRole("button", { name: "粘贴到终端", exact: true }).waitFor();
  await delay(200); await focus();
  await page.keyboard.type("di");
  await suggestions.waitFor();
  assert.ok(requests > 0, "Ready prompt did not enable completion");

  // Entering authentication must invalidate a completion already in flight.
  await page.keyboard.press("Control+c");
  slow = true;
  await page.keyboard.type("dis");
  await delay(170);
  state("auth", "Username: ");
  await delay(400);
  assert.equal(await suggestions.count(), 0, "Late completion appeared during login");
  const before = requests;
  await page.keyboard.type("admin"); await page.keyboard.press("Enter");
  send({ type: "recommendation", suggestions: ["display version"], input_source: "human" });
  send({ type: "terminal_state", mode: "command", version: version - 1 });
  await delay(250);
  assert.equal(requests, before);
  assert.equal(await suggestions.count(), 0, "Stale state or recommendation reopened login popup");
  assert.ok(inputs.join("").includes("admin\r"));
  assert.equal(await page.getByRole("dialog").count(), 0);

  for (const [mode, row, text] of [["secret", "Password: ", "private"],
    ["pager", "---- More ----", "qq"], ["interaction", "Continue? [Y/N]: ", "y"]]) {
    state(mode, row); await delay(60); await focus();
    await page.keyboard.type(text); await page.keyboard.press("Enter");
    await delay(180);
    assert.equal(requests, before, `${mode} requested completion`);
    assert.equal(await suggestions.count(), 0);
  }

  slow = false;
  state("command", "<HUAWEI>"); await delay(80); await focus();
  await page.keyboard.type("di"); await suggestions.waitFor();
  await page.keyboard.press("Control+c");
  await page.keyboard.type("display version");
  await page.keyboard.press("Escape"); await page.keyboard.press("Enter");
  state("running");
  send({ type: "recommendation", suggestions: ["display version"], input_source: "human" });
  await delay(100);
  assert.equal(await suggestions.count(), 0, "Recommendation appeared before device was ready");
  state("command", "<HUAWEI>"); await suggestions.waitFor();

  state("confirmation");
  send({ type: "alert", action: "confirm", level: "danger", command: "reboot", confirmation_id: "one-time", reason: "重启设备" });
  await page.getByRole("dialog").waitFor();
  state("running"); await delay(80);
  assert.equal(await page.getByRole("dialog").count(), 1, "State update hid a valid confirmation");
  await page.getByRole("button", { name: "取消操作", exact: true }).click();
  assert.ok(inputs.some(s => s.includes("command_confirmation_cancel")));
  assert.deepEqual(errors, []);
  console.log("Terminal interaction checks passed: login, paging, stale responses, readiness and confirmation lifetime.");
} finally {
  await browser?.close();
  server.kill("SIGTERM");
}
