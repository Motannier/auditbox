import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";
import { staleReviewUpdate } from "../src/lib/operationReview.js";

assert.ok(staleReviewUpdate({ review_revision: 1, status: "ready" }, { review_revision: 2, status: "queued" }));
assert.ok(staleReviewUpdate({ review_revision: 2, status: "queued" }, { review_revision: 2, status: "ready" }));
assert.ok(!staleReviewUpdate({ review_revision: 2, status: "ready" }, { review_revision: 2, status: "queued" }));

const webRoot = fileURLToPath(new URL("..", import.meta.url));
const url = "http://127.0.0.1:5201";
const server = spawn(process.execPath, [
  fileURLToPath(new URL("../node_modules/vite/bin/vite.js", import.meta.url)),
  "--host", "127.0.0.1", "--port", "5201", "--strictPort",
], { cwd: webRoot, stdio: "ignore" });
const executablePath = [process.env.CHROME_PATH,
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
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
  const page = await browser.newPage({ viewport: { width: 1280, height: 1050 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/api/**", route => route.abort());
  await page.route("**/__review-test", route => route.fulfill({
    contentType: "text/html", body: `<!doctype html><html><head><link rel="stylesheet" href="/src/styles/index.css"></head>
      <body><main id="review-root" style="width:300px;height:1000px;margin:10px"></main>
      <script type="module">import RefreshRuntime from '/@react-refresh';
        RefreshRuntime.injectIntoGlobalHook(window);
        window.$RefreshReg$ = () => {}; window.$RefreshSig$ = () => (type) => type;
        window.__vite_plugin_react_preamble_installed__ = true;
      </script></body></html>`,
  }));
  await page.goto(`${url}/__review-test`);
  await page.waitForFunction(() => window.__vite_plugin_react_preamble_installed__);
  await page.evaluate(async () => {
    const React = await import("/node_modules/.vite/deps/react.js");
    const ReactDOM = await import("/node_modules/.vite/deps/react-dom_client.js");
    const { createElement } = React.default || React;
    const { createRoot } = ReactDOM.default || ReactDOM;
    const { OperationReviewPanel } = await import("/src/components/operations/OperationReviewPanel.jsx");
    const root = createRoot(document.querySelector("#review-root"));
    window.renderReview = (review) => root.render(createElement(OperationReviewPanel, {
      review, target: { host: "192.0.2.1", remark: review.device_profile },
    }));
  });
  const base = {
    review_source: "rules", status: "ready", risk: "high", locked: false,
    current: { seq: 3, risk: "high", reason: "当前关闭接口" },
    reason: "潜在影响按规则判断；已取消、阻断或失败不代表已生效。", evidence: [1, 2],
    history: [
      { seq: 1, command: "shutdown", status: "cancelled" },
      { seq: 2, command: "unknown-command", status: "failed" },
      { seq: 3, command: "shutdown", status: "executed", authorized: true },
    ],
    findings: [
      { seq: 1, risk: "high", status: "cancelled", reason: "关闭接口影响连通状态", execution_note: "已取消，未下发" },
      { seq: 2, risk: "unknown", status: "failed", reason: "参数待核验", execution_note: "设备报错，未确认生效" },
      { seq: 3, risk: "high", status: "executed", reason: "关闭接口影响连通状态", execution_note: "已确认本次下发，旁路仅记录和展示" },
    ],
  };
  for (const profile of ["huawei_vrp", "cisco_ios", "zte_zxr10"]) {
    await page.evaluate(review => window.renderReview(review), { ...base, device_profile: profile });
    await page.getByText("规则旁路审计运行中", { exact: true }).waitFor();
    assert.match(await page.locator(".operation-review-status small").textContent(), /规则引擎/);
    assert.equal(await page.getByText(/专用 AI 审查待接入/).count(), 0);
    const rows = page.locator(".operation-history-item");
    assert.match(await rows.nth(0).textContent(), /已取消.*高风险/);
    assert.match(await rows.nth(1).textContent(), /设备报错.*待核验/);
    assert.match(await rows.nth(2).textContent(), /已授权执行.*高风险/);
    assert.equal(await page.getByRole("button", { name: "申请管理员授权" }).count(), 0);
    assert.equal(await page.locator(".review-process-steps").count(), 0);
    const overflow = await page.locator("#review-root").evaluate(root => {
      const bounds = root.getBoundingClientRect();
      return [...root.querySelectorAll("section,article,code,.operation-history-reason")].filter(el => {
        const rect = el.getBoundingClientRect();
        return rect.width && (rect.right > bounds.right + 1 || rect.left < bounds.left - 1);
      }).map(el => el.className || el.tagName);
    });
    assert.deepEqual(overflow, [], `${profile}: review panel overflow`);
  }
  for (const status of ["queued", "error"]) {
    await page.evaluate(review => window.renderReview(review), { ...base, device_profile: "cisco_ios", status, risk: "unknown" });
    await page.getByText(status === "queued" ? "规则旁路审计处理中" : "规则旁路审计暂时中断", { exact: true }).waitFor();
    assert.equal(await page.locator(".risk-badge").textContent(), "待核验");
    assert.equal(await page.locator(".review-process-steps").count(), status === "queued" ? 1 : 0);
  }
  for (const [profile, label] of [["h3c_comware", "H3C 专用模型"], ["linux", "本地模型"]]) {
    await page.evaluate(review => window.renderReview(review), { ...base, device_profile: profile, review_source: "model", findings: [], intent: "模型判断" });
    await page.getByText("实时安全审查运行中", { exact: true }).waitFor();
    assert.ok((await page.locator(".operation-review-status small").textContent()).includes(label));
    assert.equal(await page.locator(".operation-history-reason").count(), 0);
    assert.equal(await page.getByText("模型判断", { exact: true }).count(), 1);
  }
  for (const profile of ["linux", "h3c_comware", "huawei_vrp", "cisco_ios", "zte_zxr10"]) {
    const ruleBased = !["linux", "h3c_comware"].includes(profile);
    await page.evaluate(review => window.renderReview(review), {
      ...base, device_profile: profile, review_source: ruleBased ? "rules" : "model",
      status: "locked", lock: true, locked: true, risk: "critical", findings: [],
      current: { seq: 3, risk: "critical", reason: "当前命令的独立风险" },
      lock_policy: { allow_lock: true }, intent: "严重风险仍然展示", reason: "保留风险原因及历史证据",
    });
    await page.getByText("严重风险仍然展示", { exact: true }).waitFor();
    assert.equal(await page.locator(".risk-badge").textContent(), "严重风险");
    assert.match(await page.locator(".operation-context-review").textContent(), /保留风险原因及历史证据/);
    assert.doesNotMatch(await page.locator(".operation-history-item").first().textContent(), /严重风险/);
    assert.equal(await page.locator(".operation-lock-card, .operation-review-view.locked").count(), 0);
    assert.equal(await page.getByRole("button", { name: "申请管理员授权" }).count(), 0);
    assert.equal(await page.getByText(/已暂停终端操作|锁定依据/).count(), 0);
  }
  await page.evaluate(review => window.renderReview(review), {
    ...base, device_profile: "h3c_comware", review_source: "model", risk: "critical",
    current: { seq: 4, risk: "low", reason: "仅查询设备版本" },
    history: [...base.history, { seq: 4, command: "display version", status: "executed" }],
  });
  await page.getByText("仅查询设备版本", { exact: true }).waitFor();
  assert.equal(await page.locator(".risk-badge").textContent(), "低风险");
  assert.doesNotMatch(await page.locator(".operation-history-item").last().textContent(), /高风险|严重风险|风险依据/);
  assert.match(await page.locator(".operation-context-review").textContent(), /历史上下文.*严重风险/);
  await page.evaluate(review => window.renderReview(review), {
    ...base, device_profile: "linux", review_source: "model", current: null, findings: [],
  });
  await page.waitForFunction(() => document.querySelector(".risk-badge").textContent === "待判定");
  assert.equal(await page.locator(".operation-history-item.high, .operation-history-item.critical").count(), 0);
  if (process.env.WEBUI_REVIEW_SCREENSHOT) {
    await page.evaluate(review => window.renderReview(review), { ...base, device_profile: "zte_zxr10" });
    await page.getByText("规则旁路审计运行中", { exact: true }).waitFor();
    await page.locator("#review-root").screenshot({ path: process.env.WEBUI_REVIEW_SCREENSHOT });
  }
  assert.deepEqual(errors, []);
  console.log("Review UI passed: three rule brands, H3C/Linux model labels, per-command evidence, pending/error states, 300px layout, legacy critical reviews never lock.");
} finally {
  if (browser) await browser.close();
  server.kill();
}
