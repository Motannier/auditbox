import React, { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { Plus, RefreshCw, Search, X } from "lucide-react";
import { TargetPasswordDialog } from "../bookmark/TargetPasswordDialog.jsx";
import {
  connectionEndpointLabel,
  deviceProfileDisplayName,
  deviceTypeDisplayName,
  protocolDisplayName,
} from "../../lib/connection.js";
import { TerminalPanel } from "./TerminalPanel.jsx";
import { TerminalPlaceholder } from "./TerminalPlaceholder.jsx";

const SESSION_STATUS_LABELS = {
  connecting: "连接中",
  connected: "已连接",
  disconnected: "已断开",
};

export const TerminalStage = forwardRef(function TerminalStage(
  {
    sessions,
    activeSessionId,
    onActivate,
    onClose,
    onReconnect,
    onAdd,
    devices = [],
    recentConnections = [],
    onSelectDevice,
    onSessionStatus,
    pendingTarget,
    onCancelPending,
    onConnectTarget,
    connecting,
    connectError,
    credentialTarget,
    onCancelCredentials,
    onConnectCredentials,
    credentialConnecting,
    credentialError,
    onGuideQueueChange,
    onReviewChange,
  },
  ref,
) {
  const terminalAreaRef = useRef(null);
  const terminalRefs = useRef(new Map());
  const quickMenuRef = useRef(null);
  const [quickMenuOpen, setQuickMenuOpen] = useState(false);
  const [quickSearch, setQuickSearch] = useState("");
  const [quickMenuPosition, setQuickMenuPosition] = useState(null);

  useEffect(() => {
    if (!quickMenuOpen) return;

    function closeOnOutsideClick(event) {
      if (quickMenuRef.current?.contains(event.target) || event.target.closest?.(".quick-connect-trigger")) return;
      setQuickMenuOpen(false);
    }

    function closeOnEscape(event) {
      if (event.key === "Escape") setQuickMenuOpen(false);
    }

    document.addEventListener("mousedown", closeOnOutsideClick);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", closeOnOutsideClick);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [quickMenuOpen]);

  useImperativeHandle(ref, () => ({
    executeCommand(command) {
      return terminalRefs.current.get(activeSessionId)?.executeCommand(command) || false;
    },
    startCommandQueue(batch) {
      return terminalRefs.current.get(activeSessionId)?.startCommandQueue(batch) || false;
    },
    continueCommandQueue() {
      return terminalRefs.current.get(activeSessionId)?.continueCommandQueue() || false;
    },
    cancelCommandQueue(reason) {
      return terminalRefs.current.get(activeSessionId)?.cancelCommandQueue(reason) || false;
    },
    auditGuideEvent(event, details) {
      return terminalRefs.current.get(activeSessionId)?.auditGuideEvent(event, details) || false;
    },
    focus() {
      terminalRefs.current.get(activeSessionId)?.focus();
    },
  }));

  const duplicateCounts = new Map();
  const normalizedSearch = quickSearch.trim().toLowerCase();
  const filteredDevices = devices.filter((device) => {
    if (!normalizedSearch) return true;
    return [
      device.remark,
      device.host,
      String(device.port),
      protocolDisplayName(device.protocol),
      deviceTypeDisplayName(device.deviceType),
      deviceProfileDisplayName(device.deviceProfile),
    ]
      .join(" ")
      .toLowerCase()
      .includes(normalizedSearch);
  });

  function closeSession(sessionId) {
    terminalRefs.current.get(sessionId)?.disconnect();
    onClose(sessionId);
  }

  function openQuickMenu(trigger) {
    const areaRect = terminalAreaRef.current?.getBoundingClientRect();
    const triggerRect = trigger?.getBoundingClientRect();
    if (areaRect && triggerRect) {
      const menuWidth = Math.min(330, areaRect.width - 20);
      const left = Math.min(
        Math.max(10, triggerRect.left - areaRect.left + triggerRect.width / 2 - menuWidth / 2),
        Math.max(10, areaRect.width - menuWidth - 10),
      );
      const top = triggerRect.bottom - areaRect.top + 8;
      setQuickMenuPosition({
        left,
        top,
        maxHeight: Math.max(160, areaRect.height - top - 10),
      });
    }
    setQuickSearch("");
    setQuickMenuOpen(true);
  }

  function toggleQuickMenu(trigger) {
    if (quickMenuOpen) {
      setQuickMenuOpen(false);
      return;
    }
    openQuickMenu(trigger);
  }

  return (
    <section ref={terminalAreaRef} className={`terminal-area ${sessions.length === 0 ? "empty" : ""}`}>
      {sessions.length > 0 ? (
        <div className="session-tabs" role="tablist" aria-label="连接标签">
          {sessions.map((session) => {
            const endpointKey = session.deviceId || `${session.protocol}:${session.host}:${session.port}`;
            const occurrence = (duplicateCounts.get(endpointKey) || 0) + 1;
            duplicateCounts.set(endpointKey, occurrence);
            const baseName = session.remark || connectionEndpointLabel(session);
            const tabName = occurrence > 1 ? `${baseName} · ${occurrence}` : baseName;
            const statusLabel = SESSION_STATUS_LABELS[session.status] || SESSION_STATUS_LABELS.connecting;
            return (
              <div
                className={`session-tab ${session.id === activeSessionId ? "active" : ""}`}
                role="presentation"
                key={session.id}
              >
                <button
                  className="session-tab-main"
                  type="button"
                  role="tab"
                  aria-selected={session.id === activeSessionId}
                  title={`${tabName}（${statusLabel}）`}
                  onClick={() => onActivate(session.id)}
                >
                  <span className={`session-status-dot ${session.status}`} />
                  <span className="session-tab-name">{tabName}</span>
                  <small className={`session-tab-status ${session.status}`}>{statusLabel}</small>
                </button>
                <button
                  className="session-tab-refresh"
                  type="button"
                  title={session.status === "disconnected" ? `重新连接 ${tabName}` : `刷新连接 ${tabName}`}
                  aria-label={session.status === "disconnected" ? `重新连接 ${tabName}` : `刷新连接 ${tabName}`}
                  disabled={session.status === "connecting"}
                  onClick={() => onReconnect(session.id)}
                >
                  <RefreshCw size={13} />
                </button>
                <button
                  className="session-tab-close"
                  type="button"
                  title={`关闭 ${tabName}`}
                  aria-label={`关闭 ${tabName}`}
                  onClick={() => closeSession(session.id)}
                >
                  <X size={14} />
                </button>
              </div>
            );
          })}
        <button
          className="session-tab-add quick-connect-trigger"
          type="button"
          title="快速新建安全连接"
          aria-label="快速新建安全连接"
          aria-expanded={quickMenuOpen}
          aria-haspopup="menu"
          onClick={(event) => toggleQuickMenu(event.currentTarget)}
        >
          <Plus size={17} />
        </button>
        </div>
      ) : null}

      {quickMenuOpen ? (
        <section
          className="quick-connect-menu"
          ref={quickMenuRef}
          role="menu"
          aria-label="快速打开连接"
          style={quickMenuPosition ? {
            left: `${quickMenuPosition.left}px`,
            right: "auto",
            top: `${quickMenuPosition.top}px`,
            maxHeight: `${quickMenuPosition.maxHeight}px`,
          } : undefined}
        >
          <div className="quick-connect-heading">
            <strong>从设备列表选择</strong>
            <small>{devices.length} 台</small>
          </div>
          <label className="quick-connect-search">
            <Search size={14} />
            <input
              autoFocus
              value={quickSearch}
              onChange={(event) => setQuickSearch(event.target.value)}
              placeholder="搜索设备名称、IP 或控制台"
              aria-label="搜索已管理设备"
            />
          </label>
          <div className="quick-connect-devices">
            {filteredDevices.map((device) => {
              const name = String(device.remark || "").trim() || connectionEndpointLabel(device);
              return (
                <button
                  className="quick-connect-device"
                  type="button"
                  role="menuitem"
                  key={device.id}
                  onClick={() => {
                    setQuickMenuOpen(false);
                    onSelectDevice(device);
                  }}
                >
                  <span className={`status-dot ${device.status}`} />
                  <span>
                    <strong>{name}</strong>
                    <small>
                      {deviceTypeDisplayName(device.deviceType)} · {deviceProfileDisplayName(device.deviceProfile)} · {protocolDisplayName(device.protocol)}
                    </small>
                  </span>
                </button>
              );
            })}
            {filteredDevices.length === 0 ? <p className="quick-connect-empty">没有匹配的设备</p> : null}
          </div>
          <div className="quick-connect-footer">
            <button
              className="quick-connect-new"
              type="button"
              role="menuitem"
              onClick={() => {
                setQuickMenuOpen(false);
                onAdd();
              }}
            >
              <Plus size={16} />
              <span>
                <strong>新建安全连接</strong>
                <small>接入设备并启用智能审查</small>
              </span>
            </button>
          </div>
        </section>
      ) : null}

      <div className="terminal-session-stack">
        {sessions.length === 0 ? (
          <TerminalPlaceholder
            devices={recentConnections}
            onSelectDevice={onSelectDevice}
            onNewConnection={openQuickMenu}
          />
        ) : null}
        {sessions.map((session) => (
          <div
            className={`terminal-session ${session.id === activeSessionId ? "active" : ""}`}
            key={session.id}
            role="tabpanel"
          >
            <TerminalPanel
              ref={(instance) => {
                if (instance) terminalRefs.current.set(session.id, instance);
                else terminalRefs.current.delete(session.id);
              }}
              target={session}
              active={session.id === activeSessionId}
              onStatusChange={(status, error, errorCode, serverSessionId) =>
                onSessionStatus(session.id, status, error, errorCode, serverSessionId)
              }
              onGuideQueueChange={(queue) => onGuideQueueChange(session.id, queue)}
              onReviewChange={(review) => onReviewChange(session.id, review)}
              onReconnect={() => onReconnect(session.id)}
            />
          </div>
        ))}
      </div>

      {pendingTarget ? (
        <TargetPasswordDialog
          target={pendingTarget}
          onCancel={onCancelPending}
          onConnect={onConnectTarget}
          connecting={connecting}
          connectError={connectError}
        />
      ) : credentialTarget ? (
        <TargetPasswordDialog
          target={credentialTarget}
          title="SSH 认证失败"
          submitLabel="重新连接"
          onCancel={onCancelCredentials}
          onConnect={onConnectCredentials}
          connecting={credentialConnecting}
          connectError={credentialError}
        />
      ) : null}
    </section>
  );
});
