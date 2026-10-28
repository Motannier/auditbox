import { Shield } from "lucide-react";
import { OPERATOR_DISPLAY_NAME } from "../../constants/branding.js";
import { connectionEndpointLabel } from "../../lib/connection.js";

export function DangerConfirmDialog({ pendingDanger, target, onCancel, onConfirm, canRun }) {
  const command = pendingDanger.command || pendingDanger.commands?.join(" && ") || "";
  const targetName = target?.remark || (target?.host ? connectionEndpointLabel(target) : "当前目标设备");
  const reason = pendingDanger.reason || "该操作可能造成业务中断或数据变更";
  return (
    <div className="confirm-backdrop">
      <section className="confirm-dialog" role="dialog" aria-modal="true" aria-label="高危操作确认">
        <header className="confirm-dialog-header">
          <span className="confirm-dialog-icon"><Shield size={18} /></span>
          <div>
            <strong>高风险操作二次确认</strong>
            <small>安全审查已暂停当前命令，确认前不会执行</small>
          </div>
        </header>
        <div className="confirm-dialog-content">
          <dl className="confirm-risk-details">
            <div><dt>目标设备</dt><dd>{targetName}</dd></div>
            <div><dt>执行账号</dt><dd>{OPERATOR_DISPLAY_NAME}</dd></div>
            <div><dt>操作命令</dt><dd><code>{command}</code></dd></div>
            <div><dt>风险等级</dt><dd><em>高风险</em></dd></div>
            <div><dt>风险说明</dt><dd>{reason}</dd></div>
            <div><dt>影响范围</dt><dd>数据删除或配置变更后可能无法恢复。</dd></div>
          </dl>
          <p className="confirm-authorization-note">
            再次确认仅对当前目标上的这条命令生效一次，并会写入操作日志。
          </p>
        </div>
        <div className="dialog-actions">
          <button className="ghost-button" type="button" onClick={onCancel} autoFocus>
            取消操作
          </button>
          <button className="danger-button" type="button" onClick={onConfirm} disabled={!canRun}>
            再次确认执行
          </button>
        </div>
      </section>
    </div>
  );
}
