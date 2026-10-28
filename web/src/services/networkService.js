import { apiRequest } from "./http.js";

export function getEthernetSettings() {
  return apiRequest("/api/settings/ethernet");
}

export function applyEthernetSettings(settings) {
  return apiRequest("/api/settings/ethernet", {
    method: "PUT",
    body: JSON.stringify(settings),
  });
}
