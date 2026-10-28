import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { fetchConsolePorts } from "../../services/deviceService.js";

export function ConsolePortField({ value, onChange, disabled = false }) {
  const [ports, setPorts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const valueRef = useRef(value);
  const onChangeRef = useRef(onChange);

  useEffect(() => {
    valueRef.current = value;
    onChangeRef.current = onChange;
  }, [onChange, value]);

  const loadPorts = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setLoading(true);
    setError("");
    try {
      const nextPorts = await fetchConsolePorts();
      setPorts(nextPorts);
      if (!valueRef.current && nextPorts.length === 1) onChangeRef.current(nextPorts[0].path);
    } catch (err) {
      setPorts([]);
      setError(err.message || "Console 设备检测失败");
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadPorts();
    const timer = window.setInterval(() => void loadPorts({ silent: true }), 2_000);
    return () => window.clearInterval(timer);
  }, [loadPorts]);

  const currentMissing = value && !ports.some((port) => port.path === value);

  return (
    <label className="target-password-field console-port-field">
      <span>USB-A Console 设备</span>
      <div className="console-port-select-row">
        <select value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled} required>
          <option value="">
            {loading ? "正在检测 Console 设备" : ports.length ? "请选择 Console 设备" : "未检测到 Console 设备"}
          </option>
          {currentMissing ? <option value={value}>已保存设备（当前未连接）</option> : null}
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
      {error ? <small className="console-port-error">{error}</small> : null}
    </label>
  );
}
