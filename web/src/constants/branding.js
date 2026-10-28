export const DEFAULT_PRODUCT_NAME = "轻量化调试推理工具";
export let PRODUCT_NAME = DEFAULT_PRODUCT_NAME;

// Read public runtime configuration before mounting React so all consumers,
// including the login illustration and terminal, use the same name.
export async function loadBranding({ timeoutMs = 1500 } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  PRODUCT_NAME = DEFAULT_PRODUCT_NAME;
  try {
    const base = import.meta.env?.VITE_AUDITBOX_API_BASE_URL || "";
    const response = await fetch(`${base}/api/branding`, {
      signal: controller.signal, cache: "no-store",
    });
    if (response.ok) {
      const data = await response.json();
      const name = typeof data?.product_name === "string" ? data.product_name.trim() : "";
      if (name && [...name].length <= 80 && !/[\u0000-\u001f\u007f-\u009f]/u.test(name)) {
        PRODUCT_NAME = name;
      }
    }
  } catch {
    // An older/offline backend must not prevent the login screen from opening.
  } finally {
    clearTimeout(timer);
  }
  document.title = PRODUCT_NAME;
}
export const DEFAULT_OPERATOR_USERNAME = "opsadmin";
export const OPERATOR_DISPLAY_NAME = "运维管理员";

export function operatorDisplayName(username) {
  const normalized = String(username || "").trim().toLowerCase();
  if (["admin", "opsadmin"].includes(normalized)) return OPERATOR_DISPLAY_NAME;
  if (normalized === "superadmin") return "系统管理员";
  return username || OPERATOR_DISPLAY_NAME;
}
