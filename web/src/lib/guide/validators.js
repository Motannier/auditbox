import { cleanPath, invalidGuide } from "./helpers.js";
import { IPV4_PATTERN } from "../ipv4.js";

export { IPV4_PATTERN };

export const HOST_PATTERN = /^[a-zA-Z0-9.:-]+$/;
export const SERVICE_PATTERN = /^[a-zA-Z0-9_.@-]+$/;
export const KEYWORD_PATTERN = /^[a-zA-Z0-9_.:@/-]*$/;
export const MOUNT_PATTERN = /^\/[a-zA-Z0-9_./-]*$/;
export const PATH_PATTERN = /^\/[a-zA-Z0-9_./:@+-]*$/;
export const USER_PATTERN = /^[a-zA-Z_][a-zA-Z0-9_-]*[$]?$/;
export const PROTOCOL_PATTERN = /^(tcp|udp)$/i;
export function validatePath(path, label = "路径") {
  const value = cleanPath(path);
  if (!PATH_PATTERN.test(value)) {
    return {
      error: invalidGuide(`${label}需要检查`, `${label}必须以 / 开头，不要带空格或命令符号。`, [
        "例子：/var/log",
        "例子：/tmp/audit-test",
      ]),
    };
  }
  return { value };
}

export function validateDestructivePath(path) {
  const raw = String(path || "").trim();
  if (!raw) {
    return {
      error: invalidGuide("删除路径不能为空", "必须明确填写要删除的绝对路径。", [
        "例子：/tmp/audit-test",
        "根目录永久禁止删除",
      ]),
    };
  }
  const result = validatePath(raw, "删除路径");
  if (result.error) return result;

  const normalizedParts = [];
  for (const part of result.value.split("/")) {
    if (!part || part === ".") continue;
    if (part === "..") normalizedParts.pop();
    else normalizedParts.push(part);
  }
  if (normalizedParts.length === 0) {
    return {
      error: invalidGuide("根目录禁止删除", "该路径解析后是 /，不允许下发。", [
        "请填写明确的业务子目录",
        "删除前先检查目录大小和内容",
      ]),
    };
  }
  return result;
}

export function validateService(service) {
  const value = (service || "").trim();
  if (!value || !SERVICE_PATTERN.test(value)) {
    return {
      error: invalidGuide("服务名需要检查", "服务名只填写 systemd 服务名，不要带空格和命令符号。", [
        "例子：nginx",
        "例子：ssh",
        "例子：httpd",
      ]),
    };
  }
  return { value };
}

export function validatePid(pid) {
  const value = (pid || "").trim();
  if (!/^\d+$/.test(value)) {
    return {
      error: invalidGuide("PID 需要检查", "PID 只能填写数字。", ["例子：412", "不确定 PID 时先使用进程列表"]),
    };
  }
  return { value };
}

export function validateKeyword(keyword, fallback = "") {
  const value = (keyword || fallback).trim();
  if (!KEYWORD_PATTERN.test(value)) {
    return {
      error: invalidGuide("关键词需要检查", "关键词只能使用字母、数字、点、下划线、横线、冒号和斜杠。", [
        "例子：nginx",
        "例子：error",
      ]),
    };
  }
  return { value };
}

export function validateUser(user, fallback = "root") {
  const value = (user || fallback).trim() || fallback;
  if (!USER_PATTERN.test(value)) {
    return {
      error: invalidGuide("用户名需要检查", "用户名只能使用常见 Linux 用户名格式。", [
        "例子：root",
        "例子：operator",
      ]),
    };
  }
  return { value };
}
