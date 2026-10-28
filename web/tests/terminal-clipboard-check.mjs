import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";
import { normalizeTerminalPaste, terminalClipboardShortcut } from "../src/lib/terminalClipboard.js";

assert.equal(normalizeTerminalPaste("display version\r\n"), "display version");
assert.equal(normalizeTerminalPaste("a\rb\r\n"), "a\nb");
assert.equal(normalizeTerminalPaste("\x1b[31m中文\x1b[0m\t命令\x03\x15\x1b[200~"), "中文    命令");
assert.equal(normalizeTerminalPaste("\x1b]52;c;c2VjcmV0\x07ls\n"), "ls");
assert.throws(() => normalizeTerminalPaste("a".repeat(65537)), /过长/);
const key = (value, fields = {}) => ({ type: "keydown", key: value, ...fields });
assert.equal(terminalClipboardShortcut(key("c", { ctrlKey: true }), false), null);
assert.equal(terminalClipboardShortcut(key("c", { ctrlKey: true }), true), "copy");
assert.equal(terminalClipboardShortcut(key("v", { ctrlKey: true, shiftKey: true })), "paste");
assert.equal(terminalClipboardShortcut(key("v", { metaKey: true })), "paste");
assert.equal(terminalClipboardShortcut(key("Insert", { shiftKey: true })), "paste");
assert.equal(terminalClipboardShortcut(key("c", { ctrlKey: true, altKey: true }), true), null);

const port = 5216;
const url = `http://127.0.0.1:${port}`;
const server = spawn(process.execPath, [fileURLToPath(new URL("../node_modules/vite/bin/vite.js", import.meta.url)),
  "--host", "127.0.0.1", "--port", String(port), "--strictPort"], {
  cwd: fileURLToPath(new URL("..", import.meta.url)), stdio: "ignore",
});
const executablePath = [process.env.CHROME_PATH, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/usr/bin/chromium", "/usr/bin/google-chrome", "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
].filter(Boolean).find(existsSync);
let browser;

