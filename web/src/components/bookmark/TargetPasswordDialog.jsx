import React, { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { LockKeyhole, Zap } from "lucide-react";
import {
  connectionEndpointLabel,
  deviceProfileDisplayName,
  deviceTypeDisplayName,
  protocolDisplayName,
} from "../../lib/connection.js";

export function TargetPasswordDialog({
  target,
  onCancel,
  onConnect,
  connecting,
  connectError,
  title = "连接目标",
  submitLabel = "连接",
}) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const inputRef = useRef(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, [target]);

  useEffect(() => {
    if (!connectError) return;
    setError(connectError);
    window.requestAnimationFrame(() => {
      inputRef.current?.focus();
      inputRef.current?.select();
    });
  }, [connectError]);

  function submit(event) {
    event.preventDefault();
    if (!password.trim()) {
      setError("请输入登录密码");
      return;
    }
    setError("");
    onConnect(password);
  }

  return (
    <div className="terminal-modal-backdrop">
      <form
        className="target-connect-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onSubmit={submit}
      >
        <div className="dialog-heading">
          <div className="dialog-icon">
            <LockKeyhole size={20} />
          </div>
          <div>
            <strong>{title}</strong>
            <p>{target.remark || connectionEndpointLabel(target)}</p>
          </div>
        </div>

        <dl className="target-connect-summary">
          <div>
            <dt>地址</dt>
            <dd>{target.host}</dd>
          </div>
          <div>
            <dt>端口</dt>
            <dd>{target.port}</dd>
          </div>
          <div>
            <dt>协议</dt>
            <dd>{protocolDisplayName(target.protocol)}</dd>
          </div>
          <div>
            <dt>设备</dt>
            <dd>{deviceTypeDisplayName(target.deviceType)} · {deviceProfileDisplayName(target.deviceProfile)}</dd>
          </div>
        </dl>

        <label className="target-password-field">
          <span>登录密码</span>
          <input
            ref={inputRef}
            type="password"
            value={password}
            aria-invalid={Boolean(error)}
            aria-describedby={error ? "target-password-error" : undefined}
            onChange={(event) => {
              setPassword(event.target.value);
              setError("");
            }}
            placeholder="请输入目标机器密码"
          />
        </label>

        <p className="target-password-hint">密码只用于本次连接，不会保存。</p>
        {error ? (
          <p id="target-password-error" className="target-password-error" role="alert">
            {error}
          </p>
        ) : null}

        <div className="dialog-actions">
          <button className="ghost-button" type="button" onClick={onCancel} disabled={connecting}>
            取消
          </button>
          <button className="connect-button" type="submit" disabled={connecting}>
            <Zap size={16} />
            {connecting ? "连接中" : submitLabel}
          </button>
        </div>
      </form>
    </div>
  );
}
