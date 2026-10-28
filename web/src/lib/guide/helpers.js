export function guideResult({ status = "warn", title, message, commands, hints, danger = false }) {
  return { status, title, message, commands, hints, danger };
}

export function invalidGuide(title, message, hints) {
  return { status: "error", title, message, commands: [], hints };
}

export function cleanPath(path, fallback = "/") {
  return (path || fallback).trim() || fallback;
}
