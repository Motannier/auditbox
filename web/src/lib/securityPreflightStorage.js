const SECURITY_PREFLIGHT_STORAGE_KEY = "auditbox_security_preflight";

export function readSecurityPreflightSession() {
  try {
    const raw = window.sessionStorage.getItem(SECURITY_PREFLIGHT_STORAGE_KEY);
    if (!raw) return null;
    const session = JSON.parse(raw);
    if (
      !session ||
      typeof session.admissionToken !== "string" ||
      !Number.isFinite(session.expiresAt) ||
      session.expiresAt <= Date.now()
    ) {
      clearSecurityPreflightSession();
      return null;
    }
    return {
      admissionToken: session.admissionToken,
      expiresAt: session.expiresAt,
      config: session.config && typeof session.config === "object" ? session.config : null,
    };
  } catch {
    clearSecurityPreflightSession();
    return null;
  }
}

export function writeSecurityPreflightSession({ admissionToken = "", expiresIn, config = null }) {
  const lifetimeSeconds = Math.max(1, Number(expiresIn) || 12 * 60 * 60);
  const session = {
    admissionToken,
    expiresAt: Date.now() + lifetimeSeconds * 1000,
    config,
  };
  window.sessionStorage.setItem(SECURITY_PREFLIGHT_STORAGE_KEY, JSON.stringify(session));
  return session;
}

export function clearSecurityPreflightSession() {
  window.sessionStorage.removeItem(SECURITY_PREFLIGHT_STORAGE_KEY);
}
