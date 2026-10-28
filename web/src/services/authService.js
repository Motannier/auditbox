import { apiRequest } from "./http.js";

export function loginAccount(username, password) {
  return apiRequest("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password, client_time_ms: Date.now() }),
  });
}
