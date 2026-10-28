export const MAX_TERMINAL_PASTE_LENGTH = 65536;

// Clipboard content is text, never terminal control input. In particular, tabs
// must not trigger native completion and a copied final newline must not run it.
export function normalizeTerminalPaste(value) {
  const raw = String(value || "");
  if (raw.length > MAX_TERMINAL_PASTE_LENGTH) {
    throw new Error("粘贴内容过长，请分段粘贴（每次不超过 64K 字符）");
  }
  return raw
    .replace(/\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|.)/g, "")
    .replace(/\r\n?/g, "\n")
    .replace(/\t/g, "    ")
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/g, "")
    .replace(/\n+$/, "");
}

export function terminalClipboardShortcut(event, hasSelection) {
  if (event.type !== "keydown" || event.altKey || event.isComposing) return null;
  const key = event.key.toLowerCase();
  if ((event.metaKey && !event.ctrlKey) || (event.ctrlKey && !event.metaKey && event.shiftKey)) {
    if (key === "c") return "copy";
    if (key === "v") return "paste";
  }
  if (event.ctrlKey && !event.metaKey && !event.shiftKey && key === "c" && hasSelection) return "copy";
  if (!event.metaKey && key === "insert") {
    if (event.ctrlKey && !event.shiftKey) return "copy";
    if (event.shiftKey && !event.ctrlKey) return "paste";
  }
  return null;
}

export async function writeTerminalClipboard(text) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // HTTP device addresses and denied clipboard permissions use the legacy
    // user-gesture copy path instead of requiring HTTPS for the whole product.
  }
  const field = document.createElement("textarea");
  field.value = text;
  field.setAttribute("aria-label", "复制终端文本");
  field.style.cssText = "position:fixed;left:-9999px;top:0;opacity:0";
  const previous = document.activeElement;
  document.body.appendChild(field);
  field.select();
  try {
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    field.remove();
    previous?.focus?.({ preventScroll: true });
  }
}
