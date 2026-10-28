import { apiRequest } from "./http.js";

export function getSecurityPreflight() {
  return apiRequest("/api/security/preflight");
}

export function createSecurityChallenge() {
  return apiRequest("/api/security/challenge", { method: "POST", body: "{}" });
}

export function verifySecurityReport(report) {
  return apiRequest("/api/security/verify", {
    method: "POST",
    body: JSON.stringify(report),
  });
}

export function refreshSecurityAdmission() {
  return apiRequest("/api/security/admission/refresh", {
    method: "POST",
    body: "{}",
  });
}

export async function detectGuardian(localUrl, timeoutMs = 2_500) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await localRequest(`${localUrl}/v1/health`, { signal: controller.signal });
  } finally {
    window.clearTimeout(timeout);
  }
}

export async function startGuardianCheck(localUrl, challenge) {
  return localRequest(`${localUrl}/v1/checks`, {
    method: "POST",
    body: JSON.stringify(challenge),
  });
}

export async function readGuardianCheck(localUrl, checkId) {
  return localRequest(`${localUrl}/v1/checks/${encodeURIComponent(checkId)}`);
}

async function localRequest(url, options = {}) {
  const response = await fetch(url, {
    cache: "no-store",
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : {};
  if (!response.ok) {
    throw new Error(data?.error || `智维卫士返回 HTTP ${response.status}`);
  }
  return data;
}
