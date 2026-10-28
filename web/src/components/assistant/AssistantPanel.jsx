import { useEffect, useMemo, useRef, useState } from "react";
import {
  Bot,
  Blocks,
  BrainCircuit,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  CircleStop,
  FilePlus2,
  FileText,
  FolderOpen,
  Gauge,
  History,
  LoaderCircle,
  MessageSquarePlus,
  PlugZap,
  Send,
  ShieldCheck,
  Square,
  Trash2,
  Wrench,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  compareConversationsDescending,
  conversationTimestamp,
  readActiveConversation,
  rememberActiveConversation,
} from "../../lib/assistantHistory.js";
import {
  applyAssistantRunEvent,
  cancelAssistantRun,
  continueAssistantRun,
  createAssistantMcpServer,
  createAssistantConversation,
  deleteAssistantCapability,
  deleteAssistantConversation,
  deleteAssistantConversationFile,
  fetchAssistantCapabilities,
  fetchAssistantConversation,
  fetchAssistantStatus,
  generateAssistantConversationTitle,
  listAssistantConversations,
  listAssistantConversationFiles,
  messagesFromAssistantConversation,
  probeAssistantMcpServer,
  probeSavedAssistantMcpServer,
  setAssistantCapabilityEnabled,
  streamAssistantMessage,
  uploadAssistantConversationFile,
  uploadAssistantSkill,
} from "../../services/assistantService.js";

const welcomeMessage = {
  role: "assistant",
  localOnly: true,
  content: "我可以为你提供 Linux、网络设备、防火墙和交换机相关的运维建议，并辅助生成常用排查命令。",
};

const MAX_CONVERSATION_FILES = 10;

const assistantModes = [
  { id: "standard", label: "标准模式", shortLabel: "标准", description: "关闭思考" },
  { id: "enhanced", label: "增强模式", shortLabel: "增强", description: "开启思考" },
];

const markdownComponents = {
  a({ children, node: _node, ...props }) {
    return (
      <a {...props} target="_blank" rel="noreferrer noopener">
        {children}
      </a>
    );
  },
  table({ children, node: _node, ...props }) {
    return (
      <div className="assistant-markdown-table">
        <table {...props}>{children}</table>
      </div>
    );
  },
};

function uniqueId(prefix) {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return `${prefix}-${crypto.randomUUID()}`;
  }
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function createMessage(role, content) {
  return { id: uniqueId(role), role, content };
}

function createConversation(session, files = [], fallbackScopeKey = "public") {
  const deviceId = session.device_id || session.session_data?.device_id || null;
  const restoredMessages = messagesFromAssistantConversation(session);
  return {
    id: session.session_id,
    title: session.title || "新对话",
    messages: restoredMessages.length
      ? restoredMessages
      : [{ ...welcomeMessage, id: uniqueId("welcome") }],
    files,
    deviceId,
    scopeKey: deviceId ? `device:${deviceId}` : fallbackScopeKey,
    loaded: Object.prototype.hasOwnProperty.call(session, "runs"),
    updatedAt: conversationTimestamp(session),
  };
}

function initialConversationState() {
  return { activeId: null, items: [] };
}

function conversationTitle(content) {
  const title = String(content || "").replace(/\s+/g, " ").trim();
  return title.length > 18 ? `${title.slice(0, 18)}…` : title || "新会话";
}

function assistantTarget(target) {
  if (!target) return null;
  const address = String(target.host || target.address || "").trim();
  if (!address) return null;
  return {
    session_id: String(target.serverSessionId || ""),
    device_id: target.deviceId ? String(target.deviceId) : null,
    protocol: String(target.protocol || "").toLowerCase(),
    address,
    port: Number(target.port) || null,
    remark: String(target.remark || ""),
    device_type: String(target.deviceType || "server"),
    device_profile: String(target.deviceProfile || "linux"),
  };
}

function formatConversationTime(timestamp) {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(timestamp);
}

