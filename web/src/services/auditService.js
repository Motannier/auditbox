import { apiFetch } from "./http.js";

export async function fetchOperationLogExport() {
  const response = await apiFetch("/api/audit/export", { method: "GET" });
  if (!response.ok) {
    const contentType = response.headers.get("content-type") || "";
    const body = contentType.includes("application/json")
      ? await response.json()
      : await response.text();
    const message = typeof body === "object" && body?.detail ? body.detail : `HTTP ${response.status}`;
    throw new Error(message);
  }

  const disposition = response.headers.get("content-disposition") || "";
  const filenameMatch = disposition.match(/filename="?([^";]+)"?/i);
  return {
    blob: await response.blob(),
    filename: filenameMatch?.[1] || "auditbox-operation-logs.csv",
  };
}
