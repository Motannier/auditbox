import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { fetchConsolePorts } from "../../services/deviceService.js";

export function ConsolePortField({ value, onChange, disabled = false }) {
  const [ports, setPorts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const requestRef = useRef(null);
  const valueRef = useRef(value);
  const onChangeRef = useRef(onChange);

  useEffect(() => {
    valueRef.current = value;
    onChangeRef.current = onChange;
  }, [onChange, value]);

  const loadPorts = useCallback(async ({ silent = false } = {}) => {
    if (silent && requestRef.current) return;
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    const timeout = window.setTimeout(() => controller.abort(), 10_000);
    if (!silent) setLoading(true);
    try {
      const nextPorts = await fetchConsolePorts(controller.signal);
      if (requestRef.current !== controller) return;
      if (!Array.isArray(nextPorts)) throw new Error("Console 检测接口返回异常");
      setPorts(nextPorts);
      setError("");
      if (!valueRef.current && nextPorts.length === 1) onChangeRef.current(nextPorts[0].path);
    } catch (err) {
      if (requestRef.current !== controller) return;
      setError(err.name === "AbortError" ? "检测超时，请重新检测" : err.message || "请重新检测");
    } finally {
      window.clearTimeout(timeout);
      if (requestRef.current === controller) {
        requestRef.current = null;
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    void loadPorts();
    const timer = window.setInterval(() => void loadPorts({ silent: true }), 2_000);
    return () => {
      window.clearInterval(timer);
      const controller = requestRef.current;
      requestRef.current = null;
      controller?.abort();
    };
  }, [loadPorts]);

  const currentMissing = value && !ports.some((port) => port.path === value);

  return (
    <label className="target-password-field console-port-field">
      <span>USB-A Console 设备</span>
      <div className="console-port-select-row">
        <select value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled} required>
          <option value="">
            {loading ? "正在检测 Console 设备" : error ? "Console 设备检测失败" : ports.length ? "请选择 Console 设备" : "未检测到 Console 设备"}
          </option>
          {currentMissing ? <option value={value}>已保存设备（{loading || error ? "待检测" : "当前未连接"}）</option> : null}
          {ports.map((port) => (
            <option value={port.path} key={port.path}>{port.label}</option>
          ))}
        </select>
        <button
          className="console-port-refresh"
          type="button"
          title="重新检测 Console 设备"
          aria-label="重新检测 Console 设备"
          onClick={() => void loadPorts()}
          disabled={disabled || loading}
        >
          <RefreshCw size={15} className={loading ? "spinning" : ""} />
        </button>
      </div>
      {error ? <small className="console-port-error" role="alert">
        Console 设备检测失败{ports.length ? "，已保留上次检测结果" : ""}：{error}
      </small> : null}
    </label>
  );
}
