import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import net from "node:net";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";
import { checkClientDeviceFlow } from "./client-device-ui-check.mjs";

const port = Number(process.env.WEBUI_PREFLIGHT_TEST_PORT || 5198);
const url = `http://127.0.0.1:${port}/console`;

function chromeExecutable() {
  const candidates = [
    process.env.CHROME_PATH,
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
  ].filter(Boolean);
  return candidates.find((candidate) => existsSync(candidate));
}

async function waitForPort(portNumber, timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const ready = await new Promise((resolve) => {
      const socket = net.createConnection({ host: "127.0.0.1", port: portNumber });
      socket.once("connect", () => {
        socket.destroy();
        resolve(true);
      });
      socket.once("error", () => resolve(false));
    });
    if (ready) return;
    await delay(100);
  }
  throw new Error(`Timed out waiting for ${url}`);
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
  browser = await chromium.launch({ headless: true, executablePath: chromeExecutable() });
  await checkClientDeviceFlow(browser, url);
} finally {
  if (browser) await browser.close();
  server.kill();
}
