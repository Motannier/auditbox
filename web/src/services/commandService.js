import { apiRequest } from "./http.js";

export async function fetchCommandSuggestions({ prefix, target, limit = 8 }) {
  const params = new URLSearchParams({
    prefix,
    limit: String(limit),
  });
  if (target?.host && target?.port) {
    params.set("target", `${target.host}:${target.port}`);
  }
  if (target?.protocol) {
    params.set("protocol", target.protocol.toLowerCase());
  }
  if (target?.deviceType) {
    params.set("device_type", target.deviceType);
  }
  if (target?.deviceProfile) {
    params.set("device_profile", target.deviceProfile);
  }
  return apiRequest(`/api/commands/suggestions?${params.toString()}`);
}
