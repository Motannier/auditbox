import { readAuthSession, writeAuthSession } from "../lib/authStorage.js";

let inFlight = null;
let lastToken = "";
let nextAttempt = 0;

export function invalidateComputerClock() {
  lastToken = "";
  nextAttempt = 0;
}

// Use monotonic time for throttling: calibrating either machine must not
// suppress future synchronization. No unauthenticated endpoint can set time.
export async function synchronizeComputerClock(baseUrl = "", force = false) {
  const auth = readAuthSession();
  if (!auth?.token) return;
  if (inFlight) return inFlight;
  if (!force && auth.token === lastToken && performance.now() < nextAttempt) return;
  inFlight = (async () => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 4000);
    try {
      const response = await fetch(`${baseUrl}/api/system/clock/sync`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${auth.token}` },
        body: JSON.stringify({ client_time_ms: Date.now() }),
        signal: controller.signal,
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result?.detail || "电脑时间同步失败");
      // A response arriving after logout/account switch must not restore auth.
      if (readAuthSession()?.token !== auth.token) return;
      if (result.token) writeAuthSession({ ...auth, token: result.token });
      lastToken = result.token || auth.token;
      nextAttempt = performance.now() + 30_000;
      window.dispatchEvent(new CustomEvent("auditbox-clock-sync", { detail: { error: "" } }));
    } catch (error) {
      if (readAuthSession()?.token !== auth.token) return;
      lastToken = auth.token;
      nextAttempt = performance.now() + 5000;
      // Clock trouble must not disable the terminal or the rest of the UI.
      window.dispatchEvent(new CustomEvent("auditbox-clock-sync", {
        detail: { error: `电脑时间尚未同步：${error.message}` },
      }));
    } finally {
      clearTimeout(timeout);
      inFlight = null;
    }
  })();
  return inFlight;
}
