import { useEffect, useRef, useState } from "react";
import { Pencil } from "lucide-react";
import { IPV4_INPUT_PATTERN, normalizeIPv4Input } from "../../lib/ipv4.js";
import { ConsolePortField } from "./ConsolePortField.jsx";
import {
  DEVICE_TYPES,
  defaultDeviceProfile,
  deviceProfilesForType,
} from "../../lib/deviceProfiles.js";

const DEFAULT_PROTOCOL_PORTS = { ssh: "22", telnet: "23", console: "9600" };
const CONSOLE_BAUD_RATES = [9600, 19200, 38400, 57600, 115200];

export function DeviceEditDialog({ target, onCancel, onSave, saving, error }) {
  const [form, setForm] = useState(() => ({
    device_type: target.deviceType || "",
    device_profile: target.deviceProfile || defaultDeviceProfile(target.deviceType),
    address: target.host,
    port: String(target.port),
    protocol: String(target.protocol).toLowerCase(),
    remark: target.remark || "",
    notes: target.notes || "",
    username: target.username || "",
  }));
  const inputRef = useRef(null);

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  function updateField(key, value) {
    setForm((current) => ({ ...current, [key]: value }));
  }

  function updateDeviceType(deviceType) {
    setForm((current) => ({
      ...current,
      device_type: deviceType,
      device_profile: "",
    }));
  }

  function updateProtocol(protocol) {
    setForm((current) => ({
      ...current,
      protocol,
      address: protocol === "console" || current.protocol === "console" ? "" : current.address,
      port: DEFAULT_PROTOCOL_PORTS[protocol],
      username: protocol === "console" ? "" : current.username,
    }));
  }

  function submit(event) {
    event.preventDefault();
    onSave({
      device_type: form.device_type,
      device_profile: form.device_profile,
      address: form.address.trim(),
      port: Number(form.port),
      protocol: form.protocol,
      remark: form.remark.trim(),
      notes: form.notes.trim(),
      username: form.username.trim(),
    });
  }

  return (
    <div className="terminal-modal-backdrop page-modal-backdrop">
      <form
        className="target-connect-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="编辑设备"
        onSubmit={submit}
      >
        <div className="dialog-heading">
          <div className="dialog-icon">
            <Pencil size={19} />
          </div>
          <div>
            <strong>编辑设备</strong>
            <p>修改只影响之后新建的连接</p>
          </div>
        </div>

        <label className="target-password-field">
          <span>设备大类</span>
          <select
            value={form.device_type}
            onChange={(event) => updateDeviceType(event.target.value)}
            required
            disabled={saving}
          >
            <option value="" disabled>请选择设备大类</option>
            {DEVICE_TYPES.map((item) => (
              <option value={item.value} key={item.value}>{item.label}</option>
            ))}
          </select>
        </label>

        <label className="target-password-field">
          <span>设备子类型</span>
          <select
            value={form.device_profile}
            onChange={(event) => updateField("device_profile", event.target.value)}
            required
            disabled={saving || !form.device_type}
          >
            <option value="" disabled>请选择设备子类型</option>
            {deviceProfilesForType(form.device_type).map((profile) => (
              <option value={profile.value} key={profile.value}>{profile.label}</option>
            ))}
          </select>
        </label>

        <div className={`connection-form-grid ${form.protocol === "console" ? "console" : ""}`}>
          <label className="target-password-field">
            <span>连接方式</span>
            <select value={form.protocol} onChange={(event) => updateProtocol(event.target.value)}>
              <option value="ssh">SSH</option>
              <option value="telnet">Telnet</option>
              <option value="console">Console</option>
            </select>
          </label>
          {form.protocol === "console" ? (
            <>
              <ConsolePortField
                value={form.address}
                onChange={(value) => updateField("address", value)}
                disabled={saving}
              />
              <label className="target-password-field">
                <span>波特率</span>
                <input
                  value={form.port}
                  onChange={(event) => updateField("port", event.target.value.replace(/\D/g, ""))}
                  inputMode="numeric"
                  list="device-edit-baud-rates"
                  placeholder="请填写波特率"
                  required
                />
                <datalist id="device-edit-baud-rates">
                  {CONSOLE_BAUD_RATES.map((baudRate) => (
                    <option value={baudRate} key={baudRate} />
                  ))}
                </datalist>
              </label>
            </>
          ) : (
            <>
              <label className="target-password-field">
                <span>IP 地址</span>
                <input
                  ref={inputRef}
                  value={form.address}
                  onChange={(event) =>
                    updateField("address", normalizeIPv4Input(event.target.value, form.address))
                  }
                  inputMode="decimal"
                  pattern={IPV4_INPUT_PATTERN}
                  title="请输入有效的完整 IPv4 地址"
                  required
                />
              </label>
              <label className="target-password-field">
                <span>端口</span>
                <input
                  value={form.port}
                  onChange={(event) => updateField("port", event.target.value.replace(/\D/g, ""))}
                  inputMode="numeric"
                  required
                />
              </label>
            </>
          )}
        </div>

        <label className="target-password-field">
          <span>设备名称</span>
          <input
            value={form.remark}
            onChange={(event) => updateField("remark", event.target.value)}
            placeholder="例如：核心业务服务器"
          />
        </label>

        <label className="target-password-field">
          <span>备注信息 <small>选填</small></span>
          <input
            value={form.notes}
            onChange={(event) => updateField("notes", event.target.value)}
            placeholder="补充业务用途、位置或维护说明"
          />
        </label>

        {form.protocol !== "console" ? (
          <>
            <label className="target-password-field">
              <span>用户名</span>
              <input
                value={form.username}
                onChange={(event) => updateField("username", event.target.value)}
                required
              />
            </label>
            <p className="target-password-hint">密码不会保存；下次连接时仍需输入。</p>
          </>
        ) : null}
        {error ? <p className="target-password-error">{error}</p> : null}

        <div className="dialog-actions">
          <button className="ghost-button" type="button" onClick={onCancel} disabled={saving}>
            取消
          </button>
          <button className="connect-button" type="submit" disabled={saving}>
            {saving ? "保存中" : "保存"}
          </button>
        </div>
      </form>
    </div>
  );
}