try {
  for (let attempt = 0; ; attempt++) {
    try { if ((await fetch(url)).ok) break; } catch { /* Wait for Vite. */ }
    assert.ok(attempt < 100, "Frontend did not start");
    await delay(100);
  }
  browser = await chromium.launch({ headless: true, executablePath });
  const context = await browser.newContext({ viewport: { width: 1000, height: 760 } });
  // Simulate the board's HTTP clipboard restrictions, not localhost privileges.
  await context.addInitScript(() => {
    window.copiedText = "";
    window.nativeClipboard = navigator.clipboard;
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
    document.execCommand = (command) => {
      if (command === "copy") window.copiedText = document.activeElement.value;
      return true;
    };
  });
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const sockets = new Map();
  const inputs = [[], []];
  const commands = [[], []];
  const executed = [[], []];
  const buffers = ["", ""];
  const cursors = [0, 0];
  const auditSources = [];
  const pending = new Map();
  let confirmations = 0;
  await page.route("**/api/**", route => route.fulfill({ contentType: "application/json", body: "[]" }));
  await page.routeWebSocket("**/ws/clipboard/**", socket => {
    const index = Number(new URL(socket.url()).pathname.split("/").at(-1));
    sockets.set(index, socket);
    buffers[index] = "";
    cursors[index] = 0;
    socket.onMessage(message => {
      let data = String(message);
      let source = "human";
      if (data.startsWith("{")) {
        const event = JSON.parse(data);
        if (event.type === "guide_audit_event") return;
        if (event.type === "command_confirmation" || event.type === "command_confirmation_cancel") {
          if (event.type === "command_confirmation") { confirmations++; executed[index].push(pending.get(index)); }
          pending.delete(index);
          socket.send("\r\nSwitch# ");
          return;
        }
        if (event.type !== "terminal_input") return;
        data = event.data;
        source = event.source;
      }
      inputs[index].push(data);
      if (pending.has(index)) throw new Error("Input bypassed pending confirmation");
      auditSources.push(source);
      for (let offset = 0; offset < data.length; offset++) {
        if (data.startsWith("\x1b[F", offset)) { cursors[index] = buffers[index].length; offset += 2; continue; }
        if (data.startsWith("\x1b[D", offset)) { cursors[index] = Math.max(0, cursors[index] - 1); offset += 2; continue; }
        const character = data[offset];
        if (character === "\x15" || character === "\x18" || character === "\x03") {
          buffers[index] = ""; cursors[index] = 0; socket.send("\r\nSwitch# "); continue;
        }
        if (character === "\x7f") {
          const cursor = cursors[index];
          if (cursor > 0) { buffers[index] = buffers[index].slice(0, cursor - 1) + buffers[index].slice(cursor); cursors[index]--; }
          continue;
        }
        if (character === "\r" || character === "\n") {
          const command = buffers[index];
          buffers[index] = ""; cursors[index] = 0;
          commands[index].push(command);
          if (command === "sudo reboot") {
            pending.set(index, command);
            socket.send(JSON.stringify({ type: "alert", action: "confirm", level: "danger", command,
              reason: "重启设备", confirmation_id: "clipboard-danger" }));
          } else {
            executed[index].push(command);
            socket.send(`\r\n${command} result\r\nSwitch# `);
          }
          continue;
        }
        buffers[index] = buffers[index].slice(0, cursors[index]) + character + buffers[index].slice(cursors[index]);
        cursors[index]++;
      }
      socket.send(data);
    });
    socket.send(JSON.stringify({ type: "handshake", status: "ok", session_id: `clipboard-${index}` }));
    socket.send("\r\ncopy-this-terminal-output\r\nSwitch# ");
  });
  const panel = () => page.locator(".terminal-session.active");
  const focus = () => panel().locator(".xterm-helper-textarea").focus();
  const paste = (text) => panel().locator(".xterm-helper-textarea").evaluate((element, value) => {
    element.focus();
    const clipboardData = new DataTransfer();
    clipboardData.setData("text/plain", value);
    element.dispatchEvent(new ClipboardEvent("paste", { bubbles: true, cancelable: true, clipboardData }));
  }, text);
  const waitBuffer = async (index, expected) => {
    for (let attempt = 0; attempt < 100 && buffers[index] !== expected; attempt++) await delay(20);
    assert.equal(buffers[index], expected);
  };
  await page.goto(`${url}/tests/fixtures/terminal-clipboard.html`);
  await panel().getByRole("button", { name: "粘贴到终端", exact: true }).waitFor();
  await page.waitForFunction(() => !document.querySelector(".terminal-session.active [aria-label='粘贴到终端']").disabled);
  await focus();
  await paste("echo copied\r\n");
  await waitBuffer(0, "echo copied");
  assert.equal(commands[0].length, 0, "Pasting a final newline executed a command");
  assert.equal(inputs[0].filter(input => input === "echo copied").length, 1, "xterm pasted twice");
  await page.keyboard.press("Enter");
  await delay(100);
  assert.deepEqual(commands[0], ["echo copied"]);

  // Normal paste inserts at the remote cursor, not at the start/end unconditionally.
  await paste("echo ac");
  await page.keyboard.press("ArrowLeft");
  await paste("b");
  await waitBuffer(0, "echo abc");
  await page.keyboard.press("Control+c");
  await waitBuffer(0, "");
  assert.equal(inputs[0].at(-1), "\x03", "Ctrl+C without a selection did not interrupt");

  await panel().getByRole("button", { name: "粘贴到终端", exact: true }).click();
  let dialog = page.getByRole("dialog", { name: "粘贴到终端", exact: true });
  await dialog.getByLabel("待粘贴内容").fill("display version\n");
  await dialog.getByRole("button", { name: "填入终端", exact: true }).click();
  await waitBuffer(0, "display version");
  assert.equal(commands[0].length, 1);
  await focus(); await page.keyboard.press("Control+c"); await waitBuffer(0, "");

  await paste("echo first\nsudo reboot\necho last\n");
  dialog = page.getByRole("dialog", { name: "粘贴多行命令" });
  await dialog.waitFor();
  assert.equal(buffers[0], "", "Multiline preview sent text before approval");
  await dialog.getByRole("button", { name: "按行填入" }).click();
  await waitBuffer(0, "echo first");
  assert.equal(commands[0].length, 1);
  await focus();
  await page.keyboard.press("Enter");
  await waitBuffer(0, "sudo reboot");
  assert.deepEqual(executed[0], ["echo copied", "echo first"]);
  await focus();
  await page.keyboard.press("Enter");
  const danger = page.getByRole("dialog", { name: "高危操作确认" });
  await danger.waitFor();
  assert.ok(await panel().getByRole("button", { name: "粘贴到终端", exact: true }).isDisabled());
  const beforeDangerPaste = inputs[0].length;
  await paste("echo bypass\n");
  assert.equal(inputs[0].length, beforeDangerPaste, "Paste bypassed danger confirmation");
  await danger.getByRole("button", { name: "取消操作" }).click();
  await delay(100);
  assert.equal(buffers[0], "", "Cancelling danger continued the paste queue");
  assert.equal(confirmations, 0);
  assert.ok(!executed[0].includes("echo last"));
  assert.ok(auditSources.every(source => source === "human"), "Paste was misattributed to guide/AI");

  // Repeat, accept the dangerous command, but require another Enter for line 2.
  await paste("sudo reboot\necho last");
  await page.getByRole("dialog", { name: "粘贴多行命令" }).getByRole("button", { name: "按行填入" }).click();
  await waitBuffer(0, "sudo reboot");
  await focus();
  await page.keyboard.press("Enter");
  await danger.getByRole("button", { name: "再次确认执行" }).click();
  await waitBuffer(0, "echo last");
  assert.equal(confirmations, 1);
  assert.ok(!executed[0].includes("echo last"));
  await panel().getByRole("button", { name: "停止填入" }).click();
  await waitBuffer(0, "");

  // Right click uses the same HTTP fallback as the toolbar.
  await panel().locator(".xterm-host").click({ button: "right", position: { x: 80, y: 80 } });
  await page.getByRole("menu", { name: "终端剪贴板" }).getByRole("menuitem", { name: "粘贴", exact: true }).click();
  await page.getByRole("dialog", { name: "粘贴到终端", exact: true }).getByRole("button", { name: "取消", exact: true }).click();
  await paste("\x1b[31mecho safe\x1b[0m\tinput\x03\x15\n");
  await waitBuffer(0, "echo safe    input");
  await focus(); await page.keyboard.press("Control+c"); await waitBuffer(0, "");

  // Select actual xterm output, then copy via HTTP execCommand fallback.
  const row = panel().locator(".xterm-rows > div").filter({ hasText: "copy-this-terminal-output" }).first();
  await row.waitFor();
  const rect = await row.boundingBox();
  await page.mouse.move(rect.x + 1, rect.y + rect.height / 2);
  await page.mouse.down();
  await page.mouse.move(rect.x + 190, rect.y + rect.height / 2, { steps: 8 });
  await page.mouse.up();
  const copy = panel().getByRole("button", { name: "复制终端选中文本" });
  assert.ok(await copy.isEnabled());
  const beforeCopy = inputs[0].length;
  await copy.click();
  await page.waitForFunction(() => window.copiedText.includes("copy-this-terminal"));
  assert.equal(inputs[0].length, beforeCopy, "Copy sent terminal input");
  await focus();
  await page.keyboard.press("Control+c");
  await delay(60);
  assert.equal(inputs[0].length, beforeCopy, "Ctrl+C with a selection sent interrupt");

  // Clipboard API permission denial and stale async reads never paste to a new session.
  await page.evaluate(() => Object.defineProperty(navigator, "clipboard", { configurable: true, value: {
    readText: () => new Promise(resolve => { window.releaseClipboard = resolve; }),
  } }));
  await panel().getByRole("button", { name: "粘贴到终端", exact: true }).click();
  await page.evaluate(() => window.clipboardFixture.switchSession(1));
  await page.locator(".terminal-session").nth(1).locator(".terminal-clipboard-toolbar").waitFor({ state: "visible" });
  await page.evaluate(() => window.releaseClipboard("echo stale"));
  await delay(100);
  assert.equal(buffers[0], ""); assert.equal(buffers[1], "");

  // On Comware, replacing and stopping multiline input clears native text with Ctrl+X.
  await paste("old");
  await waitBuffer(1, "old");
  await paste("display version\ndisplay clock");
  await page.getByRole("dialog", { name: "粘贴多行命令" }).getByRole("button", { name: "按行填入" }).click();
  await waitBuffer(1, "display version");
  assert.ok(inputs[1].includes("\x1b[F\x18"));
  await panel().getByRole("button", { name: "停止填入" }).click();
  await waitBuffer(1, "");
  assert.deepEqual(commands[1], []);
  await page.evaluate(() => Object.defineProperty(navigator, "clipboard", { configurable: true,
    value: { readText: () => Promise.reject(new Error("denied")) } }));
  await panel().getByRole("button", { name: "粘贴到终端", exact: true }).click();
  await page.getByRole("dialog", { name: "粘贴到终端", exact: true }).getByRole("button", { name: "取消", exact: true }).click();

  // Reconnect invalidates an async clipboard read even if the same tab remains active.
  await page.evaluate(() => Object.defineProperty(navigator, "clipboard", { configurable: true, value: {
    readText: () => new Promise(resolve => { window.releaseClipboard = resolve; }),
  } }));
  await panel().getByRole("button", { name: "粘贴到终端", exact: true }).click();
  await page.evaluate(() => window.clipboardFixture.reconnect());
  await delay(100);
  await page.evaluate(() => window.releaseClipboard("echo stale reconnect"));
  await delay(100);
  assert.equal(buffers[1], "");
  // Exercise a real browser paste shortcut, not just synthetic paste events.
  await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin: url });
  await page.evaluate(async () => {
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: window.nativeClipboard });
    await navigator.clipboard.writeText("display clock\n");
  });
  await focus();
  await page.keyboard.press(process.platform === "darwin" ? "Meta+v" : "Control+v");
  await waitBuffer(1, "display clock");
  assert.deepEqual(commands[1], [], "Native paste shortcut executed the copied final newline");
  await page.keyboard.press("Control+c"); await waitBuffer(1, "");
  await panel().getByRole("button", { name: "粘贴到终端", exact: true }).click();
  await waitBuffer(1, "display clock");
  await focus(); await page.keyboard.press("Control+c"); await waitBuffer(1, "");

  // Sensitive input remains concealed; reject multiline password paste entirely.
  sockets.get(1).send("\r\nPassword: ");
  await delay(80);
  const beforePassword = inputs[1].length;
  await paste("first-secret\nsecond-secret");
  await delay(50);
  assert.equal(inputs[1].length, beforePassword);
  assert.equal(await page.getByRole("dialog").count(), 0, "Password leaked into multiline preview");
  await paste("private-password\n");
  await waitBuffer(1, "private-password");
  await page.keyboard.press("Control+c"); await waitBuffer(1, "");

  // If neither clipboard API nor execCommand is allowed, keep manual copy usable.
  await page.evaluate(() => {
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
    document.execCommand = () => false;
  });
  const newRow = panel().locator(".xterm-rows > div").filter({ hasText: "copy-this-terminal-output" }).first();
  const newRect = await newRow.boundingBox();
  await page.mouse.move(newRect.x + 1, newRect.y + newRect.height / 2);
  await page.mouse.down();
  await page.mouse.move(newRect.x + 190, newRect.y + newRect.height / 2, { steps: 8 });
  await page.mouse.up();
  await panel().getByRole("button", { name: "复制终端选中文本" }).click();
  const manualCopy = page.getByRole("dialog", { name: "复制终端内容" });
  await manualCopy.waitFor();
  assert.ok((await manualCopy.getByLabel("待复制内容").inputValue()).includes("copy-this-terminal"));
  await manualCopy.getByRole("button", { name: "关闭", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 700 });
  await delay(100);
  const overflow = await panel().locator(".terminal-clipboard-toolbar").evaluate(element => element.scrollWidth > element.clientWidth);
  assert.equal(overflow, false, "Clipboard toolbar overflowed on narrow terminal");
  if (process.env.CLIPBOARD_SCREENSHOT) await page.screenshot({ path: process.env.CLIPBOARD_SCREENSHOT });
  assert.deepEqual(errors, []);
  console.log("Terminal clipboard regression passed: HTTP fallback, copy/interrupt, insert, safe multiline, confirmation, H3C clearing, session isolation and layout.");
} finally {
  await browser?.close();
  server.kill("SIGTERM");
}
