import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";
import { GUIDE_VARIANT_FIELD, packageGuideCatalog, packageGuideCounts, buildPackageGuide, packageGuideTree,
  packageGuideScenarios, mergedPonCatalog, guideDispatchBlockReason, resolvePackageGuideScenario,
  selectPackageGuideVariant } from "../src/lib/guide/packageGuide.js";

const vendors = ["fiberhome", "huawei", "raisecom", "zte"];
const profileIds = vendors.flatMap((v) => [v + "_olt", v + "_onu"]);
function fixture(profile) {
  const viewOnly = ["fiberhome_onu", "raisecom_onu"].includes(profile);
  const form = (id, template, risk = "read_only", documentation_only = false) => ({
    id: profile + "-" + id, template, syntax: documentation_only ? "show interface [<port>]" : "",
    risk, risk_reason: risk === "read_only" ? "" : "改变设备状态", purpose: "手册中记录的设备状态",
    parameters: documentation_only || !template.includes("<") ? [] : [{ key: "parameter0", label: "端口", slot: "<port>", validation: "integer" }],
    views: ["documented view"], models: [profile + " series"], evidence: [{ source_id: "manual", pages: [2] }],
    fill_only: true, documentation_only, interface: viewOnly ? "Web" : "CLI",
    steps: viewOnly ? ["进入手册所述 Web 页面查看。"] : [],
  });
  const forms = [form("all", "show interface"), form("port", "show interface <port>", "ordinary_change"),
      { ...form("reference", "show pon <port>", "pending"), parameters: [{ key: "parameter0", label: "查看范围", slot: "<port>",
        validation: "enum", options: [{ value: "all", label: "全部" }, { value: "detail", label: "详情" }] }] }];
  const scenario = { ...forms[0], id: profile + "-inspect", behavior_key: "interface-state", title: "查看接口状态",
    summary: "文档中的常用操作", variants: forms };
  const port = { ...forms[1], id: profile + "-port-detail", behavior_key: "interface-detail", title: "指定端口详情",
    summary: "查看指定端口", variants: [forms[1]] };
  const reference = { ...forms[2], id: profile + "-manual", behavior_key: "manual", title: "操作说明",
    summary: "手册参考", variants: [forms[2]] };
  return { schema: "auditbox-command-guide/1", profile_id: profile, label: profile,
    device_type: profile.endsWith("_olt") ? "olt" : "onu", fill_only: true, available: !viewOnly,
    model_summary: profile + " series", message: viewOnly ? "只有 Web/TL1 操作资料" : "",
    categories: viewOnly ? [] : [{ id: "guide-" + profile + "-interface", key: "interface", label: "端口与链路", summary: "端口检查",
      scenarios: [scenario, port, reference] }] };
}
function valueFor(p) {
  if (p.validation === "enum") return p.options[0].value;
  if (p.validation === "position") return p.formats[0].prefix + "1" + p.formats[0].separators.map((s) => s + "1").join("");
  if (p.validation === "ipv4") return "192.0.2.1";
  return String(p.minimum ?? 1);
}
const data = process.env.GUIDE_DATA_PATH ? JSON.parse(readFileSync(process.env.GUIDE_DATA_PATH, "utf8"))
  : Object.fromEntries(profileIds.map((p) => [p, fixture(p)]));
const states = Object.fromEntries(profileIds.map((p) => [p, { catalog: packageGuideCatalog(data[p], p) }]));
const types = ["olt", "onu"].map((type) => ({ value: type, label: type.toUpperCase(), profiles: profileIds
  .filter((p) => p.endsWith("_" + type)).map((value) => ({ value, label: data[value].label, guideSource: "package" })) }));
const catalogFor = (profile) => mergedPonCatalog(profile.split("_")[0], states, profileIds.filter((p) => p.split("_")[0] === profile.split("_")[0]));
const countsData = { schema: "auditbox-command-guide-counts/1",
  profiles: profileIds.map((p) => ({ profile_id: p, count: states[p].catalog.scenarios.length })),
  brands: vendors.map((v) => ({ id: "pon-" + v, label: v, profile_ids: [v + "_olt", v + "_onu"],
    model_summary: catalogFor(v + "_olt").modelSummary || v + " series", count: catalogFor(v + "_olt").scenarios.length })) };
