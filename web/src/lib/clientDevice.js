// Describes the browser's host only. Never use this as an admission check or
// as the Linux/H3C profile of a remote device being operated on.
export function detectClientDevice(browser = globalThis.navigator) {
  const ua = String(browser?.userAgent || "");
  const platform = String(browser?.userAgentData?.platform || browser?.platform || "");
  const mobileHint = browser?.userAgentData?.mobile === true;
  const desktopIPad = /Mac/i.test(platform) && Number(browser?.maxTouchPoints) > 1;
  let os = "未知系统";
  let deviceType = "unknown";

  if (/iPad/i.test(ua) || desktopIPad) {
    os = "iPadOS";
    deviceType = "tablet";
  } else if (/iPhone|iPod/i.test(ua) || /^iOS$/i.test(platform)) {
    os = "iOS";
    deviceType = "phone";
  } else if (/Android/i.test(ua) || /^Android$/i.test(platform)) {
    os = "Android";
    deviceType = /Mobile/i.test(ua) || mobileHint ? "phone" : "tablet";
  } else if (/Windows/i.test(ua) || /^Win/i.test(platform)) {
    os = "Windows";
    deviceType = "computer";
  } else if (/CrOS/i.test(ua) || /Chrome OS|Chromium OS/i.test(platform)) {
    os = "ChromeOS";
    deviceType = "computer";
  } else if (/Macintosh|Mac OS X/i.test(ua) || /^Mac/i.test(platform)) {
    os = "macOS";
    deviceType = "computer";
  } else if (/Linux/i.test(ua) || /^Linux/i.test(platform)) {
    os = "Linux";
    deviceType = "computer";
  } else if (/Tablet/i.test(ua)) {
    deviceType = "tablet";
  } else if (/Mobile/i.test(ua) || mobileHint) {
    deviceType = "phone";
  }

  const deviceLabel = {
    computer: "电脑",
    phone: "手机",
    tablet: "平板",
    unknown: "未知设备",
  }[deviceType];
  return { os, deviceType, label: os === "未知系统" ? deviceLabel : `${os} · ${deviceLabel}` };
}
