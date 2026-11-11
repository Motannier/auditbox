import assert from "node:assert/strict";
import { invalidateComputerClock, synchronizeComputerClock } from "../src/services/clockService.js";
import { writeAuthSession, readAuthSession, clearAuthSession } from "../src/lib/authStorage.js";
import { compareConversationsDescending, conversationTimestamp,
  readActiveConversation, rememberActiveConversation } from "../src/lib/assistantHistory.js";

const values = new Map();
globalThis.window = new EventTarget();
window.sessionStorage = {
  getItem: (key) => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, value),
  removeItem: (key) => values.delete(key),
};
const events = [];
window.addEventListener("auditbox-clock-sync", (event) => events.push(event.detail));
let calls = 0;
const nativeTimeout = AbortSignal.timeout;
AbortSignal.timeout = undefined;
globalThis.fetch = async (_url, options) => {
  calls++;
  assert.equal(options.headers.Authorization, "Bearer old-token");
  assert.ok(Math.abs(JSON.parse(options.body).client_time_ms - Date.now()) < 1000);
  assert.ok(options.signal instanceof AbortSignal);
  assert.equal(options.signal.aborted, false);
  return Response.json({ synchronized: true, token: "new-token" });
};
writeAuthSession({ username: "opsadmin", role: "admin", token: "old-token" });
await Promise.all([synchronizeComputerClock(), synchronizeComputerClock(), synchronizeComputerClock()]);
assert.equal(calls, 1);
assert.equal(readAuthSession().token, "new-token");
await synchronizeComputerClock();
assert.equal(calls, 1, "Repeated API requests should not continually reset the clock");

globalThis.fetch = async () => { calls++; return Response.json({ synchronized: false }); };
invalidateComputerClock();
await synchronizeComputerClock();
assert.equal(calls, 2, "The first request after a disconnected transport must synchronize again");

globalThis.fetch = async () => { throw new Error("USB offline"); };
await synchronizeComputerClock("", true);
assert.match(events.at(-1).error, /USB offline/);
let resolve;
globalThis.fetch = () => new Promise((done) => { resolve = done; });
const pending = synchronizeComputerClock("", true);
clearAuthSession();
resolve(Response.json({ synchronized: true, token: "must-not-restore" }));
await pending;
assert.equal(readAuthSession(), null);

writeAuthSession({ username: "opsadmin", role: "admin", token: "token" });
rememberActiveConversation("device:A", "conversation-A");
rememberActiveConversation("device:B", "conversation-B");
assert.equal(readActiveConversation("device:A"), "conversation-A");
assert.equal(readActiveConversation("device:B"), "conversation-B");
assert.equal(readActiveConversation("public"), null);
assert.equal(conversationTimestamp({ created_at: 2000, updated_at: 1000 }), 2000000);
assert.deepEqual([
  { id: "a", updatedAt: 3 }, { id: "b", updatedAt: 3 }, { id: "c", updatedAt: 5 },
].sort(compareConversationsDescending).map((x) => x.id), ["c", "b", "a"]);
AbortSignal.timeout = nativeTimeout;
console.log("Clock/history units passed: authenticated sync, coalescing, token rotation, failure/logout isolation, device persistence, descending ordering");
