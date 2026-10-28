import { CONNECTION_PAGE_SIZE } from "../constants/index.js";
import { connectionToTarget } from "../lib/connection.js";
import { defaultDeviceProfile } from "../lib/deviceProfiles.js";
import { apiRequest } from "./http.js";

export async function fetchDevices({ search = "", page = 1, pageSize = CONNECTION_PAGE_SIZE } = {}) {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  });
  if (search.trim()) {
    params.set("search", search.trim());
  }
  const data = await apiRequest(`/api/devices?${params.toString()}`);
  return {
    ...data,
    items: data.items.map(connectionToTarget),
  };
}

export async function createDevice(payload) {
  const data = await apiRequest("/api/devices", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return connectionToTarget(data);
}

export async function updateDevice(deviceId, payload) {
  const data = await apiRequest(`/api/devices/${encodeURIComponent(deviceId)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
  return connectionToTarget(data);
}

export async function fetchDeviceStatuses() {
  return apiRequest("/api/devices/status");
}

export async function fetchRecentConnections(limit = 4) {
  const data = await apiRequest(`/api/recent-connections?limit=${encodeURIComponent(limit)}`);
  return data.map((item) => ({
    id: item.device_id || "",
    deviceId: item.device_id || null,
    recentId: item.id,
    protocol: String(item.protocol || "").toUpperCase(),
    deviceType: item.device_type || "",
    deviceProfile: item.device_profile || defaultDeviceProfile(item.device_type),
    host: item.address,
    port: item.port,
    remark: item.remark || "",
    notes: item.notes || "",
    username: item.username || "",
    status: "probing",
    lastOpenedAt: item.last_opened_at,
    lastOperationAt: item.last_operation_at,
    operationResult: item.operation_result || "",
    operationRisk: Boolean(item.operation_risk),
  }));
}

export async function fetchConsolePorts() {
  return apiRequest("/api/console/ports");
}

export async function createSession(payload) {
  const data = await apiRequest("/api/sessions", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return connectionToTarget(data);
}

export async function createSavedConnection(payload) {
  const data = await apiRequest("/api/connections", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return connectionToTarget(data);
}
