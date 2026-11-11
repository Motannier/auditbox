import assert from "node:assert/strict";

export async function checkConsolePorts(browser, url) {
  const context = await browser.newContext();
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.clock.install();
  await page.addInitScript(() => {
    const originalFetch = window.fetch;
    window.consolePortRequests = [];
    window.fetch = (input, options = {}) => {
      if (!String(input).endsWith("/api/console/ports")) return originalFetch(input, options);
      return new Promise((resolve, reject) => {
        const request = {
          signal: options.signal,
          ignoreAbort: false,
          respond: (body, status = 200) => resolve(new Response(JSON.stringify(body), {
            status, headers: { "Content-Type": "application/json" },
          })),
          fail: () => reject(new TypeError("Failed to fetch")),
        };
        options.signal?.addEventListener("abort", () => {
          if (!request.ignoreAbort) reject(new DOMException("Aborted", "AbortError"));
        });
        window.consolePortRequests.push(request);
      });
    };
  });
  const adapter = { path: "/dev/ttyUSB0", label: "FT232R USB UART" };
  const otherAdapter = { path: "/dev/ttyUSB1", label: "USB Console 2" };
  const visit = async (value = "") => {
    await page.goto(new URL(`/tests/fixtures/console-ports.html?value=${encodeURIComponent(value)}`, url).href);
    await count(1);
  };
  const count = (expected) => page.waitForFunction((n) => window.consolePortRequests?.length === n, expected);
  const respond = (index, body, status = 200) => page.evaluate(({ index, body, status }) => {
    window.consolePortRequests[index].respond(body, status);
  }, { index, body, status });
  const poll = () => page.clock.runFor(2000);
  const refresh = () => page.getByRole("button", { name: "重新检测 Console 设备", exact: true }).click();
  const options = () => page.locator("select option").allTextContents();
  const settled = () => page.waitForFunction(() => !document.querySelector(".console-port-refresh").disabled);
  try {
    // A failed first scan is unknown, not proof that a saved adapter was unplugged.
    await visit(adapter.path);
    await respond(0, { detail: "检测服务暂不可用" }, 503);
    await page.getByRole("alert").filter({ hasText: "检测服务暂不可用" }).waitFor();
    assert.deepEqual(await options(), ["Console 设备检测失败", "已保存设备（待检测）"]);
    await refresh(); await count(2); await respond(1, [adapter]); await settled();
    assert.equal(await page.getByRole("alert").count(), 0);
    assert.equal(await page.locator("select").inputValue(), adapter.path);

    // Background failures preserve known ports/selection; a successful empty scan removes them.
    await poll(); await count(3); await respond(2, {}, 500);
    await page.getByRole("alert").filter({ hasText: "已保留上次检测结果" }).waitFor();
    assert.ok((await options()).includes(adapter.label));
    assert.equal(await page.locator("select").inputValue(), adapter.path);
    await poll(); await count(4); await respond(3, []);
    await page.waitForFunction(() => document.querySelector("select option")?.textContent === "未检测到 Console 设备");
    assert.deepEqual(await options(), ["未检测到 Console 设备", "已保存设备（当前未连接）"]);
    assert.equal(await page.getByRole("alert").count(), 0);

    // Even an already-parsing response that ignores abort must not overwrite a newer scan.
    await poll(); await count(5);
    await page.evaluate(() => { window.consolePortRequests[4].ignoreAbort = true; });
    await poll();
    assert.equal(await page.evaluate(() => window.consolePortRequests.length), 5, "Polling overlapped a pending scan");
    await refresh(); await count(6);
    assert.equal(await page.evaluate(() => window.consolePortRequests[4].signal.aborted), true);
    await respond(5, [otherAdapter]); await settled();
    await respond(4, []);
    await page.waitForTimeout(50);
    assert.ok((await options()).includes(otherAdapter.label), "An old empty response removed a newer adapter");
    assert.equal(await page.locator("select").inputValue(), adapter.path, "A scan changed the user's saved selection");
    await poll(); await count(7);
    await page.evaluate(() => { window.consolePortRequests[6].ignoreAbort = true; });
    await refresh(); await count(8); await respond(7, [otherAdapter]); await settled();
    await page.evaluate(() => window.consolePortRequests[6].fail());
    await page.waitForTimeout(50);
    assert.equal(await page.getByRole("alert").count(), 0, "A superseded failure replaced the newer success");

    // A slow scan survives polling ticks, but a hung one times out and remains retryable.
    await visit();
    await poll();
    assert.equal(await page.evaluate(() => window.consolePortRequests.length), 1);
    await respond(0, [adapter]); await settled();
    assert.equal(await page.locator("select").inputValue(), adapter.path);
    assert.equal(await page.getByTestId("changes").textContent(), "1");
    await visit();
    await page.clock.runFor(10_100);
    await page.getByRole("alert").filter({ hasText: "检测超时" }).waitFor();
    assert.equal(await page.locator("select option").first().textContent(), "Console 设备检测失败");
    await refresh();
    const retryIndex = await page.evaluate(() => window.consolePortRequests.length - 1);
    await respond(retryIndex, [adapter]); await settled();
    assert.equal(await page.getByRole("alert").count(), 0);

    // Closing the dialog cancels detection and prevents late auto-selection.
    await visit();
    await page.evaluate(() => { window.consolePortRequests[0].ignoreAbort = true; });
    await page.getByRole("button", { name: "切换检测窗口", exact: true }).click();
    assert.equal(await page.evaluate(() => window.consolePortRequests[0].signal.aborted), true);
    await respond(0, [adapter]); await page.waitForTimeout(50);
    assert.equal(await page.getByTestId("changes").textContent(), "0");
    await page.getByRole("button", { name: "切换检测窗口", exact: true }).click();
    await count(2); await respond(1, [adapter]); await settled();
    assert.equal(await page.getByTestId("changes").textContent(), "1");

    await visit();
    await respond(0, { unexpected: "not a port list" });
    await page.getByRole("alert").filter({ hasText: "检测接口返回异常" }).waitFor();
    assert.deepEqual(errors, []);
    console.log("Console ports passed: error/empty distinction, retained selection, serialized polling, stale response guards, timeout/retry, unmount and malformed response");
  } finally {
    await context.close();
  }
}
