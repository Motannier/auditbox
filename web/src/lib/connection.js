import { defaultDeviceProfile } from "./deviceProfiles.js";

export function connectionToTarget(connection) {
  return {
    id: connection.id,
    deviceId: connection.device_id || null,
    sessionId: connection.session_id || null,
    protocol: String(connection.protocol || "").toUpperCase(),
    deviceType: connection.device_type || "",
    deviceProfile: connection.device_profile || defaultDeviceProfile(connection.device_type),
    host: connection.address,
    port: connection.port,
    remark: connection.remark || "",
    notes: connection.notes || "",
    status: connection.status || "online",
    username: connection.username || "",
    lastConnectedAt: connection.last_connected_at || null,
    websocketUrl: connection.websocket_url || "",
  };
}

export function targetToConnectionPayload(target, targetPassword = "") {
  return {
    address: target.host,
    port: Number(target.port),
    protocol: String(target.protocol || "ssh").toLowerCase(),
    device_type: target.deviceType,
    device_profile: target.deviceProfile,
    remark: target.remark || "",
    notes: target.notes || "",
    username: target.username || undefined,
    target_password: targetPassword,
  };
}

export function protocolDisplayName(protocol) {
  const normalized = String(protocol || "").toLowerCase();
  if (normalized === "ssh") return "安全接入";
  if (normalized === "telnet") return "Telnet";
  if (normalized === "console") return "Console";
  return "远程连接";
}

export function connectionEndpointLabel(target) {
  if (String(target?.protocol || "").toLowerCase() === "console") {
    const deviceName = String(target?.host || "Console").split("/").filter(Boolean).at(-1) || "Console";
    return `${deviceName} · ${target?.port || 9600} baud`;
  }
  return `${target?.host || ""}:${target?.port || ""}`;
}

export { deviceProfileDisplayName, deviceTypeDisplayName } from "./deviceProfiles.js";
