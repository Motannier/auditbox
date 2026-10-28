import { useEffect, useRef, useState } from "react";
import { BookmarkPlus, Plus, Zap } from "lucide-react";
import { IPV4_INPUT_PATTERN, normalizeIPv4Input } from "../../lib/ipv4.js";
import { ConsolePortField } from "./ConsolePortField.jsx";
import { DEVICE_TYPES, deviceProfilesForType } from "../../lib/deviceProfiles.js";

const DEFAULT_PROTOCOL_PORTS = {
  ssh: "22",
  telnet: "23",
  console: "9600",
};

const CONSOLE_BAUD_RATES = [9600, 19200, 38400, 57600, 115200];

export function NewConnectionDialog({ onCancel, onCreate, saving, error }) {
  const [networkProtocol, setNetworkProtocol] = useState("ssh");
  const [form, setForm] = useState({
    device_type: "",
    device_profile: "",
    address: "",
    port: "22",
    protocol: "ssh",
    remark: "",
    notes: "",
    username: "",
    target_password: "",
  });
  const inputRef = useRef(null);

  useEffect(() => {
    inputRef.current?.focus();
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
    if (protocol !== "console") setNetworkProtocol(protocol);
    setForm((current) => {
      const currentDefaultPort = DEFAULT_PROTOCOL_PORTS[current.protocol];
      const shouldUseProtocolDefault = !current.port || current.port === currentDefaultPort;
      return {
        ...current,
        protocol,
        address: protocol === "console" || current.protocol === "console" ? "" : current.address,
        port: shouldUseProtocolDefault ? DEFAULT_PROTOCOL_PORTS[protocol] : current.port,
        username: protocol === "console" ? "" : current.username,
        target_password: protocol === "console" ? "" : current.target_password,
      };
    });
  }

  function updateConnectionKind(kind) {
    updateProtocol(kind === "console" ? "console" : networkProtocol);
  }

  function submit(event) {
    event.preventDefault();
    const mode = event.nativeEvent.submitter?.value === "save" ? "save" : "direct";
    onCreate(
      {
        device_type: form.device_type,
        device_profile: form.device_profile,
        address: form.address.trim(),
        port: Number(form.port),
        protocol: form.protocol,
        remark: form.remark.trim(),
        notes: form.notes.trim(),
        username: form.username.trim(),
        target_password: form.target_password,
      },
      mode,
    );
  }

  return (
    <div className="terminal-modal-backdrop">
      <form
        className="target-connect-dialog new-connection-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="新建安全连接"
        onSubmit={submit}
      >
        <div className="dialog-heading">
          <div className="dialog-icon">
            <Plus size={20} />
          </div>
          <div>
            <strong>新建安全连接</strong>
            <p>接入目标设备，系统将对运维操作进行智能审查。</p>
          </div>
        </div>

        <div className="new-connection-layout">
          <section className="new-connection-section" aria-labelledby="new-connection-device-heading">
            <div className="new-connection-section-heading">
              <span aria-hidden="true">01</span>
              <div>
                <strong id="new-connection-device-heading">设备信息</strong>
                <small>用于匹配命令能力与审查规则</small>
              </div>
            </div>

            <div className="new-connection-field-grid">
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

              <label className="target-password-field new-connection-field-full">
                <span>设备名称</span>
                <input
                  value={form.remark}
                  onChange={(event) => updateField("remark", event.target.value)}
                  placeholder="例如：核心业务服务器"
                />
              </label>

              <label className="target-password-field new-connection-field-full">
                <span>备注信息 <small>选填</small></span>
                <input
                  value={form.notes}
                  onChange={(event) => updateField("notes", event.target.value)}
                  placeholder="补充业务用途、位置或维护说明"
                />
              </label>
            </div>
          </section>

          <section className="new-connection-section" aria-labelledby="new-connection-access-heading">
            <div className="new-connection-section-heading">
              <span aria-hidden="true">02</span>
              <div>
                <strong id="new-connection-access-heading">连接参数</strong>
                <small>配置接入方式与本次登录凭据</small>
              </div>
            </div>

            <div className={`connection-form-grid ${form.protocol === "console" ? "console" : ""}`}>
              <div className="target-password-field connection-protocol-field">
                <span>连接方式</span>
                <div
                  className={`connection-kind-switch ${form.protocol === "console" ? "kind-console" : "kind-network"}`}
                  role="radiogroup"
                  aria-label="连接方式"
                >
                  {[
                    ["network", "RJ45 网口设备接入"],
                    ["console", "USB Console 接入"],
                  ].map(([value, label]) => (
                    <button
                      type="button"
                      role="radio"
                      aria-checked={(form.protocol === "console" ? "console" : "network") === value}
                      className={(form.protocol === "console" ? "console" : "network") === value ? "active" : ""}
                      onClick={() => updateConnectionKind(value)}
                      disabled={saving}
                      key={value}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
              {form.protocol === "console" ? (
                <>
                  <ConsolePortField value={form.address} onChange={(value) => updateField("address", value)} />
                  <label className="target-password-field">
                    <span>波特率</span>
                    <input
                      value={form.port}
                      onChange={(event) => updateField("port", event.target.value.replace(/\D/g, ""))}
                      inputMode="numeric"
                      list="new-connection-baud-rates"
                      placeholder="请填写波特率"
                      required
                    />
                    <datalist id="new-connection-baud-rates">
                      {CONSOLE_BAUD_RATES.map((baudRate) => (
                        <option value={baudRate} key={baudRate} />
                      ))}
                    </datalist>
                  </label>
                </>
              ) : (
                <>
                  <div className="target-password-field network-protocol-field">
                    <span>连接协议</span>
                    <div className="network-protocol-switch" role="radiogroup" aria-label="连接协议">
                      {["ssh", "telnet"].map((protocol) => (
                        <button
                          type="button"
                          role="radio"
                          aria-checked={form.protocol === protocol}
                          className={form.protocol === protocol ? "active" : ""}
                          onClick={() => updateProtocol(protocol)}
                          disabled={saving}
                          key={protocol}
                        >
                          {protocol === "ssh" ? "SSH" : "Telnet"}
                        </button>
                      ))}
                    </div>
                  </div>
                  <label className="target-password-field">
                    <span>地址</span>
                    <input
                      ref={inputRef}
                      value={form.address}
                      onChange={(event) => {
                        updateField("address", normalizeIPv4Input(event.target.value, form.address));
                      }}
                      inputMode="decimal"
                      pattern={IPV4_INPUT_PATTERN}
                      title="请输入有效的完整 IPv4 地址"
                      placeholder="请填写 IP 地址"
                      required
                    />
                  </label>
                  <label className="target-password-field">
                    <span>端口</span>
                    <input
                      value={form.port}
                      onChange={(event) => updateField("port", event.target.value.replace(/\D/g, ""))}
                      inputMode="numeric"
                      placeholder="请填写端口"
                      required
                    />
                  </label>
                </>
              )}
            </div>

            {form.protocol !== "console" ? (
              <>
                <div className="new-connection-auth-grid">
                  <label className="target-password-field">
                    <span>用户名</span>
                    <input
                      value={form.username}
                      onChange={(event) => updateField("username", event.target.value)}
                      placeholder="请填写用户名"
                      required
                    />
                  </label>

                  <label className="target-password-field">
                    <span>登录密码</span>
                    <input
                      type="password"
                      value={form.target_password}
                      onChange={(event) => updateField("target_password", event.target.value)}
                      placeholder="请输入登录密码"
                      required
                    />
                  </label>
                </div>

                <p className="target-password-hint">
                  {form.protocol === "telnet" ? "Telnet 将识别登录提示并自动提交，" : ""}
                  密码只用于本次连接，不会保存。
                </p>
              </>
            ) : null}
          </section>
        </div>
        {error ? <p className="target-password-error">{error}</p> : null}

        <div className="dialog-actions">
          <button className="ghost-button" type="button" onClick={onCancel} disabled={saving}>
            取消
          </button>
          <button className="ghost-button" type="submit" value="direct" disabled={saving}>
            <Zap size={16} />
            {saving ? "连接中" : "连接"}
          </button>
          <button className="connect-button" type="submit" value="save" disabled={saving}>
            <BookmarkPlus size={16} />
            {saving ? "处理中" : "保存并连接"}
          </button>
        </div>
      </form>
    </div>
  );
}