function formatFileSize(bytes) {
  const size = Number(bytes || 0);
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.ceil(size / 1024)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function AssistantRunParts({ message }) {
  if (!message.parts?.length) {
    if (!message.content && message.streaming) return <span className="assistant-waiting"><i /><i /><i /></span>;
    return message.content ? (
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
        {message.content}
      </ReactMarkdown>
    ) : null;
  }
  return (
    <div className="assistant-run-parts">
      {message.parts.map((part, index) => {
        if (part.type === "content") {
          return (
            <div className="assistant-run-content" key={`content-${index}`}>
              <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                {part.content}
              </ReactMarkdown>
            </div>
          );
        }
        const running = part.status === "running";
        const failed = part.status === "error";
        const warning = part.type === "warning";
        return (
          <div className={`assistant-run-step ${part.type} ${part.status}`} key={`${part.type}-${part.id || index}`}>
            {running ? <LoaderCircle className="spin" size={14} /> : failed || warning ? <CircleAlert size={14} /> : <CheckCircle2 size={14} />}
            {warning ? null : part.type === "tool" ? <Wrench size={13} /> : <BrainCircuit size={13} />}
            <span>{part.type === "tool" ? `${running ? "正在调用" : failed ? "调用失败" : "调用完成"}：${part.name}` : part.label}</span>
          </div>
        );
      })}
      {message.streaming && message.parts.at(-1)?.type !== "reasoning" ? <span className="assistant-waiting inline"><i /><i /><i /></span> : null}
    </div>
  );
}

export function AssistantPanel({ target }) {
  const [conversationState, setConversationState] = useState(initialConversationState);
  const [historyPage, setHistoryPage] = useState({ scope: "", page: 1, hasMore: false, loading: false });
  const [draft, setDraft] = useState("");
  const [status, setStatus] = useState({
    checking: true, ready: false, skills: 0, toolkits: 0, mcpServers: 0,
    executionEnabled: false,
  });
  const [runningConversationIds, setRunningConversationIds] = useState(() => new Set());
  const [error, setError] = useState("");
  const [mode, setMode] = useState("standard");
  const [openMenu, setOpenMenu] = useState("");
  const [mcpMoreOpen, setMcpMoreOpen] = useState(false);
  const [pluginView, setPluginView] = useState("tools");
  const [mcpView, setMcpView] = useState("list");
  const [mcpError, setMcpError] = useState("");
  const [mcpProbe, setMcpProbe] = useState(null);
  const [selectedMcpId, setSelectedMcpId] = useState("");
  const [parsingFiles, setParsingFiles] = useState(false);
  const [approval, setApproval] = useState(null);
  const [capabilities, setCapabilities] = useState([]);
  const [capabilityBusy, setCapabilityBusy] = useState("");
  const [mcpDraft, setMcpDraft] = useState({
    name: "", url: "", tools: "", transport: "streamable-http", headers: "",
  });
  const messageListRef = useRef(null);
  const runStatesRef = useRef(new Map());
  const fileInputRef = useRef(null);
  const skillInputRef = useRef(null);
  const panelRef = useRef(null);
  const activeByScopeRef = useRef(new Map());
  const historyLoadingRef = useRef(false);
  const targetContext = useMemo(() => assistantTarget(target), [target]);
  const conversationDeviceId = targetContext?.device_id || null;
  const conversationScopeKey = conversationDeviceId
    ? `device:${conversationDeviceId}`
    : "public";
  const scopeRef = useRef(conversationScopeKey);
  scopeRef.current = conversationScopeKey;
  const activeConversation = useMemo(
    () => conversationState.items.find((item) => (
      item.id === conversationState.activeId && item.scopeKey === conversationScopeKey
    )),
    [conversationScopeKey, conversationState],
  );
  const selectedMode = assistantModes.find((item) => item.id === mode) || assistantModes[0];
  const activeSending = Boolean(activeConversation && runningConversationIds.has(activeConversation.id));

  function updateConversation(conversationId, update, touch = true) {
    setConversationState((current) => ({
      ...current,
      items: current.items.map((conversation) => (
        conversation.id === conversationId
          ? { ...conversation, ...update(conversation), ...(touch ? { updatedAt: Date.now() } : {}) }
          : conversation
      )),
    }));
  }

  function setConversationRunning(conversationId, running) {
    setRunningConversationIds((current) => {
      const next = new Set(current);
      if (running) next.add(conversationId);
      else next.delete(conversationId);
      return next;
    });
  }

  function applyRunEvent(conversationId, messageId, eventName, data) {
    updateConversation(conversationId, (conversation) => ({
      messages: conversation.messages.map((message) => (
        message.id === messageId
          ? { ...message, parts: applyAssistantRunEvent(message.parts, eventName, data) }
          : message
      )),
    }));
  }

  async function refreshStatus() {
    try {
      const result = await fetchAssistantStatus();
      setStatus({
        checking: false,
        ready: Boolean(result.ready),
        skills: Number(result.capabilities?.skills || 0),
        toolkits: Number(result.capabilities?.toolkits || 0),
        mcpServers: Number(result.capabilities?.mcp_servers || 0),
        executionEnabled: result.capabilities?.execution_enabled === true,
      });
      try {
        const capabilityResult = await fetchAssistantCapabilities();
        setCapabilities(capabilityResult.data || []);
      } catch {
        // Status remains useful while talking to an older Agent during a rolling upgrade.
      }
    } catch {
      setStatus((current) => ({ ...current, checking: false, ready: false }));
    }
  }

  async function toggleCapability(item) {
    if (capabilityBusy) return;
    setCapabilityBusy(item.id);
    setError("");
    try {
      const updated = await setAssistantCapabilityEnabled(item.id, !item.enabled);
      setCapabilities((current) => current.map((entry) => (
        entry.id === updated.id ? updated : entry
      )));
      await refreshStatus();
    } catch (capabilityError) {
      setError(capabilityError.message || "能力设置更新失败");
    } finally {
      setCapabilityBusy("");
    }
  }

  async function handleSkillImport(event) {
    const [file] = Array.from(event.target.files || []);
    event.target.value = "";
    if (!file) return;
    setCapabilityBusy("skill-import");
    setError("");
    try {
      await uploadAssistantSkill(file);
      await refreshStatus();
    } catch (capabilityError) {
      setError(capabilityError.message || "Skill 导入失败");
    } finally {
      setCapabilityBusy("");
    }
  }

  async function handleDeleteCapability(item) {
    if (capabilityBusy) return;
    const kindLabel = item.kind === "mcp" ? "MCP 连接" : "Skill";
    if (!window.confirm(`确定删除${kindLabel}“${item.name}”吗？此操作无法撤销。`)) return;
    setCapabilityBusy(`delete:${item.id}`);
    setError("");
    setMcpError("");
    try {
      await deleteAssistantCapability(item.id);
      setCapabilities((current) => current.filter((entry) => entry.id !== item.id));
      await refreshStatus();
    } catch (capabilityError) {
      const message = capabilityError.message || `${kindLabel}删除失败`;
      if (item.kind === "mcp") setMcpError(message);
      else setError(message);
    } finally {
      setCapabilityBusy("");
    }
  }

  async function submitMcp() {
    if (!mcpDraft.name.trim() || !mcpDraft.url.trim()) return;
    setCapabilityBusy("mcp-import");
    setMcpError("");
    setMcpProbe({ status: "checking", tools: [] });
    try {
      const created = await createAssistantMcpServer(mcpDraftPayload());
      const discoveredTools = created.discovered_tools || created.config?.discovered_tools || [];
      setMcpProbe({ status: "available", tools: discoveredTools });
      setMcpDraft({ name: "", url: "", tools: "", transport: "streamable-http", headers: "" });
      setMcpMoreOpen(false);
      await refreshStatus();
      setSelectedMcpId(created.id);
      setMcpView("list");
    } catch (capabilityError) {
      setMcpError(capabilityError.message || "MCP 导入失败");
      setMcpProbe({ status: "unavailable", tools: [] });
    } finally {
      setCapabilityBusy("");
    }
  }

  function mcpDraftPayload() {
    const tools = mcpDraft.tools.split(",").map((item) => item.trim()).filter(Boolean);
    let headers = {};
    if (mcpDraft.headers.trim()) {
      headers = JSON.parse(mcpDraft.headers);
      if (!headers || Array.isArray(headers) || typeof headers !== "object") {
        throw new Error("请求头必须是 JSON 对象");
      }
    }
    return {
      name: mcpDraft.name.trim(),
      url: mcpDraft.url.trim(),
      tools,
      transport: mcpDraft.transport,
      headers,
      enabled: true,
    };
  }

  function updateMcpDraft(field, value) {
    setMcpDraft((current) => ({ ...current, [field]: value }));
    setMcpProbe(null);
    setMcpError("");
  }

  async function testMcpDraft() {
    if (!mcpDraft.name.trim() || !mcpDraft.url.trim() || capabilityBusy) return;
    setCapabilityBusy("mcp-probe");
    setMcpError("");
    setMcpProbe({ status: "checking", tools: [] });
    try {
      const result = await probeAssistantMcpServer(mcpDraftPayload());
      setMcpProbe({ status: "available", tools: result.tools || [] });
    } catch (capabilityError) {
      setMcpProbe({ status: "unavailable", tools: [] });
      setMcpError(capabilityError.message || "MCP 连通性测试失败");
    } finally {
      setCapabilityBusy("");
    }
  }

  async function inspectMcp(item) {
    if (selectedMcpId === item.id) {
      setSelectedMcpId("");
      return;
    }
    setSelectedMcpId(item.id);
    setCapabilityBusy(`probe:${item.id}`);
    setMcpError("");
    try {
      const updated = await probeSavedAssistantMcpServer(item.id);
      setCapabilities((current) => current.map((entry) => (
        entry.id === updated.id ? updated : entry
      )));
    } catch (capabilityError) {
      const message = capabilityError.message || `MCP“${item.name}”连通性测试失败`;
      setCapabilities((current) => current.map((entry) => (
        entry.id === item.id
          ? { ...entry, runtime_status: "unavailable", runtime_error: message }
          : entry
      )));
      setMcpError(message);
    } finally {
      setCapabilityBusy("");
    }
  }

  useEffect(() => {
    void refreshStatus();
    const timer = window.setInterval(refreshStatus, 10_000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const result = await listAssistantConversations(conversationDeviceId);
        let sessions = result.data || [];
        if (cancelled) return;
        const rememberedId = activeByScopeRef.current.get(conversationScopeKey)
          || readActiveConversation(conversationScopeKey);
        // A remembered conversation may be on an older page after a clock
        // rollback. Restore it directly, but never cross device boundaries.
        if (rememberedId && !sessions.some((item) => item.session_id === rememberedId)) {
          try {
            const remembered = await fetchAssistantConversation(rememberedId);
            const deviceId = remembered.device_id || remembered.session_data?.device_id || null;
            if (deviceId === conversationDeviceId) sessions = [...sessions, remembered];
            else rememberActiveConversation(conversationScopeKey, null);
          } catch (restoreError) {
            if (restoreError.status === 404) rememberActiveConversation(conversationScopeKey, null);
          }
        }
        if (cancelled) return;
        if (!sessions.length) {
          sessions = [await createAssistantConversation("新对话", conversationDeviceId)];
        }
        if (cancelled) return;
        const summaries = sessions.map((session) => (
          createConversation(session, [], conversationScopeKey)
        ));
        const validIds = new Set(summaries.map((item) => item.id));
        const activeId = validIds.has(rememberedId) ? rememberedId : summaries[0].id;
        activeByScopeRef.current.set(conversationScopeKey, activeId);
        rememberActiveConversation(conversationScopeKey, activeId);
        setHistoryPage({ scope: conversationScopeKey, page: 1,
          hasMore: result.has_more ?? (result.data?.length === 50), loading: false });
        setConversationState((current) => {
          const existing = new Map(
            current.items
              .filter((item) => item.scopeKey === conversationScopeKey)
              .map((item) => [item.id, item]),
          );
          const scopedItems = summaries.map((summary) => {
            const cached = existing.get(summary.id);
            return cached
              ? { ...cached, title: summary.title, updatedAt: Math.max(cached.updatedAt, summary.updatedAt) }
              : summary;
          });
          return {
            activeId,
            items: [
              ...current.items.filter((item) => item.scopeKey !== conversationScopeKey),
              ...scopedItems,
            ],
          };
        });
        const [active, fileResult] = await Promise.all([
          fetchAssistantConversation(activeId),
          listAssistantConversationFiles(activeId),
        ]);
        if (cancelled) return;
        setConversationState((current) => {
          const currentScopedActive = current.items.some((item) => (
            item.id === current.activeId && item.scopeKey === conversationScopeKey
          )) ? current.activeId : active.session_id;
          return {
            activeId: currentScopedActive,
            items: current.items.map((item) => (
              item.id === active.session_id && item.scopeKey === conversationScopeKey
                ? createConversation(active, fileResult.data || [], conversationScopeKey)
                : item
            )),
          };
        });
      } catch (loadError) {
        if (cancelled) return;
        setError(loadError.message || "会话加载失败");
      }
    })();
    return () => { cancelled = true; };
  }, [conversationDeviceId, conversationScopeKey]);

  async function loadMoreConversations() {
    if (historyLoadingRef.current || historyPage.scope !== conversationScopeKey || !historyPage.hasMore) return;
    const scope = conversationScopeKey;
    const page = historyPage.page + 1;
    historyLoadingRef.current = true;
    setHistoryPage((current) => ({ ...current, loading: true }));
    try {
      const result = await listAssistantConversations(conversationDeviceId, page);
      if (scopeRef.current !== scope) return;
      setConversationState((current) => {
        const ids = new Set(current.items.map((item) => item.id));
        return { ...current, items: [...current.items, ...(result.data || [])
          .filter((session) => !ids.has(session.session_id))
          .map((session) => createConversation(session, [], scope))] };
      });
      setHistoryPage({ scope, page, hasMore: result.has_more ?? (result.data?.length === 50), loading: false });
    } catch (loadError) {
      if (scopeRef.current === scope) setError(loadError.message || "历史会话加载失败");
    } finally {
      historyLoadingRef.current = false;
      setHistoryPage((current) => current.scope === scope ? { ...current, loading: false } : current);
    }
  }

  useEffect(() => {
    return () => {
      for (const runState of runStatesRef.current.values()) {
        runState.reason = "unmount";
        runState.controller.abort();
      }
      runStatesRef.current.clear();
    };
  }, []);

  useEffect(() => {
    const messageList = messageListRef.current;
    if (messageList) messageList.scrollTop = messageList.scrollHeight;
  }, [activeConversation?.messages, activeSending]);

  useEffect(() => {
    function closeMenus(event) {
      if (!(event.target instanceof Element)) return;
      if (
        event.target.closest("[data-assistant-menu-surface]")
        || event.target.closest("[data-assistant-menu-trigger]")
      ) return;
      setOpenMenu("");
    }
    function closeOnEscape(event) {
      if (event.key === "Escape") setOpenMenu("");
    }
    document.addEventListener("pointerdown", closeMenus);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeMenus);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, []);

  async function submitMessage(event) {
    event?.preventDefault();
    const content = draft.trim();
    if (!content || activeSending || !status.ready || !activeConversation) return;

    const conversationId = activeConversation.id;
    const userMessage = createMessage("user", content);
    const assistantMessage = { ...createMessage("assistant", ""), streaming: true, parts: [] };
    updateConversation(conversationId, (conversation) => ({
      title: ["新对话", "新会话"].includes(conversation.title)
        ? conversationTitle(content)
        : conversation.title,
      messages: [...conversation.messages, userMessage, assistantMessage],
    }));
    setDraft("");
    setConversationRunning(conversationId, true);
    setError("");
    setOpenMenu("");
    const controller = new AbortController();
    const runState = { controller, runId: "", reason: "", mode };
    runStatesRef.current.set(conversationId, runState);
    let receivedContent = "";
    let pausedForApproval = false;
    try {
      await streamAssistantMessage(conversationId, content, targetContext, {
        mode,
        signal: controller.signal,
        onRunId(runId) {
          runState.runId = runId;
        },
        onEvent(eventName, data) {
          if (eventName === "RunContent" && typeof data.content === "string") {
            receivedContent += data.content;
          }
          applyRunEvent(conversationId, assistantMessage.id, eventName, data);
          if (eventName === "RunPaused") {
            const tools = (data.tools || []).filter((tool) => tool.requires_confirmation);
            if (!tools.length) return;
            pausedForApproval = true;
            setApproval({
              conversationId,
              runId: String(data.run_id || runState.runId),
              mode,
              messageId: assistantMessage.id,
              tools,
            });
          }
        },
      });
      if (!receivedContent.trim() && !pausedForApproval) {
        throw new Error("运维助手响应流已结束，但没有返回正文；模型或扩展工具链可能未能启动");
      }
      if (activeConversation.title === "新对话" || activeConversation.title === "新会话") {
        void generateAssistantConversationTitle(conversationId, content).then((titleResult) => {
          updateConversation(conversationId, () => ({ title: titleResult.title }));
        }).catch(() => {
          updateConversation(conversationId, () => ({ title: conversationTitle(content) }));
        });
      }
    } catch (requestError) {
      const wasAborted = requestError.name === "AbortError" || controller.signal.aborted;
      if (wasAborted && runState.reason === "stop") {
        // Keep the partial answer visible; the server owns durable run state.
      } else if (!wasAborted) {
        setError(requestError.message || "运维助手暂时无法回答，请稍后重试");
        void refreshStatus();
      }
    } finally {
      updateConversation(conversationId, (conversation) => ({
        messages: conversation.messages
          .map((message) => (
            message.id === assistantMessage.id
              ? { ...message, content: receivedContent, streaming: false }
              : message
          ))
          .filter((message) => (
            message.id !== assistantMessage.id
            // An approval pause is not an empty completed reply. Continuation
            // events still target this message, even before its first content.
            || pausedForApproval
            || message.content.trim()
            || message.parts?.length
          )),
      }));
      if (runStatesRef.current.get(conversationId) === runState) {
        runStatesRef.current.delete(conversationId);
        setConversationRunning(conversationId, false);
      }
      void refreshStatus();
    }
  }

  function stopGeneration() {
    if (!activeConversation) return;
    const runState = runStatesRef.current.get(activeConversation.id);
    if (!runState) return;
    runState.reason = "stop";
    if (runState.runId) void cancelAssistantRun(runState.runId).catch(() => {});
    runState.controller.abort();
  }

  function cancelConversationRequest(conversationId, reason) {
    const runState = runStatesRef.current.get(conversationId);
    if (!runState) return;
    runState.reason = reason;
    runState.controller.abort();
  }

  async function resolveApproval(approved) {
    if (!approval || runStatesRef.current.has(approval.conversationId)) return;
    if (!status.executionEnabled) {
      setApproval(null);
      return;
    }
    const pending = approval;
    const messageId = pending.messageId;
    updateConversation(pending.conversationId, (conversation) => ({
      // Reloading a device's history while paused can replace local message
      // IDs. Recreate the live reply if the pending approval's ID is absent.
      messages: conversation.messages.some((message) => message.id === messageId)
        ? conversation.messages.map((message) => (
          message.id === messageId ? { ...message, streaming: true } : message
        ))
        : [...conversation.messages, { id: messageId, role: "assistant", content: "", parts: [], streaming: true }],
    }));
    setApproval(null);
    setConversationRunning(pending.conversationId, true);
    setError("");
    const controller = new AbortController();
    const runState = { controller, runId: pending.runId, reason: "", mode: pending.mode };
    runStatesRef.current.set(pending.conversationId, runState);
    let pausedForApproval = false;
    try {
      await continueAssistantRun(
        pending.conversationId,
        pending.runId,
        pending.tools,
        approved,
        {
          mode: pending.mode,
          signal: controller.signal,
          onEvent(eventName, data) {
            applyRunEvent(pending.conversationId, messageId, eventName, data);
            if (eventName === "RunPaused") {
              const tools = (data.tools || []).filter((tool) => tool.requires_confirmation);
              if (tools.length) {
                pausedForApproval = true;
                setApproval({
                  conversationId: pending.conversationId,
                  runId: String(data.run_id || pending.runId),
                  mode: pending.mode,
                  messageId,
                  tools,
                });
              }
            }
          },
        },
      );
    } catch (approvalError) {
      if (!controller.signal.aborted) {
        setError(approvalError.message || "工具审批处理失败");
      }
    } finally {
      updateConversation(pending.conversationId, (conversation) => ({
        messages: conversation.messages
          .map((message) => (
            message.id === messageId
              ? { ...message, streaming: false }
              : message
          ))
          .filter((message) => (
            message.id !== messageId || pausedForApproval || message.content.trim() || message.parts?.length
          )),
      }));
      if (runStatesRef.current.get(pending.conversationId) === runState) {
        runStatesRef.current.delete(pending.conversationId);
        setConversationRunning(pending.conversationId, false);
      }
      void refreshStatus();
    }
  }

  async function startNewConversation() {
    setDraft("");
    setError("");
    setOpenMenu("");
    try {
      const conversation = createConversation(
        await createAssistantConversation("新对话", conversationDeviceId),
        [],
        conversationScopeKey,
      );
      activeByScopeRef.current.set(conversationScopeKey, conversation.id);
      rememberActiveConversation(conversationScopeKey, conversation.id);
      setConversationState((current) => ({
        activeId: conversation.id,
        items: [conversation, ...current.items],
      }));
    } catch (createError) {
      setError(createError.message || "新建会话失败");
    }
  }

  async function switchConversation(conversationId) {
    if (conversationId === conversationState.activeId) {
      setOpenMenu("");
      return;
    }
    setConversationState((current) => ({ ...current, activeId: conversationId }));
    activeByScopeRef.current.set(conversationScopeKey, conversationId);
    rememberActiveConversation(conversationScopeKey, conversationId);
    setDraft("");
    setError("");
    setOpenMenu("");
    const cached = conversationState.items.find((item) => item.id === conversationId);
    if (cached?.loaded) return;
    try {
      const [session, fileResult] = await Promise.all([
        fetchAssistantConversation(conversationId),
        listAssistantConversationFiles(conversationId),
      ]);
      updateConversation(
        conversationId,
        () => createConversation(session, fileResult.data || [], conversationScopeKey),
        false,
      );
    } catch (loadError) {
      setError(loadError.message || "历史会话加载失败");
    }
  }

  async function removeConversation(conversationId) {
    const conversation = conversationState.items.find((item) => item.id === conversationId);
    if (!conversation) return;
    if (!window.confirm(`确定删除会话“${conversation.title}”吗？\n会话记录和文件区内容将一并删除。`)) return;
    setError("");
    try {
      let replacement = null;
      const scopedItems = conversationState.items.filter(
        (item) => item.scopeKey === conversationScopeKey,
      );
      if (scopedItems.length === 1) {
        replacement = createConversation(
          await createAssistantConversation("新对话", conversationDeviceId),
          [],
          conversationScopeKey,
        );
      }
      cancelConversationRequest(conversationId, "delete-conversation");
      await deleteAssistantConversation(conversationId);
      // Deletion changes offset pagination. Re-read from page 2 on the next
      // load-more click and deduplicate, rather than skipping an unseen row.
      setHistoryPage((current) => current.scope === conversationScopeKey
        ? { ...current, page: 1 } : current);
      if (approval?.conversationId === conversationId) setApproval(null);
      setConversationState((current) => {
        const remaining = current.items.filter((item) => item.id !== conversationId);
        const items = replacement ? [replacement, ...remaining] : remaining;
        const scopedRemaining = items.filter((item) => item.scopeKey === conversationScopeKey);
        const activeId = current.activeId === conversationId
          ? (scopedRemaining[0]?.id || null)
          : current.activeId;
        if (activeId) activeByScopeRef.current.set(conversationScopeKey, activeId);
        rememberActiveConversation(conversationScopeKey, activeId);
        return { activeId, items };
      });
      setOpenMenu("");
    } catch (deleteError) {
      setError(deleteError.message || "删除会话失败");
    }
  }

  async function handleFileSelection(event) {
    const files = [...(event.target.files || [])];
    event.target.value = "";
    if (!files.length || !activeConversation || parsingFiles) return;
    const availableSlots = MAX_CONVERSATION_FILES - activeConversation.files.length;
    if (availableSlots <= 0) {
      setError(`每个会话最多上传 ${MAX_CONVERSATION_FILES} 个文件`);
      return;
    }

    setParsingFiles(true);
    setError("");
    try {
      for (const file of files.slice(0, availableSlots)) {
        await uploadAssistantConversationFile(activeConversation.id, file);
      }
    } catch (uploadError) {
      setError(uploadError.message || "文件上传失败");
    } finally {
      try {
        const result = await listAssistantConversationFiles(activeConversation.id);
        updateConversation(activeConversation.id, (conversation) => ({
          files: result.data || conversation.files,
        }));
      } catch {
        // Keep the existing file list when a refresh fails after an upload attempt.
      }
      setParsingFiles(false);
    }
  }

  async function removeConversationFile(fileId) {
    if (!activeConversation || parsingFiles) return;
    setParsingFiles(true);
    setError("");
    try {
      await deleteAssistantConversationFile(activeConversation.id, fileId);
      updateConversation(activeConversation.id, (conversation) => ({
        files: conversation.files.filter((item) => item.id !== fileId),
      }));
    } catch (deleteError) {
      setError(deleteError.message || "文件删除失败");
    } finally {
      setParsingFiles(false);
    }
  }

  function handleKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void submitMessage();
    }
  }

  function toggleMenu(name) {
    if (name === "mcp" && openMenu !== "mcp") {
      setMcpView("list");
      setMcpError("");
    }
    if (name === "plugins" && openMenu !== "plugins") setPluginView("tools");
    setOpenMenu((current) => (current === name ? "" : name));
  }

  function closeMenuFromPanel(event) {
    if (!(event.target instanceof Element)) return;
    if (
      event.target.closest("[data-assistant-menu-surface]")
      || event.target.closest("[data-assistant-menu-trigger]")
    ) return;
    setOpenMenu("");
  }

  const statusText = status.checking ? "正在检查" : status.ready ? "本地模型已就绪" : "本地模型未就绪";
  const executionCapability = capabilities.find((item) => item.id === "control:agent-execution");
  const messages = activeConversation?.messages || [];
  const conversationFiles = activeConversation?.files || [];
  const sortedConversations = conversationState.items
    .filter((item) => item.scopeKey === conversationScopeKey)
    .sort(compareConversationsDescending);

  return (
    <section
      className="assistant-panel-view"
      aria-label="运维助手内容"
      ref={panelRef}
      onPointerDown={closeMenuFromPanel}
    >
      <div className="assistant-header">
        <div className="assistant-identity">
          <span className="assistant-avatar"><Bot size={18} /></span>
          <span>
            <strong>运维助手</strong>
            <small className={status.ready ? "ready" : "offline"}>
              {status.ready ? <CircleStop size={8} fill="currentColor" /> : null}
              {statusText} · {status.executionEnabled ? "Agent 执行已开启" : "模型问答"}
            </small>
          </span>
        </div>
        <div className="assistant-actions">
          <button type="button" onClick={startNewConversation} aria-label="新会话" title="新会话">
            <MessageSquarePlus size={16} />
          </button>
          <button
            type="button"
            data-assistant-menu-trigger
            className={openMenu === "history" ? "active" : ""}
            onClick={() => toggleMenu("history")}
            aria-label="历史会话"
            aria-expanded={openMenu === "history"}
            title="历史会话"
          >
            <History size={16} />
          </button>
        </div>
        {openMenu === "history" ? (
          <div
            className="assistant-history-menu"
            role="menu"
            aria-label="历史会话"
            data-assistant-menu-surface
          >
            <strong>历史会话 · 最近更新在前</strong>
            <div>
              {sortedConversations.map((conversation) => (
                <div
                  className={conversation.id === conversationState.activeId ? "active" : ""}
                  key={conversation.id}
                >
                  <button type="button" role="menuitem" onClick={() => switchConversation(conversation.id)}>
                    <span>{conversation.title}</span>
                    <time title={new Date(conversation.updatedAt).toLocaleString("zh-CN")}>
                      {formatConversationTime(conversation.updatedAt)}
                    </time>
                  </button>
                  <button
                    type="button"
                    className="delete"
                    aria-label={`删除会话 ${conversation.title}`}
                    title="删除会话"
                    onClick={() => void removeConversation(conversation.id)}
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              ))}
            </div>
            {historyPage.scope === conversationScopeKey && historyPage.hasMore ? (
              <button type="button" className="assistant-history-more"
                disabled={historyPage.loading} onClick={loadMoreConversations}>
                {historyPage.loading ? "正在加载…" : "加载更早会话"}
              </button>
            ) : null}
          </div>
        ) : null}
      </div>

      <div className="assistant-messages" ref={messageListRef} aria-live="polite">
        {messages.map((message) => (
          <article
            className={`assistant-message ${message.role}${message.streaming ? " streaming" : ""}`}
            key={message.id}
          >
            <span>{message.role === "user" ? "你" : "助手"}</span>
            <div className={message.role === "assistant" ? "assistant-markdown" : ""}>
              {message.role === "assistant"
                ? <AssistantRunParts message={message} />
                : message.content}
            </div>
          </article>
        ))}
      </div>

      <form className="assistant-composer" onSubmit={submitMessage}>
        {targetContext ? (
          <div className="assistant-context" title={`${targetContext.address}:${targetContext.port || ""}`}>
            当前设备：{targetContext.remark || targetContext.address}
          </div>
        ) : null}
        {error ? <p className="assistant-error">{error}</p> : null}
        {approval && approval.conversationId === activeConversation?.id ? (
          <div className="assistant-approval-card" role="alert">
            <div>
              <ShieldCheck size={17} />
              <span><strong>{status.executionEnabled ? "等待执行确认" : "执行功能已关闭"}</strong><small>{status.executionEnabled ? "确认后工具才会实际运行" : "待审批操作不会执行，请重新提问"}</small></span>
            </div>
            {approval.tools.map((tool) => (
              <section key={tool.tool_call_id || tool.tool_name}>
                <b>{tool.tool_name || "业务工具"}</b>
                <code>{JSON.stringify(tool.tool_args || {}, null, 2)}</code>
              </section>
            ))}
            <footer>
              <button type="button" onClick={() => void resolveApproval(false)}>{status.executionEnabled ? "拒绝执行" : "关闭提示"}</button>
              <button type="button" className="confirm" disabled={!status.executionEnabled} onClick={() => void resolveApproval(true)}>
                确认执行
              </button>
            </footer>
          </div>
        ) : null}

        <div className="assistant-toolbar-wrap">
          {openMenu === "mode" ? (
            <div
              className="assistant-mode-menu"
              role="menu"
              aria-label="选择助手模式"
              data-assistant-menu-surface
            >
              {assistantModes.map((item) => (
                <button
                  type="button"
                  role="menuitemradio"
                  aria-checked={mode === item.id}
                  className={mode === item.id ? "selected" : ""}
                  key={item.id}
                  onClick={() => {
                    setMode(item.id);
                    setOpenMenu("");
                  }}
                >
                  <span><strong>{item.label}</strong><small>{item.description}</small></span>
                  {mode === item.id ? <Check size={16} /> : null}
                </button>
              ))}
            </div>
          ) : null}
          {openMenu === "plugins" ? (
            <div className="assistant-capability-menu assistant-capability-manager" role="dialog" aria-label="插件设置" data-assistant-menu-surface>
              <header><strong>插件</strong><small>{status.executionEnabled ? "受控工具执行前需确认" : "当前仅回答问题，执行功能已关闭"}</small></header>
              <section>
                <label>
                  <span><strong>Agent 执行</strong><small>{status.executionEnabled ? "允许调用工具，执行前逐次确认" : "关闭时提供回答和可复制命令"}</small></span>
                  <input
                    aria-label="Agent 执行"
                    role="switch"
                    type="checkbox"
                    checked={status.executionEnabled}
                    disabled={!executionCapability || status.checking || Boolean(capabilityBusy) || runningConversationIds.size > 0}
                    onChange={() => void toggleCapability(executionCapability)}
                  />
                </label>
              </section>
              <nav className="assistant-plugin-tabs" role="tablist" aria-label="插件分类">
                <button
                  type="button"
                  role="tab"
                  aria-selected={pluginView === "tools"}
                  className={pluginView === "tools" ? "active" : ""}
                  onClick={() => setPluginView("tools")}
                >
                  工具
                  <b>{capabilities.filter((item) => item.kind === "tool").length}</b>
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={pluginView === "skills"}
                  className={pluginView === "skills" ? "active" : ""}
                  onClick={() => setPluginView("skills")}
                >
                  Skill
                  <b>{capabilities.filter((item) => item.kind === "skill").length}</b>
                </button>
              </nav>
              {pluginView === "tools" ? (
                <section role="tabpanel">
                  <b>默认工具</b>
                  {!status.executionEnabled ? <small>开启 Agent 执行后，已启用的工具与 Skill 才会参与对话</small> : null}
                  {capabilities.filter((item) => item.kind === "tool").map((item) => (
                    <label key={item.id}>
                      <span><strong>{item.name}</strong><small>{item.source}</small></span>
                      <input
                        type="checkbox"
                        checked={item.enabled}
                        disabled={Boolean(capabilityBusy)}
                        onChange={() => void toggleCapability(item)}
                      />
                    </label>
                  ))}
                </section>
              ) : (
                <section role="tabpanel">
                  <b>Skill</b>
                  {capabilities.filter((item) => item.kind === "skill").map((item) => (
                    <div className="assistant-capability-row" key={item.id}>
                      <span><strong>{item.name}</strong><small>{item.source}</small></span>
                      <span className="assistant-capability-actions">
                        <input
                          aria-label={`${item.name}启用状态`}
                          type="checkbox"
                          checked={item.enabled}
                          disabled={Boolean(capabilityBusy)}
                          onChange={() => void toggleCapability(item)}
                        />
                        {["user-upload", "agent-created"].includes(item.source) && !item.config?.bundled ? (
                          <button type="button" aria-label={`删除 Skill ${item.name}`} disabled={Boolean(capabilityBusy)} onClick={() => void handleDeleteCapability(item)}>
                            <Trash2 size={14} />
                          </button>
                        ) : null}
                      </span>
                    </div>
                  ))}
                  <button type="button" className="assistant-import-button" onClick={() => skillInputRef.current?.click()} disabled={Boolean(capabilityBusy)}>
                    {capabilityBusy === "skill-import" ? "正在导入…" : "导入 Skill 包"}
                  </button>
                </section>
              )}
            </div>
          ) : null}
          {openMenu === "mcp" ? (
            <div className="assistant-capability-menu assistant-capability-manager mcp" role="dialog" aria-label="MCP 设置" data-assistant-menu-surface>
              <header>
                <strong>{mcpView === "list" ? "MCP 连接" : "添加 MCP 连接"}</strong>
                <small>{status.executionEnabled ? "工具执行需用户确认" : "连接配置已保留，开启 Agent 执行后生效"}</small>
              </header>
              {mcpView === "list" ? (
                <>
                  <section>
                    {capabilities.filter((item) => item.kind === "mcp").length ? capabilities.filter((item) => item.kind === "mcp").map((item) => (
                      <article className={`assistant-mcp-connection${selectedMcpId === item.id ? " expanded" : ""}`} key={item.id}>
                        <div className="assistant-capability-row">
                          <button type="button" className="assistant-mcp-summary" onClick={() => void inspectMcp(item)} aria-expanded={selectedMcpId === item.id}>
                            <span>
                              <strong>{item.name}</strong>
                              <small>
                                {item.config?.url} · {item.config?.transport === "sse" ? "SSE" : "Streamable HTTP"}
                                {item.runtime_status === "unavailable" ? " · 当前不可用" : ""}
                              </small>
                            </span>
                            <ChevronDown size={14} />
                          </button>
                          <span className="assistant-capability-actions">
                            <input
                              aria-label={`${item.name}启用状态`}
                              type="checkbox"
                              checked={item.enabled}
                              disabled={Boolean(capabilityBusy)}
                              onChange={() => void toggleCapability(item)}
                            />
                            {item.source === "user" ? (
                              <button type="button" aria-label={`删除 MCP ${item.name}`} disabled={Boolean(capabilityBusy)} onClick={() => void handleDeleteCapability(item)}>
                                <Trash2 size={14} />
                              </button>
                            ) : null}
                          </span>
                        </div>
                        {selectedMcpId === item.id ? (
                          <div className="assistant-mcp-tools">
                            <b>
                              {capabilityBusy === `probe:${item.id}` ? "正在测试连接并读取工具…" : "服务工具"}
                            </b>
                            {capabilityBusy !== `probe:${item.id}` ? (
                              (item.discovered_tools || item.config?.discovered_tools || []).length
                                ? <ul>{(item.discovered_tools || item.config?.discovered_tools || []).map((tool) => <li key={tool}>{tool}</li>)}</ul>
                                : <span>服务未返回工具，或连接测试未通过</span>
                            ) : null}
                          </div>
                        ) : null}
                      </article>
                    )) : <span>尚未添加 MCP 连接</span>}
                  </section>
                  {mcpError ? <p className="assistant-mcp-error">{mcpError}</p> : null}
                  <button type="button" className="assistant-import-button" onClick={() => { setMcpError(""); setMcpView("add"); }}>
                    <PlugZap size={14} />
                    添加 MCP 连接
                  </button>
                </>
              ) : (
                <div className="assistant-mcp-form">
                  <button type="button" className="assistant-mcp-back" onClick={() => { setMcpError(""); setMcpView("list"); }}>
                    <ChevronRight size={13} /> 返回连接列表
                  </button>
                  <label>
                    <span>名称</span>
                    <input aria-label="MCP 名称" placeholder="例如：设备管理工具" value={mcpDraft.name} onChange={(event) => updateMcpDraft("name", event.target.value)} />
                  </label>
                  <label>
                    <span>服务地址</span>
                    <input aria-label="MCP 地址" placeholder="http://127.0.0.1:端口/mcp" value={mcpDraft.url} onChange={(event) => updateMcpDraft("url", event.target.value)} />
                  </label>
                  <label>
                    <span>传输协议</span>
                    <select required aria-label="MCP 传输协议" value={mcpDraft.transport} onChange={(event) => updateMcpDraft("transport", event.target.value)}>
                      <option value="streamable-http">Streamable HTTP</option>
                      <option value="sse">SSE</option>
                    </select>
                  </label>
                  <button
                    type="button"
                    className="assistant-mcp-more"
                    aria-expanded={mcpMoreOpen}
                    onClick={() => setMcpMoreOpen((current) => !current)}
                  >
                    {mcpMoreOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                    更多设置
                  </button>
                  {mcpMoreOpen ? (
                    <div className="assistant-mcp-advanced">
                      <label>
                        <span>工具白名单（可选）</span>
                        <input aria-label="MCP 工具白名单" placeholder="留空则使用服务提供的全部工具" value={mcpDraft.tools} onChange={(event) => updateMcpDraft("tools", event.target.value)} />
                      </label>
                      <label>
                        <span>鉴权请求头（可选）</span>
                        <textarea aria-label="MCP 请求头" placeholder={'JSON，例如 {"Authorization":"Bearer …"}'} value={mcpDraft.headers} onChange={(event) => updateMcpDraft("headers", event.target.value)} />
                      </label>
                    </div>
                  ) : null}
                  {mcpError ? <p className="assistant-mcp-error">{mcpError}</p> : null}
                  {mcpProbe?.status === "available" ? (
                    <div className="assistant-mcp-probe-result available">
                      <CheckCircle2 size={14} />
                      <span>连接成功，发现 {mcpProbe.tools.length} 个工具</span>
                    </div>
                  ) : null}
                  <button
                    type="button"
                    className="assistant-mcp-test"
                    onClick={() => void testMcpDraft()}
                    disabled={Boolean(capabilityBusy) || !mcpDraft.name.trim() || !mcpDraft.url.trim() || !mcpDraft.transport}
                  >
                    {capabilityBusy === "mcp-probe" ? "正在测试连接…" : "测试连接"}
                  </button>
                  <button
                    type="button"
                    className="assistant-import-button"
                    onClick={() => void submitMcp()}
                    disabled={Boolean(capabilityBusy) || !mcpDraft.name.trim() || !mcpDraft.url.trim() || !mcpDraft.transport}
                  >
                    {capabilityBusy === "mcp-import" ? "正在测试并添加…" : "添加并启用"}
                  </button>
                </div>
              )}
            </div>
          ) : null}
          {openMenu === "files" ? (
            <div
              className="assistant-capability-menu assistant-file-manager"
              role="dialog"
              aria-label="当前对话文件区"
              data-assistant-menu-surface
            >
              <header>
                <span>
                  <strong>当前对话文件区</strong>
                  <small>{status.executionEnabled ? "助手按需搜索和分段读取，不会把全文直接放入上下文" : "提到附件时提供有限相关摘录，长文档请指定章节或问题"}</small>
                </span>
                <b>{conversationFiles.length}/{MAX_CONVERSATION_FILES}</b>
              </header>
              <section>
                {conversationFiles.length ? conversationFiles.map((file) => (
                  <article key={file.id}>
                    <FileText size={15} />
                    <span title={file.name}>
                      <strong>{file.name}</strong>
                      <small>{formatFileSize(file.size)} · {file.character_count} 字符</small>
                    </span>
                    <button
                      type="button"
                      onClick={() => void removeConversationFile(file.id)}
                      disabled={parsingFiles}
                      aria-label={`删除 ${file.name}`}
                      title="从当前对话文件区删除"
                    >
                      <Trash2 size={14} />
                    </button>
                  </article>
                )) : (
                  <div className="assistant-file-empty">
                    <FolderOpen size={22} />
                    <span>当前对话还没有文件</span>
                  </div>
                )}
              </section>
              <button
                type="button"
                className="assistant-import-button"
                onClick={() => fileInputRef.current?.click()}
                disabled={parsingFiles || conversationFiles.length >= MAX_CONVERSATION_FILES}
              >
                <FilePlus2 size={14} />
                {parsingFiles ? "正在处理…" : "添加文件"}
              </button>
            </div>
          ) : null}

          <div className="assistant-toolbar" aria-label="助手能力设置">
            <button
              type="button"
              data-assistant-menu-trigger
              className={openMenu === "plugins" ? "active" : ""}
              onClick={() => toggleMenu("plugins")}
              aria-expanded={openMenu === "plugins"}
            >
              <Blocks size={15} />
              <span>插件 · {status.skills + status.toolkits}</span>
            </button>
            <button
              type="button"
              data-assistant-menu-trigger
              className={openMenu === "mcp" ? "active" : ""}
              onClick={() => toggleMenu("mcp")}
              aria-expanded={openMenu === "mcp"}
            >
              <PlugZap size={15} />
              <span>MCP · {status.mcpServers}</span>
            </button>
            <button
              type="button"
              data-assistant-menu-trigger
              className={openMenu === "mode" ? "active" : ""}
              onClick={() => toggleMenu("mode")}
              aria-expanded={openMenu === "mode"}
            >
              <Gauge size={15} />
              <span>{selectedMode.shortLabel}</span>
              <ChevronDown size={12} />
            </button>
            <button
              type="button"
              data-assistant-menu-trigger
              className={openMenu === "files" ? "active" : ""}
              onClick={() => toggleMenu("files")}
              aria-expanded={openMenu === "files"}
            >
              <FolderOpen size={15} />
              <span>文件 · {conversationFiles.length}</span>
            </button>
          </div>
          <input
            ref={fileInputRef}
            className="assistant-file-input"
            type="file"
            accept=".md,.markdown,.txt,.docx,text/plain,text/markdown,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            multiple
            onChange={handleFileSelection}
            tabIndex={-1}
          />
          <input
            ref={skillInputRef}
            className="assistant-skill-file-input"
            type="file"
            accept=".zip,.skill,application/zip"
            onChange={handleSkillImport}
            tabIndex={-1}
          />
        </div>

        <div className="assistant-input-row">
          <textarea
            aria-label="向运维助手提问"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={status.ready ? "输入运维问题，按回车键发送" : "本地模型就绪后可以提问"}
            rows={3}
            disabled={!status.ready}
          />
          <button
            type={activeSending ? "button" : "submit"}
            className={activeSending ? "stop" : ""}
            aria-label={activeSending ? "停止生成" : "发送消息"}
            title={activeSending ? "停止生成" : "发送消息"}
            onClick={activeSending ? stopGeneration : undefined}
            disabled={activeSending ? false : !draft.trim() || !status.ready}
          >
            {activeSending ? <Square size={14} fill="currentColor" /> : <Send size={17} />}
          </button>
        </div>
      </form>
    </section>
  );
}
