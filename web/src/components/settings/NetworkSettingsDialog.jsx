import { useEffect, useRef, useState } from "react";
import { EthernetPort, RefreshCw, X } from "lucide-react";
import { IPV4_INPUT_PATTERN, normalizeIPv4Input } from "../../lib/ipv4.js";
import { applyEthernetSettings, getEthernetSettings } from "../../services/networkService.js";

export function NetworkSettingsDialog({ onClose }) {
  const [settings, setSettings] = useState(null);
  const [form, setForm] = useState({ mode: "auto", ip_address: "", netmask: "255.255.255.0" });
  const [loading, setLoading] = useState(true);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [reload, setReload] = useState(0);
  const closeRef = useRef(null);

  useEffect(() => {
    const previousFocus = document.activeElement;
    closeRef.current?.focus();
    return () => previousFocus?.focus();
  }, []);

  useEffect(() => {
    function dismiss(event) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      if (!applying) onClose();
    }
    window.addEventListener("keydown", dismiss, true);
    return () => window.removeEventListener("keydown", dismiss, true);
  }, [applying, onClose]);

  useEffect(() => {
    let disposed = false;
    setLoading(true);
    setError("");
    getEthernetSettings().then((result) => {
      if (disposed) return;
      setSettings(result);
      if (result.supported !== false) setForm({ mode: result.mode, ip_address: result.ip_address, netmask: result.netmask });
    }).catch((err) => {
      if (!disposed) setError(err.message || "无法读取网口设置");
    }).finally(() => {
      if (!disposed) setLoading(false);
    });
    return () => { disposed = true; };
  }, [reload]);

  function updateField(key, value) {
    setForm((current) => ({ ...current, [key]: value }));
    setError("");
    setMessage("");
  }

  async function submit(event) {
    event.preventDefault();
    if (applying || loading || !settings || settings.supported === false) return;
    setApplying(true);
    setError("");
    setMessage("");
    try {
      const result = await applyEthernetSettings(form.mode === "static"
        ? { mode: "static", ip_address: form.ip_address.trim(), netmask: form.netmask.trim() }
        : { mode: "auto" });
      setSettings(result);
      setForm({ mode: result.mode, ip_address: result.ip_address, netmask: result.netmask });
      setMessage(result.mode === "static"
        ? `网口 IP 已应用；重启后恢复默认。${result.link_up ? "" : "网线尚未连接，接线后请确认两端 IP 不冲突。"}`
        : "已恢复默认网口配置，并启用自动适配目标网络。");
    } catch (err) {
      setError(err.message || "网口设置应用失败");
    } finally {
      setApplying(false);
      window.requestAnimationFrame(() => closeRef.current?.focus());
    }
  }

  function handleKeys(event) {
    if (event.key !== "Tab") return;
    const controls = [...event.currentTarget.querySelectorAll("button:not(:disabled), input:not(:disabled), select:not(:disabled)")];
    const first = controls[0];
    const last = controls.at(-1);
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  }

  return (
    <div className="terminal-modal-backdrop page-modal-backdrop">
      <form className="target-connect-dialog network-settings-dialog" role="dialog" aria-modal="true"
        aria-label="网络设置" onSubmit={submit} onKeyDown={handleKeys}>
        <div className="dialog-heading">
          <div className="dialog-icon"><EthernetPort size={21} /></div>
          <div><strong>设置 · 有线网口</strong><p>通过 RJ45 网线直连服务器或交换机</p></div>
          <button ref={closeRef} className="ghost-button network-settings-close" type="button"
            aria-label="关闭网络设置" onClick={onClose} disabled={applying}><X size={18} /></button>
        </div>

        {loading ? <p role="status">正在读取网口配置…</p> : null}
        {settings?.supported === false && !loading ? <p className="network-settings-note">{settings.reason}</p> : null}
        {settings && settings.supported !== false && !loading ? <>
          <dl className="network-settings-status">
            <div><dt>有线网口</dt><dd>{settings.interface}</dd></div>
            <div><dt>网线状态</dt><dd>{settings.link_up ? "已连接" : "未连接"}</dd></div>
            <div><dt>当前 IPv4</dt><dd>{settings.addresses.join("、") || "尚未配置"}</dd></div>
            <div><dt>重启后默认</dt><dd>{settings.default_address}</dd></div>
          </dl>
          <label className="target-password-field">
            <span>配置方式</span>
            <select value={form.mode} onChange={(event) => updateField("mode", event.target.value)} disabled={applying}>
              <option value="auto">默认配置 · 自动适配目标网络</option>
              <option value="static">手动设置 · 本次开机有效</option>
            </select>
          </label>
          {form.mode === "static" ? <div className="network-settings-fields">
            <label className="target-password-field"><span>盒子网口 IP</span>
              <input value={form.ip_address} onChange={(event) => updateField("ip_address", normalizeIPv4Input(event.target.value, form.ip_address))}
                placeholder="例如 192.168.10.2" inputMode="decimal" pattern={IPV4_INPUT_PATTERN} required disabled={applying} />
            </label>
            <label className="target-password-field"><span>子网掩码</span>
              <input value={form.netmask} onChange={(event) => updateField("netmask", normalizeIPv4Input(event.target.value, form.netmask))}
                placeholder="255.255.255.0" inputMode="decimal" pattern={IPV4_INPUT_PATTERN} required disabled={applying} />
            </label>
          </div> : null}
          <div className="network-settings-note">
            <p>直连时，两端应处于同一网段且 IP 不同。例如盒子为 192.168.10.2，目标为 192.168.10.10，掩码均为 255.255.255.0。直连无需网关。</p>
            <p>配置仅在本次开机生效；重启后恢复 {settings.default_address}。手动模式不会自动改动网口 IP。</p>
            <p>修改网口配置可能中断现有 RJ45 会话。Type-C 管理网段 {settings.management_networks.join("、")} 保持不变。</p>
          </div>
        </> : null}
        {error ? <p className="target-password-error" role="alert">{error}</p> : null}
        {message ? <p className="network-settings-success" role="status">{message}</p> : null}
        <div className="dialog-actions">
          {!settings && !loading ? <button className="ghost-button" type="button" onClick={() => setReload((n) => n + 1)}><RefreshCw size={16} />重新读取</button> : null}
          <button className="ghost-button" type="button" disabled={applying} onClick={onClose}>关闭</button>
          {settings?.supported !== false ? <button className="connect-button" type="submit" disabled={loading || applying || !settings}>
            {applying ? "正在应用…" : "应用配置"}
          </button> : null}
        </div>
      </form>
    </div>
  );
}
