import { readAccessToken } from "../lib/authStorage.js";
import { invalidateComputerClock, synchronizeComputerClock } from "./clockService.js";

// Production is served by the gateway itself, so relative URLs keep requests
// on the Orange Pi regardless of whether the browser is running on macOS or
// Windows. Local development can still override this in web/.env.
export const API_BASE_URL = import.meta.env.VITE_AUDITBOX_API_BASE_URL || "";

export async function apiFetch(path, options = {}) {
  // Before fetching/creating dated data, synchronize once per connection and
  // periodically thereafter. Concurrent requests share the same short probe.
  if (readAccessToken() && path !== "/api/auth/login") {
    await synchronizeComputerClock(API_BASE_URL);
  }
  const token = readAccessToken();
  const { headers, ...requestOptions } = options;
  const isFormData = typeof FormData !== "undefined" && requestOptions.body instanceof FormData;
  try {
    return await fetch(`${API_BASE_URL}${path}`, {
      ...requestOptions,
      headers: {
        ...(isFormData ? {} : { "Content-Type": "application/json" }),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(headers || {}),
      },
    });
  } catch (error) {
    // A unplug/reboot may have reset the appliance clock during the short
    // throttle window. The first recovered request must calibrate again.
    if (error.name !== "AbortError") invalidateComputerClock();
    throw error;
  }
}

export async function apiRequest(path, options = {}) {
  const response = await apiFetch(path, options);
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const message = typeof data === "object" && data?.detail ? data.detail : `HTTP ${response.status}`;
    const error = new Error(message);
    error.status = response.status;
    error.code = typeof data === "object" ? data?.code || "" : "";
    throw error;
  }
  return data;
}
