import { apiFetch, apiRequest } from "./http.js";

export function fetchAssistantStatus(deviceProfile = "linux") {
  const params = new URLSearchParams({ device_profile: deviceProfile || "linux" });
  return apiRequest(`/api/assistant/status?${params.toString()}`);
}

export function fetchAssistantCapabilities() {
  return apiRequest("/api/assistant/capabilities");
}

export function setAssistantCapabilityEnabled(capabilityId, enabled) {
  return apiRequest(`/api/assistant/capabilities/${encodeURIComponent(capabilityId)}`, {
    method: "PATCH",
    body: JSON.stringify({ enabled }),
  });
}

export function deleteAssistantCapability(capabilityId) {
  return apiRequest(`/api/assistant/capabilities/${encodeURIComponent(capabilityId)}`, {
    method: "DELETE",
  });
}

export function createAssistantMcpServer(payload) {
  return apiRequest("/api/assistant/capabilities/mcp", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function probeAssistantMcpServer(payload) {
  return apiRequest("/api/assistant/capabilities/mcp/probe", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function probeSavedAssistantMcpServer(capabilityId) {
  return apiRequest(`/api/assistant/capabilities/${encodeURIComponent(capabilityId)}/probe`, {
    method: "POST",
    body: "{}",
  });
}

export function uploadAssistantSkill(file, name = "") {
  const body = new FormData();
  body.append("file", file);
  if (name) body.append("name", name);
  body.append("enabled", "true");
  return apiRequest("/api/assistant/capabilities/skills", { method: "POST", body });
}

export function listAssistantConversations(deviceId = null, page = 1) {
  const params = new URLSearchParams({ limit: "50", page: String(page) });
  if (deviceId) params.set("device_id", deviceId);
  return apiRequest(`/api/assistant/conversations?${params.toString()}`);
}

export function createAssistantConversation(title = "新对话", deviceId = null) {
  return apiRequest("/api/assistant/conversations", {
    method: "POST",
    body: JSON.stringify({ title, device_id: deviceId }),
  });
}

export function fetchAssistantConversation(conversationId) {
  return apiRequest(`/api/assistant/conversations/${conversationId}`);
}

export function deleteAssistantConversation(conversationId) {
  return apiRequest(`/api/assistant/conversations/${conversationId}`, { method: "DELETE" });
}

export function listAssistantConversationFiles(conversationId) {
  return apiRequest(`/api/assistant/conversations/${conversationId}/files`);
}

export function uploadAssistantConversationFile(conversationId, file) {
  const body = new FormData();
  body.append("file", file);
  return apiRequest(`/api/assistant/conversations/${conversationId}/files`, {
    method: "POST",
    body,
  });
}

export function deleteAssistantConversationFile(conversationId, fileId) {
  return apiRequest(
    `/api/assistant/conversations/${conversationId}/files/${encodeURIComponent(fileId)}`,
    { method: "DELETE" },
  );
}

export function generateAssistantConversationTitle(conversationId, firstMessage) {
  return apiRequest(`/api/assistant/conversations/${conversationId}/title`, {
    method: "POST",
    body: JSON.stringify({ first_message: firstMessage }),
  });
}

export function cancelAssistantRun(runId) {
  return apiRequest(`/api/assistant/runs/${runId}/cancel`, {
    method: "POST",
    body: "{}",
  });
}

async function assistantError(response) {
  try {
    const data = await response.json();
    return new Error(data?.detail || `HTTP ${response.status}`);
  } catch {
    return new Error(`HTTP ${response.status}`);
  }
}

export function messagesFromAssistantConversation(session) {
  const messages = [];
  const timelines = session?.metadata?.public_timelines || {};
  const snapshots = Object.fromEntries(Object.entries(
    session?.metadata?.public_run_snapshots || {},
  ).filter(([, snapshot]) => (
    !snapshot?.conversation_id || snapshot.conversation_id === session?.session_id
  )));
  const seenRunIds = new Set();

  function appendRun(run, snapshot = {}) {
    const runId = String(run?.run_id || snapshot?.run_id || "");
    if (!runId || seenRunIds.has(runId)) return;
    seenRunIds.add(runId);
    // Agno's context hook may persist `run.input` as a Message array/object.
    // The public snapshot is the stable, user-visible source of truth and must
    // win whenever it contains a string, otherwise an interrupted run loses
    // the user's bubble after the conversation is reloaded.
    const inputCandidates = [
      snapshot?.input,
      run?.metadata?.display_message,
      run?.input?.input_content,
      run?.input,
    ];
    const input = inputCandidates.find((value) => (
      typeof value === "string" && value.trim()
    ));
    if (typeof input === "string" && input.trim()) {
      messages.push({ id: `${runId}-user`, role: "user", content: input });
    }
    const timeline = Array.isArray(snapshot?.timeline)
      ? snapshot.timeline
      : Array.isArray(timelines[runId]) ? timelines[runId] : [];
    const timelineContent = timeline
      .filter((item) => item?.kind === "content")
      .map((item) => String(item.content || ""))
      .join("");
    const storedContent = typeof run?.content === "string" ? run.content : "";
    const content = timelineContent || String(snapshot?.content || "") || storedContent;
    const parts = timeline.reduce((currentParts, item) => {
      if (item?.kind === "content") {
        return applyAssistantRunEvent(currentParts, "RunContent", { content: item.content });
      }
      if (item?.kind === "event") {
        return applyAssistantRunEvent(currentParts, item.event, item);
      }
      return currentParts;
    }, []);
    if (content.trim() || parts.length) {
      messages.push({
        id: `${runId}-assistant`,
        role: "assistant",
        content,
        parts,
      });
    }
  }

  const runEntries = (session?.runs || []).map((run, index) => ({
    run,
    snapshot: snapshots[run?.run_id] || {},
    fallbackOrder: index,
  }));
  const knownRunIds = new Set(runEntries.map(({ run }) => String(run?.run_id || "")));
  for (const snapshot of Object.values(snapshots)) {
    if (!knownRunIds.has(String(snapshot?.run_id || ""))) {
      runEntries.push({ run: null, snapshot, fallbackOrder: runEntries.length });
    }
  }
  runEntries.sort((left, right) => {
    const leftTime = Number(left.snapshot?.created_at || left.run?.created_at || 0);
    const rightTime = Number(right.snapshot?.created_at || right.run?.created_at || 0);
    return leftTime && rightTime && leftTime !== rightTime
      ? leftTime - rightTime
      : left.fallbackOrder - right.fallbackOrder;
  });
  for (const { run, snapshot } of runEntries) appendRun(run, snapshot);
  return messages.reduce((grouped, message) => {
    const previous = grouped.at(-1);
    if (message.role !== "assistant" || previous?.role !== "assistant") {
      grouped.push(message);
      return grouped;
    }
    const previousParts = previous.parts?.length
      ? previous.parts
      : previous.content ? [{ type: "content", content: previous.content }] : [];
    const currentParts = message.parts?.length
      ? message.parts
      : message.content ? [{ type: "content", content: message.content }] : [];
    grouped[grouped.length - 1] = {
      ...previous,
      content: [previous.content, message.content].filter(Boolean).join("\n\n"),
      parts: [...previousParts, ...currentParts],
    };
    return grouped;
  }, []);
}

function toolEventDetails(data) {
  const tool = data?.tool || data?.tool_call || {};
  return {
    id: String(tool.tool_call_id || data?.tool_call_id || tool.id || ""),
    name: String(data?.tool_display_name || tool.tool_name || tool.name || "业务工具"),
  };
}

export function applyAssistantRunEvent(currentParts, eventName, data = {}) {
  const parts = [...(currentParts || [])];
  if (eventName === "CapabilityWarning") {
    parts.push({
      type: "warning",
      status: "warning",
      label: String(data.message || "扩展能力当前不可用，已降级继续"),
    });
    return parts;
  }
  if (eventName === "RunContent" && typeof data.content === "string" && data.content) {
    const previous = parts.at(-1);
    if (previous?.type === "content") {
      parts[parts.length - 1] = { ...previous, content: previous.content + data.content };
    } else {
      parts.push({ type: "content", content: data.content });
    }
    return parts;
  }
  if (eventName === "ReasoningStarted") {
    parts.push({ type: "reasoning", status: "running", label: "正在分析" });
    return parts;
  }
  if (eventName === "ReasoningCompleted") {
    const index = parts.findLastIndex((part) => part.type === "reasoning" && part.status === "running");
    if (index >= 0) parts[index] = { ...parts[index], status: "completed", label: "分析完成" };
    return parts;
  }
  if (!["ToolCallStarted", "ToolCallCompleted", "ToolCallError"].includes(eventName)) return parts;
  const details = toolEventDetails(data);
  const status = eventName === "ToolCallStarted" ? "running" : eventName === "ToolCallError" ? "error" : "completed";
  const index = parts.findLastIndex((part) => (
    part.type === "tool"
    && ((details.id && part.id === details.id) || (!details.id && part.name === details.name && part.status === "running"))
  ));
  if (index >= 0) {
    parts[index] = { ...parts[index], status };
  } else {
    parts.push({ type: "tool", ...details, status });
  }
  return parts;
}

async function readAssistantEventStream(response, { onDelta, onRunId, onEvent } = {}) {
  if (!response.body) throw new Error("当前浏览器不支持流式回答");
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let runId = "";

  function consumeEvent(eventText) {
    let eventName = "message";
    let dataText = "";
    for (const line of eventText.split("\n")) {
      if (line.startsWith("event:")) eventName = line.slice(6).trim();
      if (line.startsWith("data:")) dataText += line.slice(5).trim();
    }
    if (!dataText) return;
    const data = JSON.parse(dataText);
    onEvent?.(eventName, data);
    if (data?.run_id && !runId) {
      runId = String(data.run_id);
      onRunId?.(runId);
    }
    if (eventName === "RunContent" && typeof data?.content === "string") onDelta?.(data.content);
    if (eventName === "RunError") throw new Error(data?.error || "运维助手执行失败");
  }

  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done }).replaceAll("\r\n", "\n");
      const events = buffer.split("\n\n");
      buffer = events.pop() || "";
      for (const eventText of events) consumeEvent(eventText);
      if (done) {
        if (buffer.trim()) consumeEvent(buffer);
        break;
      }
    }
  } finally {
    try {
      await reader.cancel();
    } catch {
      // The request may already have closed naturally or through AbortController.
    }
  }
  return { runId };
}

export async function streamAssistantMessage(
  conversationId,
  message,
  target,
  { onDelta, onRunId, onEvent, signal, mode = "standard" } = {},
) {
  const response = await apiFetch(`/api/assistant/conversations/${conversationId}/messages`, {
    method: "POST",
    headers: { Accept: "text/event-stream" },
    body: JSON.stringify({ message, target, mode }),
    signal,
  });
  if (!response.ok) throw await assistantError(response);
  return readAssistantEventStream(response, { onDelta, onRunId, onEvent });
}

export async function continueAssistantRun(
  conversationId,
  runId,
  tools,
  approved,
  { onDelta, onEvent, signal, mode = "standard" } = {},
) {
  const response = await apiFetch(
    `/api/assistant/conversations/${conversationId}/runs/${runId}/continue`,
    {
      method: "POST",
      headers: { Accept: "text/event-stream" },
      body: JSON.stringify({ mode, tools, approved }),
      signal,
    },
  );
  if (!response.ok) throw await assistantError(response);
  return readAssistantEventStream(response, { onDelta, onEvent });
}
