const IPV4_SEGMENT_PATTERN = "(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])";

export const IPV4_INPUT_PATTERN = `${IPV4_SEGMENT_PATTERN}(\\.${IPV4_SEGMENT_PATTERN}){3}`;
export const IPV4_PATTERN = new RegExp(`^${IPV4_INPUT_PATTERN}$`);

export function cleanIPv4Input(value) {
  return String(value ?? "")
    .normalize("NFKC")
    .replace(/[。｡]/g, ".")
    .replace(/[\s\u200B-\u200D\uFEFF]+/g, "");
}

export function isPartialIPv4Input(value) {
  const text = String(value ?? "");
  if (text === "") return true;
  if (text.length > 15 || !/^[0-9.]+$/.test(text)) return false;

  const parts = text.split(".");
  if (parts.length > 4 || parts[0] === "") return false;

  return parts.every((part, index) => {
    if (part === "") return index === parts.length - 1;
    if (part.length > 3 || Number(part) > 255) return false;
    return part.length === 1 || !part.startsWith("0");
  });
}

export function normalizeIPv4Input(value, previousValue = "") {
  const cleaned = cleanIPv4Input(value);
  return isPartialIPv4Input(cleaned) ? cleaned : previousValue;
}
