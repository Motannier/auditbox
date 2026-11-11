import { notifyAuthExpired, readAccessToken } from "./authStorage.js";

let baseUrl = "";
let pending = [];
let timer;
let installed = false;
let sending = false;
const sensitiveField = /password|passwd|passphrase|secret|token|api[_-]?key|authorization|otp|verification[_-]?code|验证码|密码|口令|请求头/i;

// Read the token at the gesture, before logout can clear the local session.
export function recordUserAction(action, details = {}) {
  const token = readAccessToken();
  if (!token) return;
  const event = { action, ...details };
  for (const key of ["label", "section", "field"]) event[key] = String(event[key] || "").slice(0, key === "label" ? 512 : 255);
  event.value = String(event.value || "").slice(0, 4096);
  if (sensitiveField.test(event.field) || event.redacted) {
    event.value = "[敏感输入已隐藏]";
    event.redacted = true;
  }
  pending.push({ token, event });
  // Bound outage buffering; make a failure visible in the diagnostic console.
  if (pending.length > 200) { pending.shift(); console.warn("用户操作日志等待发送队列已满"); }
  if (!timer) timer = window.setTimeout(flushUserActions, 150);
}

export async function flushUserActions() {
  window.clearTimeout(timer);
  timer = null;
  if (sending) return;
  sending = true;
  while (pending.length) {
    const token = pending[0].token;
    const group = [];
    // Three bounded events fit under Chrome's 64 KiB keepalive budget even
    // when every character is Chinese. Only one request is in flight.
    while (pending.length && pending[0].token === token && group.length < 3) group.push(pending.shift());
    try {
      const response = await fetch(`${baseUrl}/api/audit/behavior`, {
        method: "POST", keepalive: true,
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ events: group.map(item => item.event) }),
      });
      if (response.status === 401) notifyAuthExpired(token);
      if ([400, 401, 403, 404, 413, 422].includes(response.status)) {
        // An expired login or rejected payload cannot recover by retrying.
        // Leave the diagnostic, then allow the next login's events through.
        console.warn("用户操作日志被拒绝", `HTTP ${response.status}`);
        continue;
      }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
    } catch (error) {
      console.warn("用户操作日志发送失败", error.message);
      // Retain the actor captured at the gesture; never assign it to a new login.
      pending = [...group, ...pending].slice(0, 200);
      if (!timer) timer = window.setTimeout(flushUserActions, 3000);
      break;
    }
  }
  sending = false;
}

function sectionOf(element) {
  const dialog = element.closest('[role="dialog"]');
  if (dialog?.getAttribute("aria-label")) return dialog.getAttribute("aria-label");
  for (const [selector, label] of [
    [".terminal-frame", "终端"], [".assistant-panel", "运维助手"],
    [".guide-panel, .command-panel", "指令速查"], [".bookmark-panel", "设备列表"],
    [".topbar", "顶部导航"], [".operations-panel", "操作面板"],
  ]) if (element.closest(selector)) return label;
  return document.title;
}

function fieldName(element) {
  const label = element.labels?.[0]?.querySelector("span")?.textContent
    || element.labels?.[0]?.textContent;
  return element.getAttribute("aria-label") || element.name || element.id
    || label?.trim().slice(0, 255) || element.placeholder || "输入框";
}

export function installUserAudit(apiBaseUrl = "") {
  if (installed) return;
  installed = true;
  baseUrl = apiBaseUrl;
  const drafts = new Map();
  const lastValues = new WeakMap();
  const editable = element => element?.matches("input, textarea, select")
    && !element.closest(".xterm, .terminal-clipboard-dialog") && !element.readOnly && !element.disabled;

  function recordField(element, action) {
    if (!editable(element)) return;
    const field = fieldName(element);
    const redacted = element.type === "password" || sensitiveField.test(field);
    const value = redacted ? "[敏感输入已隐藏]" : element.type === "file"
      ? Array.from(element.files || []).map(file => file.name).join("、")
      : ["checkbox", "radio"].includes(element.type) ? String(element.checked)
        : element.tagName === "SELECT" ? element.selectedOptions?.[0]?.textContent || element.value : element.value;
    // Programmatic defaults have no trusted input/change event.
    if (action === "ui_change" && lastValues.get(element) === value) return;
    lastValues.set(element, value);
    recordUserAction(action, { field, value, redacted, section: sectionOf(element) });
  }
  function flushDrafts() {
    for (const [element, timeout] of drafts) {
      window.clearTimeout(timeout);
      recordField(element, "ui_input");
    }
    drafts.clear();
  }
  document.addEventListener("input", event => {
    if (!event.isTrusted || !editable(event.target)) return;
    const element = event.target;
    window.clearTimeout(drafts.get(element));
    drafts.set(element, window.setTimeout(() => {
      recordField(element, "ui_input"); drafts.delete(element);
    }, 600));
  }, true);
  document.addEventListener("change", event => {
    if (!event.isTrusted) return;
    window.clearTimeout(drafts.get(event.target)); drafts.delete(event.target);
    recordField(event.target, "ui_change");
  }, true);
  document.addEventListener("click", event => {
    if (!event.isTrusted || !(event.target instanceof Element)) return;
    const element = event.target.closest('button, a[href], [role="button"], [role="tab"], summary');
    if (!element || element.disabled || element.getAttribute("aria-disabled") === "true") return;
    flushDrafts();
    const label = element.dataset.auditLabel || element.getAttribute("aria-label") || element.title || element.textContent;
    recordUserAction("ui_click", { label: label?.trim(), section: sectionOf(element) });
    // Start before a logout or navigation unmounts the controls.
    flushUserActions();
  }, true);
  document.addEventListener("submit", event => {
    if (!event.isTrusted) return;
    flushDrafts();
    recordUserAction("ui_submit", { label: "提交表单", section: sectionOf(event.target) });
  }, true);
  for (const type of ["copy", "paste"]) document.addEventListener(type, event => {
    if (!event.isTrusted || !(event.target instanceof Element) || event.target.closest(".xterm")) return;
    recordUserAction(`ui_${type}`, { field: fieldName(event.target), section: sectionOf(event.target) });
  }, true);
  document.addEventListener("keydown", event => {
    if (event.isTrusted && event.key === "Enter" && !event.target.closest?.(".xterm")) flushDrafts();
  }, true);
  document.addEventListener("pointerup", event => {
    if (event.isTrusted && event.target instanceof Element && event.target.matches('[role="separator"]')) {
      recordUserAction("ui_resize", { label: event.target.getAttribute("aria-label") || "调整面板宽度" });
    }
  }, true);
  const leave = () => { flushDrafts(); flushUserActions(); };
  window.addEventListener("pagehide", leave);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") leave(); });
}
