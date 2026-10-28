import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BookmarkPanel } from "../../components/bookmark/BookmarkPanel.jsx";
import { DeviceEditDialog } from "../../components/bookmark/DeviceEditDialog.jsx";
import { NewConnectionDialog } from "../../components/bookmark/NewConnectionDialog.jsx";
import { OperationsPanel } from "../../components/operations/OperationsPanel.jsx";
import { TerminalStage } from "../../components/terminal/TerminalStage.jsx";
import { useDevices } from "../../hooks/useDevices.js";
import { targetToConnectionPayload } from "../../lib/connection.js";
import { deviceProfilesForType, isDeviceClassificationValid } from "../../lib/deviceProfiles.js";
import { IPV4_PATTERN } from "../../lib/ipv4.js";
import { createSavedConnection, createSession, fetchRecentConnections } from "../../services/deviceService.js";

const emptyTarget = {
  id: "empty",
  protocol: "SSH",
  deviceType: "server",
  deviceProfile: "",
  host: "",
  port: 22,
  remark: "",
  status: "idle",
};

function createClientSessionId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `session-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function formPayloadToTarget(payload) {
  return {
    id: "",
    protocol: payload.protocol.toUpperCase(),
    deviceType: payload.device_type,
    deviceProfile: payload.device_profile,
    host: payload.address,
    port: payload.port,
    remark: payload.remark,
    notes: payload.notes,
    username: payload.username,
  };
}

function validateConnectionPayload(payload, { includePassword = true } = {}) {
  if (!deviceProfilesForType(payload.device_type).length) {
    return "请选择设备大类";
  }
  if (!isDeviceClassificationValid(payload.device_type, payload.device_profile)) {
    return "请选择与设备大类匹配的设备子类型";
  }
  if (payload.protocol === "console") {
    if (!payload.address.trim()) return "请选择盒子识别到的 Console 设备";
    if (!Number.isInteger(payload.port) || payload.port < 1 || payload.port > 4_000_000) {
      return "请选择有效的 Console 波特率";
    }
    return "";
  }
  if (!IPV4_PATTERN.test(payload.address)) {
    return "请输入有效的完整 IPv4 地址，例如 192.168.10.1";
  }
  if (!Number.isInteger(payload.port) || payload.port < 1 || payload.port > 65535) {
    return "端口必须是 1 到 65535 之间的整数";
  }
  if (["ssh", "telnet"].includes(payload.protocol) && !payload.username.trim()) {
    return `${payload.protocol === "ssh" ? "安全接入" : "Telnet"}必须填写用户名`;
  }
  if (includePassword && ["ssh", "telnet"].includes(payload.protocol) && !payload.target_password.trim()) {
    return `${payload.protocol === "ssh" ? "安全接入" : "Telnet"}必须填写登录密码`;
  }
  return "";
}

export function ConsolePage() {
  const [targetSearch, setTargetSearch] = useState("");
  const { targets, total, loading, error, reloadTargets, editDevice } = useDevices(targetSearch);
  const [sessions, setSessions] = useState([]);
  const [activeSessionId, setActiveSessionId] = useState(null);
  const [showNewConnection, setShowNewConnection] = useState(false);
  const [newConnectionError, setNewConnectionError] = useState("");
  const [savingConnection, setSavingConnection] = useState(false);
  const [connectError, setConnectError] = useState("");
  const [connectingTarget, setConnectingTarget] = useState(false);
  const [pendingTarget, setPendingTarget] = useState(null);
  const [credentialSessionId, setCredentialSessionId] = useState(null);
  const [credentialError, setCredentialError] = useState("");
  const [credentialConnecting, setCredentialConnecting] = useState(false);
  const [editingTarget, setEditingTarget] = useState(null);
  const [editError, setEditError] = useState("");
  const [savingEdit, setSavingEdit] = useState(false);
  const [recentHistory, setRecentHistory] = useState([]);
  const terminalRef = useRef(null);

  const reloadRecentConnections = useCallback(async () => {
    try {
      const items = await fetchRecentConnections(4);
      setRecentHistory(items);
      return items;
    } catch {
      setRecentHistory([]);
      return [];
    }
  }, []);

  useEffect(() => {
    void reloadRecentConnections();
  }, [reloadRecentConnections]);

  const activeSession = useMemo(
    () => sessions.find((session) => session.id === activeSessionId) || null,
    [sessions, activeSessionId],
  );
  const credentialSession = useMemo(
    () => sessions.find((session) => session.id === credentialSessionId) || null,
    [sessions, credentialSessionId],
  );
  const canRunCommands = activeSession?.status === "connected";
  const commandTarget = activeSession || pendingTarget || emptyTarget;
  const assistantTarget = activeSession?.status === "connected" ? activeSession : emptyTarget;
  const recentConnections = useMemo(() => {
    const savedById = new Map(targets.map((target) => [target.id, target]));
    return recentHistory.map((item) => {
      const saved = item.deviceId ? savedById.get(item.deviceId) : null;
      if (!saved) return item;
      return {
        ...item,
        ...saved,
        deviceId: saved.id,
        recentId: item.recentId,
        lastOpenedAt: item.lastOpenedAt,
      };
    });
  }, [recentHistory, targets]);

  function updateSession(sessionId, changes) {
    setSessions((current) =>
      current.map((session) => (session.id === sessionId ? { ...session, ...changes } : session)),
    );
  }

  function mountOpenedSession(password, opened) {
    const sessionId = createClientSessionId();
    const session = {
      // The server resolves saved devices even when a quick-open record has
      // no ID. Use that identity for both the terminal and assistant history.
      ...opened,
      id: sessionId,
      deviceId: opened.deviceId || null,
      password,
      websocketUrl: opened.websocketUrl,
      serverSessionId: null,
      status: "connecting",
      error: "",
      errorCode: "",
    };
    void reloadRecentConnections();
    setSessions((current) => [...current, session]);
    setActiveSessionId(sessionId);
    return sessionId;
  }

  function mountFailedSession(target, password, deviceId, error) {
    const sessionId = createClientSessionId();
    const session = {
      ...target,
      id: sessionId,
      deviceId,
      password,
      websocketUrl: "",
      serverSessionId: null,
      status: "disconnected",
      error: error.message || "连接失败",
      errorCode: error.status === 401 ? "auth_failed" : "connection_failed",
    };
    setSessions((current) => [...current, session]);
    setActiveSessionId(sessionId);
  }

  async function requestSession(sessionId, session) {
    updateSession(sessionId, { status: "connecting", error: "", errorCode: "", websocketUrl: "" });
    try {
      const payload = targetToConnectionPayload(session, session.password);
      if (session.deviceId) payload.device_id = session.deviceId;
      const opened = await createSession(payload);
      updateSession(sessionId, {
        ...opened,
        id: sessionId,
        deviceId: opened.deviceId || null,
        serverSessionId: null,
        websocketUrl: opened.websocketUrl,
        status: "connecting",
        error: "",
        errorCode: "",
        password: session.password,
      });
      void reloadRecentConnections();
      return { ok: true, error: null, errorCode: "" };
    } catch (err) {
      const errorCode = err.status === 401 ? "auth_failed" : "connection_failed";
      updateSession(sessionId, {
        status: "disconnected",
        error: err.message || "连接失败",
        errorCode,
        websocketUrl: "",
      });
      return { ok: false, error: err, errorCode };
    }
  }

  async function openSession(target, password, deviceId = null) {
    const payload = targetToConnectionPayload(target, password);
    if (deviceId) payload.device_id = deviceId;
    const opened = await createSession(payload);
    return mountOpenedSession(password, opened);
  }

  const startGuideQueue = (batch) => {
    if (!canRunCommands) return false;
    return terminalRef.current?.startCommandQueue(batch) || false;
  };

  const continueGuideQueue = () => terminalRef.current?.continueCommandQueue() || false;

  const cancelGuideQueue = () => terminalRef.current?.cancelCommandQueue("用户停止队列") || false;

  const auditGuideEvent = (event, details) => {
    if (!canRunCommands) return false;
    return terminalRef.current?.auditGuideEvent(event, details) || false;
  };


  const connectTarget = async (targetPassword) => {
    if (!pendingTarget) return;
    setConnectingTarget(true);
    setConnectError("");
    const target = pendingTarget;
    try {
      await openSession(target, targetPassword, target.id);
      setPendingTarget(null);
    } catch (err) {
      setConnectError(err.message || "连接失败");
    } finally {
      setConnectingTarget(false);
    }
  };

  const createNewConnection = async (payload, mode) => {
    const validationError = validateConnectionPayload(payload);
    if (validationError) {
      setNewConnectionError(validationError);
      return;
    }
    setSavingConnection(true);
    setNewConnectionError("");
    try {
      const target = formPayloadToTarget(payload);
      const connectionPayload = targetToConnectionPayload(target, payload.target_password);
      // The server must bind the saved device before issuing the WebSocket
      // ticket, so terminal tools and assistant history share the same identity.
      const opened = mode === "save"
        ? await createSavedConnection(connectionPayload)
        : await createSession(connectionPayload);
      if (mode === "save") {
        await reloadTargets();
      }
      mountOpenedSession(payload.target_password, opened);
      setShowNewConnection(false);
    } catch (err) {
      setNewConnectionError(err.message || "新建安全连接失败");
    } finally {
      setSavingConnection(false);
    }
  };

  const saveTargetEdit = async (payload) => {
    const validationError = validateConnectionPayload(
      { ...payload, target_password: "" },
      { includePassword: false },
    );
    if (validationError) {
      setEditError(validationError);
      return;
    }
    setSavingEdit(true);
    setEditError("");
    try {
      await editDevice(editingTarget.id, {
        ...payload,
        username: payload.username || null,
      });
      setEditingTarget(null);
    } catch (err) {
      setEditError(err.message || "保存失败");
    } finally {
      setSavingEdit(false);
    }
  };

  function closeSession(sessionId) {
    const index = sessions.findIndex((session) => session.id === sessionId);
    const next = sessions.filter((session) => session.id !== sessionId);
    setSessions(next);
    if (credentialSessionId === sessionId) {
      setCredentialSessionId(null);
      setCredentialError("");
    }
    if (activeSessionId === sessionId) {
      setActiveSessionId(next[Math.min(index, next.length - 1)]?.id || null);
    }
    void reloadRecentConnections();
  }

  function selectTarget(target) {
    setConnectError("");
    if (String(target.protocol).toLowerCase() === "console") {
      void openSession(target, "", target.id).catch((err) => {
        mountFailedSession(target, "", target.id, err);
      });
      return;
    }
    setPendingTarget(target);
  }

  async function reconnectSession(sessionId) {
    const session = sessions.find((item) => item.id === sessionId);
    if (!session) return;
    if (session.errorCode === "auth_failed") {
      setCredentialError(session.error || "SSH 用户名或密码错误，请重新输入");
      setCredentialSessionId(sessionId);
      return;
    }
    const result = await requestSession(sessionId, session);
    if (!result.ok && result.errorCode === "auth_failed") {
      setCredentialError(result.error?.message || "SSH 用户名或密码错误，请重新输入");
      setCredentialSessionId(sessionId);
    }
  }

  async function reconnectWithPassword(password) {
    const session = sessions.find((item) => item.id === credentialSessionId);
    if (!session) return;
    setCredentialConnecting(true);
    setCredentialError("");
    const nextSession = { ...session, password };
    const result = await requestSession(session.id, nextSession);
    if (result.ok) {
      updateSession(session.id, { password });
      setCredentialSessionId(null);
    } else {
      setCredentialError(result.error?.message || "连接失败");
    }
    setCredentialConnecting(false);
  }

  function handleSessionStatus(sessionId, status, sessionError, errorCode, serverSessionId = "") {
    updateSession(sessionId, {
      status,
      error: sessionError || "",
      errorCode: errorCode || "",
      ...(serverSessionId ? { serverSessionId } : {}),
    });
    if (errorCode === "auth_failed") {
      setCredentialError(sessionError || "SSH 用户名或密码错误，请重新输入");
      setCredentialSessionId(sessionId);
    }
  }

  return (
    <>
      <BookmarkPanel
        selectedDeviceId={activeSession?.deviceId || pendingTarget?.id || null}
        targets={targets}
        search={targetSearch}
        loading={loading}
        error={error}
        total={total}
        onSearchChange={setTargetSearch}
        onSelect={selectTarget}
        onEdit={(target) => {
          setEditError("");
          setEditingTarget(target);
        }}
        onAdd={() => {
          setNewConnectionError("");
          setShowNewConnection(true);
        }}
      />
      <TerminalStage
        ref={terminalRef}
        sessions={sessions}
        activeSessionId={activeSessionId}
        onActivate={setActiveSessionId}
        onClose={closeSession}
        onReconnect={(sessionId) => void reconnectSession(sessionId)}
        onAdd={() => {
          setNewConnectionError("");
          setShowNewConnection(true);
        }}
        devices={targets}
        recentConnections={recentConnections}
        onSelectDevice={selectTarget}
        onSessionStatus={handleSessionStatus}
        onGuideQueueChange={(sessionId, guideQueue) => updateSession(sessionId, { guideQueue })}
        onReviewChange={(sessionId, operationReview) => updateSession(sessionId, { operationReview })}
        pendingTarget={pendingTarget}
        onCancelPending={() => {
          setConnectError("");
          setPendingTarget(null);
        }}
        onConnectTarget={connectTarget}
        connecting={connectingTarget}
        connectError={connectError}
        credentialTarget={credentialSession}
        onCancelCredentials={() => {
          setCredentialSessionId(null);
          setCredentialError("");
        }}
        onConnectCredentials={reconnectWithPassword}
        credentialConnecting={credentialConnecting}
        credentialError={credentialError}
      />
      <OperationsPanel
        onRunCommands={startGuideQueue}
        onContinueCommands={continueGuideQueue}
        onCancelCommands={cancelGuideQueue}
        onGuideEvent={auditGuideEvent}
        guideQueue={activeSession?.guideQueue || null}
        target={commandTarget}
        assistantTarget={assistantTarget}
        canRun={canRunCommands}
        review={activeSession?.operationReview || null}
        reviewTarget={activeSession || emptyTarget}
      />
      {showNewConnection ? (
        <NewConnectionDialog
          onCancel={() => setShowNewConnection(false)}
          onCreate={createNewConnection}
          saving={savingConnection}
          error={newConnectionError}
        />
      ) : null}
      {editingTarget ? (
        <DeviceEditDialog
          target={editingTarget}
          onCancel={() => setEditingTarget(null)}
          onSave={saveTargetEdit}
          saving={savingEdit}
          error={editError}
        />
      ) : null}
    </>
  );
}
