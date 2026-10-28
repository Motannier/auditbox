import { readAuthSession } from "./authStorage.js";

function storageKey(scope) {
  return `auditbox-assistant-active:${readAuthSession()?.username || "local"}:${scope}`;
}

export function readActiveConversation(scope) {
  try { return window.sessionStorage.getItem(storageKey(scope)); } catch { return null; }
}

export function rememberActiveConversation(scope, id) {
  try {
    if (id) window.sessionStorage.setItem(storageKey(scope), id);
    else window.sessionStorage.removeItem(storageKey(scope));
  } catch { /* Private browsing/storage restrictions must not break chat. */ }
}

export function conversationTimestamp(session) {
  return Math.max(Number(session.updated_at) || 0, Number(session.created_at) || 0) * 1000;
}

export function compareConversationsDescending(a, b) {
  if (a.updatedAt !== b.updatedAt) return b.updatedAt - a.updatedAt;
  return a.id === b.id ? 0 : a.id < b.id ? 1 : -1;
}
