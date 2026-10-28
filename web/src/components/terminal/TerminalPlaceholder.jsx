import { CalendarClock, ChevronRight, Plus, Server, ShieldCheck } from "lucide-react";
import {
  connectionEndpointLabel,
  deviceProfileDisplayName,
  deviceTypeDisplayName,
} from "../../lib/connection.js";

function connectionIdentity(device) {
  return [String(device.protocol || "").toLowerCase(), device.host, device.port].join(":");
}

function recentConnectedDevices(devices) {
  const seen = new Set();
  return [...devices]
    .filter((device) => Boolean(device.lastOpenedAt || device.lastConnectedAt))
    .sort((left, right) =>
      Date.parse(right.lastOpenedAt || right.lastConnectedAt) -
      Date.parse(left.lastOpenedAt || left.lastConnectedAt),
    )
    .filter((device) => {
      const identity = connectionIdentity(device);
      if (seen.has(identity)) return false;
      seen.add(identity);
      return true;
    })
    .slice(0, 4);
}

function formatRecentTime(value) {
  if (!value) return "暂无记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "暂无记录";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date).replace("/", "-");
}

export function TerminalPlaceholder({ devices = [], onSelectDevice, onNewConnection }) {
  const quickDevices = recentConnectedDevices(devices);

  return (
    <div className="terminal-frame terminal-empty-frame">
      <div className="terminal-empty">
        <div className="terminal-empty-hero">
          <span className="terminal-empty-icon"><Server size={28} /></span>
          <h2>建立安全运维连接</h2>
          <p>接入目标设备后，系统将实时分析运维操作并识别风险</p>
          <button
            className="terminal-empty-primary quick-connect-trigger"
            type="button"
            aria-label="新建安全连接"
            onClick={(event) => onNewConnection(event.currentTarget)}
          >
            <Plus size={17} />
            新建安全连接
          </button>
        </div>

        <section className="terminal-empty-recents" aria-label="快速打开">
            <div className="terminal-empty-section-title">
              <strong>快速打开</strong>
              <small>安全接入</small>
            </div>
            <div className="terminal-empty-device-grid">
              {quickDevices.map((device) => {
                const name = String(device.remark || "").trim() || connectionEndpointLabel(device);
                return (
                  <article
                    className="terminal-empty-device terminal-empty-device-record"
                    key={device.recentId || device.id || connectionIdentity(device)}
                  >
                    <span className={`status-dot ${device.status}`} />
                    <span>
                      <strong>{name}</strong>
                      <small>{device.host} · {deviceTypeDisplayName(device.deviceType)} · {deviceProfileDisplayName(device.deviceProfile)}</small>
                    </span>
                    <em><ShieldCheck size={13} />安全接入</em>
                    <button
                      type="button"
                      aria-label={`快速连接 ${name}`}
                      onClick={() => onSelectDevice(device)}
                    >
                      连接 <ChevronRight size={14} />
                    </button>
                    <dl>
                      <div><dt>最近操作时间</dt><dd>{formatRecentTime(device.lastOperationAt || device.lastOpenedAt)}</dd></div>
                      <div>
                        <dt>操作结果</dt>
                        <dd className={device.operationRisk ? "risk" : "safe"}>
                          {device.operationResult || "暂无操作记录"}
                        </dd>
                      </div>
                    </dl>
                  </article>
                );
              })}
            </div>
            {quickDevices.length === 0 ? (
              <div className="terminal-empty-recent-empty">
                <CalendarClock size={19} />
                <span><strong>暂无最近连接</strong><small>成功接入设备后，连接和操作结果将显示在这里</small></span>
              </div>
            ) : null}
          </section>
      </div>
    </div>
  );
}