const counts = packageGuideCounts(countsData);
const totalCount = countsData.brands.reduce((sum, b) => sum + b.count, 0);
for (const invalid of [null, { ...countsData, schema: "wrong" }, { ...countsData, profiles: null },
  { ...countsData, profiles: [countsData.profiles[0], countsData.profiles[0]] },
  { ...countsData, brands: [{ ...countsData.brands[0], profile_ids: ["linux"] }] }]) assert.throws(() => packageGuideCounts(invalid));
for (const profile of profileIds) {
  const catalog = states[profile].catalog;
  assert.throws(() => packageGuideCatalog(data[profile], "linux"));
  for (const scenario of catalog.scenarios) for (const variant of scenario.variants || [scenario]) {
    const values = { [GUIDE_VARIANT_FIELD]: variant.id, ...Object.fromEntries(variant.parameters.map((p) => [p.key, valueFor(p)])) };
    const selected = resolvePackageGuideScenario(scenario, values);
    assert.equal(selected.deviceProfile, profile);
    assert.equal(selected.deviceType, data[profile].device_type);
    assert.equal(selected.risk, variant.risk);
    assert.ok(selected.fields.every((field) => field.kind !== "select" || field.validation === "enum"));
    assert.equal(scenario.variantField, null);
    assert.equal(buildPackageGuide(scenario, values).commands.length, variant.documentation_only ? 0 : 1);
    assert.equal(Boolean(guideDispatchBlockReason(selected, { status: "connected", deviceProfile: profile, deviceType: data[profile].device_type }, true)), variant.documentation_only);
    assert.ok(guideDispatchBlockReason(selected, { status: "connected", deviceProfile: "linux", deviceType: "server" }, true));
    for (const param of variant.parameters) for (const bad of ["", "1;reload", "1\nreload", "1\rreload", "1 | shutdown", "$(x)", "<id>", "1 2", " 1 "]) {
      assert.deepEqual(buildPackageGuide(scenario, { ...values, [param.key]: bad }).commands, []);
    }
    assert.equal(resolvePackageGuideScenario(scenario, { [GUIDE_VARIANT_FIELD]: "missing" }), null);
  }
}
const malicious = fixture("huawei_olt");
Object.assign(malicious.categories[0].scenarios[0].variants[1], { deviceProfile: "linux", deviceType: "server", runtime: false, fields: [] });
const owned = packageGuideCatalog(malicious, "huawei_olt").scenarios[0];
assert.equal(resolvePackageGuideScenario(owned, { [GUIDE_VARIANT_FIELD]: owned.variants[1].id }).deviceProfile, "huawei_olt");
malicious.categories[0].scenarios[0].variants[2].template = "";
assert.throws(() => packageGuideCatalog(malicious, "huawei_olt"));
for (const validation of ["enum", "position", "regex"] ) {
  const bad = fixture("huawei_olt");
  bad.categories[0].scenarios[0].variants[1].parameters[0].validation = validation;
  assert.throws(() => packageGuideCatalog(bad, "huawei_olt"));
}
for (const state of [{}, states]) {
  const tree = packageGuideTree(state, types);
  assert.equal(tree.length, 1);
  assert.equal(tree[0].label, "OLT/ONU运维");
  assert.equal(tree[0].branches.length, 4);
  assert.deepEqual(new Set(tree[0].branches.flatMap((b) => b.packageProfiles)), new Set(profileIds));
  assert.ok(tree[0].branches.every((b) => b.summary && !b.summary.includes("按常用操作")));
}
const merged = packageGuideScenarios(states, types);
assert.equal(new Set(merged.map((s) => s.id)).size, merged.length);
const serverTypes = [{ value: "server", profiles: [{ value: "linux" }] }];
assert.deepEqual(packageGuideTree(states, serverTypes), []);
assert.equal(packageGuideScenarios(states, serverTypes).length, 0, "Cached PON scenarios must respect the selected device types");
const customTypes = [{ value: "firewall", label: "防火墙", profiles: [{ value: "custom_firewall", label: "自定义防火墙", guideSource: "package" }] }];
const customScenario = { id: "custom-firewall-state" };
const cachedStates = { ...states, custom_firewall: { catalog: { scenarios: [customScenario], groups: [] } } };
assert.deepEqual(packageGuideScenarios(cachedStates, customTypes), [customScenario]);
assert.deepEqual(packageGuideTree(cachedStates, customTypes).map((root) => root.id), ["package-guide-type-firewall"]);
for (const s of merged) for (const v of s.variants) {
  const selected = resolvePackageGuideScenario(s, { [GUIDE_VARIANT_FIELD]: v.id });
  assert.equal(selected.deviceProfile, v.deviceProfile);
  assert.equal(selected.deviceType, v.deviceType);
  assert.ok(s.fields.every((field) => field.key !== GUIDE_VARIANT_FIELD));
  const automatic = selectPackageGuideVariant(s, { deviceProfile: v.deviceProfile, deviceType: v.deviceType });
  assert.equal(automatic.deviceProfile, v.deviceProfile);
  assert.equal(automatic.deviceType, v.deviceType);
}
const auto = catalogFor("huawei_olt").scenarios.find((s) => s.variants.some((v) => v.deviceProfile === "huawei_olt"));
const retained = auto.variants.length;
assert.equal(selectPackageGuideVariant(auto, { deviceProfile: "huawei_olt", deviceType: "olt" }).deviceProfile, "huawei_olt");
assert.equal(auto.variants.length, retained, "Simplifying the UI must not remove documented forms");
const normal = fixture("huawei_olt");
normal.categories[0].scenarios[0].variants.unshift({ ...normal.categories[0].scenarios[0].variants[0],
  id: "expert-first", interface: "专家 CLI", views: ["SU模式"], template: "display cpu info" });
