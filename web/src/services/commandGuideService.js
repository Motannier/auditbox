import { apiRequest } from "./http.js";

export function fetchCommandGuideCounts({ signal } = {}) {
  return apiRequest("/api/commands/guide/counts", { signal, cache: "no-store" });
}

export function fetchCommandGuide(profile, { signal } = {}) {
  return apiRequest(`/api/commands/guide?device_profile=${encodeURIComponent(profile)}`, {
    signal,
    cache: "no-store",
  });
}
