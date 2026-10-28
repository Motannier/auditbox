import assert from "node:assert/strict";
import { detectClientDevice } from "../src/lib/clientDevice.js";

export const deviceCases = [
  { label: "Windows · 电脑", userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36", platform: "Win32" },
  { label: "macOS · 电脑", userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15", platform: "MacIntel" },
  { label: "Linux · 电脑", userAgent: "Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/142.0", platform: "Linux x86_64" },
  { label: "Android · 手机", userAgent: "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/140.0.0.0 Mobile Safari/537.36", platform: "Linux armv8l" },
  { label: "Android · 平板", userAgent: "Mozilla/5.0 (Linux; Android 14; SM-X910) AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36", platform: "Linux armv8l" },
  { label: "iOS · 手机", userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1", platform: "iPhone" },
  { label: "iPadOS · 平板", userAgent: "Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1", platform: "iPad" },
  { label: "iPadOS · 平板", userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1", platform: "MacIntel", maxTouchPoints: 5 },
  { label: "ChromeOS · 电脑", userAgent: "Mozilla/5.0 (X11; CrOS x86_64 16000.0.0) AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36", platform: "Linux x86_64" },
  { label: "未知设备", userAgent: "UnrecognizedBrowser/1.0", platform: "" },
];

for (const example of deviceCases) {
  assert.equal(detectClientDevice(example).label, example.label);
}
assert.equal(detectClientDevice(null).label, "未知设备");
assert.equal(detectClientDevice({}).label, "未知设备");
assert.equal(detectClientDevice({ userAgentData: { platform: "Windows" } }).label, "Windows · 电脑");
assert.equal(detectClientDevice({ userAgentData: { platform: "Android", mobile: true } }).label, "Android · 手机");
assert.equal(detectClientDevice({ userAgent: "PrivacyBrowser Mobile" }).label, "手机");
console.log("Client device classification passed (15 cases)");