const normalScenario = packageGuideCatalog(normal, "huawei_olt").scenarios[0];
assert.notEqual(selectPackageGuideVariant(normalScenario, { deviceProfile: "huawei_olt", deviceType: "olt" }).id, "expert-first");
console.log("Guide unit checks passed: CLI inputs compiled, no reference-only functions or model/form selectors, risk metadata retained");

const url = process.env.WEBUI_TEST_URL || "http://127.0.0.1:5199";
const server = process.env.WEBUI_TEST_URL ? null : spawn(process.execPath, [fileURLToPath(new URL("../node_modules/vite/bin/vite.js", import.meta.url)),
  ...(process.env.GUIDE_TEST_PRODUCTION === "1" ? ["preview"] : []), "--host", "127.0.0.1", "--port", "5199", "--strictPort"],
  { cwd: fileURLToPath(new URL("..", import.meta.url)), stdio: "ignore" });
const executablePath = [process.env.CHROME_PATH, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/usr/bin/chromium"].filter(Boolean).find(existsSync);
let browser;
async function setupPage(profile, options = {}) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
  await context.addInitScript(() => {
    sessionStorage.setItem("auditbox_auth", JSON.stringify({ username: "test", role: "admin", token: "mock" }));
    localStorage.setItem("auditbox-operations-panel-width", "360");
  });
  const page = await context.newPage();
  const state = { errors: [], socketMessages: [], completions: 0, requests: {}, counts: 0, connections: 0, expectedProfile: profile };
  let saved;
  page.on("pageerror", (e) => state.errors.push(e.message));
  await page.route("**/api/**", (route) => {
    const request = route.request(), path = new URL(request.url()).pathname;
    const json = (value, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
    if (path === "/api/device-profiles") return json({ profiles: profileIds.map((p) => ({ profile_id: p, label: data[p].label,
      device_type: data[p].device_type, category_label: data[p].device_type.toUpperCase() })) });
    if (path === "/api/commands/guide/counts") {
      state.counts++;
      if (state.counts <= (options.failCounts || 0)) return json({ detail: "统计失败" }, 503);
      return json(countsData);
    }
    if (path === "/api/commands/guide") {
      const p = new URL(request.url()).searchParams.get("device_profile");
      state.requests[p] = (state.requests[p] || 0) + 1;
      if (p === "huawei_olt" && state.requests[p] === 1) return json({ detail: "加载失败" }, 503);
      return json(data[p]);
    }
    if (path === "/api/commands/suggestions") { state.completions++; return json([]); }
    if (path === "/api/devices") {
      if (request.method() === "POST") { saved = { ...request.postDataJSON(), id: "guide-device", status: "online" }; return json(saved, 201); }
      return json({ items: saved ? [saved] : [], total: saved ? 1 : 0, page: 1, page_size: 100 });
    }
    if (path === "/api/devices/status") return json(saved ? [{ id: saved.id, status: "online" }] : []);
    if (path === "/api/recent-connections") return json([]);
    if (path === "/api/sessions" || path === "/api/connections") {
      const value = request.postDataJSON(); assert.equal(value.device_profile, state.expectedProfile); state.connections++;
      saved = { ...value, id: "guide-device", status: "online" };
      return json({ ...value, id: "guide-device", device_id: "guide-device", session_id: "mock", status: "online",
        websocket_url: url.replace(/^http/, "ws") + "/ws/connections/mock" }, 201);
    }
    return json({ required: false, data: [], ready: true, capabilities: {} });
  });
  await page.routeWebSocket("**/ws/connections/**", (socket) => {
    state.socket = socket;
    socket.send(JSON.stringify({ type: "handshake", status: "ok", session_id: "mock" }));
    socket.send("\r\nOLT# "); socket.onMessage((message) => state.socketMessages.push(String(message)));
  });
  return { context, page, state };
}
async function connect(page, profile, type = data[profile]?.device_type) {
  await page.locator(".left-panel").getByRole("button", { name: "新建安全连接" }).click();
  const d = page.getByRole("dialog", { name: "新建安全连接" });
  await d.getByLabel("设备大类").selectOption(type);
  await d.getByLabel("设备子类型").selectOption(profile);
  await d.getByLabel("地址", { exact: true }).fill("192.0.2.1");
  await d.getByLabel("设备名称").fill(profile);
  await d.getByLabel("用户名", { exact: true }).fill("test");
  await d.getByLabel("登录密码").fill("test");
  await d.getByRole("button", { name: "保存并连接" }).click();
  await page.locator(".session-status-dot.connected").waitFor();
}
async function openBrand(page, profile) {
  await page.getByRole("tab", { name: "指令速查", exact: true }).click();
  const root = page.locator('[data-guide-domain="package-guide-type-pon"]');
  assert.equal(await page.locator("[data-guide-domain^='package-guide-type-']").count(), 1);
  const rootButton = root.locator(".offline-skill-root");
  if (await rootButton.getAttribute("aria-expanded") !== "true") await rootButton.click();
  const vendor = profile.split("_")[0];
  const brand = root.locator(`[data-guide-profile="${vendor}_olt"]`);
  const button = brand.locator(".offline-skill-branch-trigger");
  if (await button.getAttribute("aria-expanded") !== "true") await button.click();
  const expected = catalogFor(profile);
  await page.waitForFunction(({ vendor, count }) => {
    const b = document.querySelector(`[data-guide-profile="${vendor}_olt"]`);
    b?.querySelector(".guide-package-notice button")?.click();
    return b?.querySelector(".offline-skill-branch-trigger b")?.textContent === String(count)
      && b?.querySelectorAll(".offline-skill-function-trigger").length;
  }, { vendor, count: expected.scenarios.length });
  for (const group of await brand.locator(".offline-skill-function-trigger").all()) {
    if (await group.getAttribute("aria-expanded") !== "true") await group.click();
  }
  assert.equal(await brand.locator(".offline-skill-leaf").count(), expected.scenarios.length);
  return brand;
}
async function select(brand, scenario, variant, fill = true) {
  const leaf = brand.locator(`[data-guide-scenario="${scenario.id}"] .offline-skill-leaf`);
  if (await leaf.getAttribute("aria-expanded") !== "true") await leaf.click();
  assert.equal(await brand.locator(`.guide-fields [name="${GUIDE_VARIANT_FIELD}"]`).count(), 0, "Do not ask for firmware or command forms");
  if (fill) for (const parameter of variant.parameters) {
    const field = brand.locator(".guide-fields").getByLabel(parameter.label, { exact: true });
    if (parameter.validation === "enum") await field.selectOption(valueFor(parameter));
    else await field.fill(valueFor(parameter));
  }
}
async function assertCannotDispatch(brand) {
  const button = brand.locator(".guide-run");
  if (await button.count()) assert.ok(await button.isDisabled());
}
function inputs(state) {
  return state.socketMessages.flatMap((m) => {
    if (!m.startsWith("{")) return [m]; const v = JSON.parse(m);
    if (v.type !== "terminal_input") return []; assert.equal(v.source, "guide"); return [v.data];
  });
}
try {
  for (let i = 0; ; i++) {
    try { if ((await fetch(url)).ok) break; } catch { /* Start preview. */ }
    assert.ok(i < 80); await delay(100);
  }
  browser = await chromium.launch({ headless: true, executablePath });
  for (const profile of profileIds) {
    const { context, page, state } = await setupPage(profile);
    try {
      await page.goto(url + "/console");
      await page.getByRole("tab", { name: "指令速查", exact: true }).click();
      await page.waitForFunction((n) => document.querySelector('[data-guide-domain="package-guide-type-pon"] .offline-skill-root b')?.textContent === String(n), totalCount);
      assert.deepEqual(state.requests, {}, "Collapsed counts must not load details");
      assert.equal(state.counts, 1);
      if (process.env.GUIDE_SCREENSHOT_DIR && profile === "fiberhome_olt") {
        const root = page.locator('[data-guide-domain="package-guide-type-pon"]');
        await root.locator(".offline-skill-root").click();
        assert.equal(await root.locator(".offline-skill-branch-trigger").count(), 4);
        for (const vendor of vendors) {
          const subtitle = root.locator(`[data-guide-profile="${vendor}_olt"] .offline-skill-branch-trigger small`);
          assert.ok((await subtitle.getAttribute("title"))?.trim());
        }
        await page.locator(".operations-panel").screenshot({ path: process.env.GUIDE_SCREENSHOT_DIR + "/merged-brand-menu.png" });
      }
      const brand = await openBrand(page, profile);
      assert.deepEqual(new Set(Object.keys(state.requests)), new Set(profileIds.filter((p) => p.startsWith(profile.split("_")[0] + "_"))), "Only this brand's OLT/ONU loads");
      assert.equal(state.connections, 0);
      const catalog = catalogFor(profile);
      const target = { deviceProfile: profile, deviceType: data[profile].device_type };
      const candidates = catalog.scenarios.filter((s) => s.variants.some((v) => v.deviceProfile === profile));
      const chosen = candidates.find((s) => { const v = selectPackageGuideVariant(s, target); return !v.documentation_only && v.risk === "read_only" && v.parameters.length; })
        || candidates.find((s) => { const v = selectPackageGuideVariant(s, target); return !v.documentation_only && v.parameters.length; })
        || candidates.find((s) => !selectPackageGuideVariant(s, target).documentation_only) || candidates[0] || catalog.scenarios[0];
      const variant = selectPackageGuideVariant(chosen, target);
      await select(brand, chosen, variant, false);
      await assertCannotDispatch(brand);
      await connect(page, profile);
      await select(brand, chosen, variant);
      if (!variant.documentation_only && variant.deviceProfile === profile) {
        const textParam = variant.parameters.find((p) => p.validation !== "enum");
        if (textParam) {
          const input = brand.locator(".guide-fields").getByLabel(textParam.label, { exact: true });
          await input.fill("1;reload");
          assert.ok(await brand.locator(".guide-run").isDisabled());
          await input.fill(valueFor(textParam));
        }
        const values = { [GUIDE_VARIANT_FIELD]: variant.id, ...Object.fromEntries(variant.parameters.map((p) => [p.key, valueFor(p)])) };
        const expected = buildPackageGuide(chosen, values).commands[0];
        const before = inputs(state).length, completions = state.completions;
        await brand.locator(".guide-run").click();
        await brand.locator(".guide-command-item.filled").waitFor();
        assert.ok(inputs(state).slice(before).some((v) => v.includes(expected)));
        assert.ok(inputs(state).slice(before).every((v) => !/[\r\n]/.test(v)), "Fill never executes");
        assert.equal(state.completions, completions, "Programmatic fill never triggers completion");
        for (const field of await brand.locator(".guide-fields input, .guide-fields select").all()) assert.ok(await field.isDisabled());
        await brand.getByRole("button", { name: "停止队列" }).click();
      } else await assertCannotDispatch(brand);
      const docScenario = catalog.scenarios.find((s) => selectPackageGuideVariant(s, target).documentation_only);
      if (docScenario) {
        const doc = selectPackageGuideVariant(docScenario, target);
        await select(brand, docScenario, doc);
        await assertCannotDispatch(brand);
        const before = inputs(state).length;
        await brand.locator(".guide-form").evaluate((f) => f.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
        assert.equal(inputs(state).length, before);
      }
      const otherScenario = catalog.scenarios.find((s) => { const v = selectPackageGuideVariant(s, target); return v.deviceProfile !== profile && !v.documentation_only; });
      if (otherScenario) {
        const other = selectPackageGuideVariant(otherScenario, target);
        await select(brand, otherScenario, other);
        assert.ok(await brand.locator(".guide-run").isDisabled());
        assert.match(await brand.locator(".guide-dispatch-notice").innerText(), /类型或品牌不匹配/);
      }
      await select(brand, chosen, variant);
      assert.equal(await brand.getByText("型号与命令形式", { exact: true }).count(), 0);
      assert.equal(await brand.getByText("文档用途与适用范围", { exact: true }).count(), 0);
      assert.equal(await brand.getByText("查看说明", { exact: true }).count(), 0);
      assert.equal(await brand.getByRole("region", { name: "手册参考内容" }).count(), 0);
      const overflow = await brand.evaluate((e) => [...e.querySelectorAll(".guide-form, .guide-command-item, .guide-fields input")]
        .filter((c) => c.clientWidth && c.scrollWidth > c.clientWidth + 1).map((c) => c.className));
      assert.deepEqual(overflow, []);
      if (process.env.GUIDE_SCREENSHOT_DIR) await page.locator(".operations-panel").screenshot({ path: process.env.GUIDE_SCREENSHOT_DIR + "/" + profile + ".png" });
      assert.deepEqual(state.errors, []);
      console.log("Merged guide UI passed: " + profile + "; " + catalog.scenarios.length + " operations, mocked device only");
    } finally { await context.close(); }
  }
  for (const [profile, type] of [["linux", "server"], ...["h3c_comware", "huawei_vrp", "cisco_ios", "zte_zxr10"].map((p) => [p, "switch"])]) {
    const { context, page, state } = await setupPage(profile);
    try {
      await page.goto(url + "/console"); await connect(page, profile, type);
      await page.getByRole("tab", { name: "指令速查", exact: true }).click();
      assert.equal(await page.locator('[data-guide-domain="package-guide-type-pon"]').count(), 0);
      assert.equal(await page.locator("[data-guide-domain]").count(), 1);
      assert.deepEqual(state.requests, {}, "Non-PON devices must not load PON details");
      assert.deepEqual(inputs(state), []); assert.deepEqual(state.errors, []);
      if (process.env.GUIDE_SCREENSHOT_DIR && ["linux", "h3c_comware"].includes(profile))
        await page.screenshot({ path: process.env.GUIDE_SCREENSHOT_DIR + "/isolation-" + profile + ".png", fullPage: true });
      console.log("Guide device isolation passed: " + profile);
    } finally { await context.close(); }
  }
  const switched = await setupPage("huawei_olt");
  try {
    await switched.page.goto(url + "/console");
    await openBrand(switched.page, "huawei_olt");
    switched.state.expectedProfile = "linux";
    await connect(switched.page, "linux", "server");
    assert.equal(await switched.page.locator('[data-guide-domain="package-guide-type-pon"]').count(), 0);
    switched.state.socket.close();
    await switched.page.locator(".session-status-dot.connected").waitFor({ state: "detached" });
    assert.equal(await switched.page.locator('[data-guide-domain="package-guide-type-pon"]').count(), 0,
      "Disconnecting a selected Linux device must not reveal cached PON categories");
    assert.deepEqual(switched.state.errors, []);
    console.log("Guide cached catalog and disconnected device isolation passed");
  } finally { await switched.context.close(); }
  const retry = await setupPage("zte_olt", { failCounts: 1 });
  try {
    await retry.page.goto(url + "/console"); await retry.page.getByRole("tab", { name: "指令速查", exact: true }).click();
    await retry.page.getByRole("button", { name: "重试统计", exact: true }).click();
    await retry.page.waitForFunction((n) => document.querySelector('[data-guide-domain="package-guide-type-pon"] .offline-skill-root b')?.textContent === String(n), totalCount);
    assert.equal(retry.state.counts, 2); assert.deepEqual(retry.state.requests, {});
  } finally { await retry.context.close(); }
} finally {
  if (browser) await browser.close();
  if (server) server.kill("SIGTERM");
}
