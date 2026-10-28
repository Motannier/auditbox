export const AUTH_STORAGE_KEY = "auditbox_auth";
const LEGACY_AUTH_STORAGE_KEY = "auditbox_user";

export function readAuthSession() {
  try {
    const raw = window.sessionStorage.getItem(AUTH_STORAGE_KEY);
    if (!raw) return null;
    const session = JSON.parse(raw);
    if (
      !session ||
      typeof session.username !== "string" ||
      typeof session.token !== "string" ||
      !["admin", "super_admin"].includes(session.role)
    ) {
      return null;
    }
    return session;
  } catch {
    return null;
  }
}

export function writeAuthSession(session) {
  window.sessionStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(session));
  window.sessionStorage.removeItem(LEGACY_AUTH_STORAGE_KEY);
}

export function clearAuthSession() {
  window.sessionStorage.removeItem(AUTH_STORAGE_KEY);
  window.sessionStorage.removeItem(LEGACY_AUTH_STORAGE_KEY);
}

export function readAccessToken() {
  return readAuthSession()?.token || "";
}
