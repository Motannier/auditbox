import { Activity, ShieldCheck } from "lucide-react";
import { connectionEndpointLabel } from "../../lib/connection.js";
import { advisoryReview, currentCommandReview } from "../../lib/operationReview.js";

const REVIEW_PROCESS_LABELS = [
  "识别命令内容",
  "匹配安全规则",
  "分析可能影响",
  "生成处置结论",
];

function reviewProcessFromStatus(review) {
  let states;
  if (review.status === "ready") {
    states = ["complete", "complete", "complete", "complete"];
  } else if (review.status === "queued") {
    states = ["complete", "complete", "active", "pending"];
  } else if (review.status === "error") {
    states = ["complete", "complete", "error", "pending"];
  } else {
    states = ["pending", "pending", "pending", "pending"];
  }
  return REVIEW_PROCESS_LABELS.map((label, index) => ({
    id: `step-${index + 1}`,
    label,
    state: states[index],
  }));
}

const RISK_LABELS = {
  unknown: "待判定",
  low: "低风险",
  medium: "中风险",
  high: "高风险",
  critical: "严重风险",
};

const STATUS_LABELS = {
  idle: "等待命令输入",
  queued: "智能审查处理中",
  ready: "实时安全审查运行中",
  error: "智能审查暂时中断",
  unsupported: "该子类型专用 AI 审查待接入",
};

const RULE_STATUS_LABELS = {
  idle: "规则旁路审计已启用",
  queued: "规则旁路审计处理中",
  ready: "规则旁路审计运行中",
  error: "规则旁路审计暂时中断",
  unsupported: "规则旁路审计尚未就绪",
};

function reviewSummaryText(review, ruleBased) {
  if (ruleBased) {
    if (review.status === "queued") return "正在按本品牌规则审计近期命令";
    if (review.status === "ready") return review.intent || "本次操作已完成规则审计";
    if (["error", "unsupported"].includes(review.status)) return "规则审计未完成，不能视为低风险";
  }
  if (review.status === "queued") return "智能审查正在实时分析当前操作";
  if (review.status === "ready") return review.intent || "本次操作已完成智能审查";
  if (review.status === "error") return "智能审查暂时未完成";
  if (review.status === "unsupported") return "已启用基础规则，尚无专用 AI 审查";
  return "等待命令输入";
}

function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

function historyStatusLabel(item, isEvidence) {
  if (item.status === "failed") return "设备报错 · 未确认生效";
  if (item.status === "cancelled") return "已取消 · 未下发";
  if (item.status === "blocked") return "已阻断 · 未下发";
  if (item.authorized) return "已授权执行";
  if (isEvidence) return "关联证据";
  return "已下发";
}

export function OperationReviewPanel({ review, target }) {
  const current = advisoryReview(review || {
    status: "idle",
    risk: "unknown",
    reason: "等待命令输入",
    history: [],
    evidence: [],
  });
  const history = Array.isArray(current.history) ? current.history : [];
  const ruleBased = current.review_source === "rules";
  const findings = new Map((Array.isArray(current.findings) ? current.findings : []).map((item) => [item.seq, item]));
  const sourceLabel = ruleBased ? "规则引擎" : current.device_profile === "h3c_comware" ? "H3C 专用模型" : "本地模型";
  const evidence = new Set(current.evidence || []);
  const commandReview = currentCommandReview(current);
  const contextReview = current.status === "ready" ? current : current.context_review;
  const persistedEvidence = Array.isArray(current.evidence_commands)
    ? current.evidence_commands
    : [];
  const targetName = target?.host
    ? target.remark || connectionEndpointLabel(target)
    : "当前没有活动终端";
  const process = Array.isArray(current.process) && current.process.length
    ? current.process
    : reviewProcessFromStatus(current);

  return (
    <section className="operation-review-view">
      <div className="operation-review-status">
        <span className="operation-review-icon">
          <ShieldCheck size={18} />
        </span>
        <span>
          <strong>{(ruleBased && RULE_STATUS_LABELS[current.status]) || STATUS_LABELS[current.status] || STATUS_LABELS.idle}</strong>
          <small>{targetName} · {sourceLabel}</small>
        </span>
        <em className={`risk-badge ${commandReview.risk || "unknown"}`}>
          {current.status === "idle" ? "等待输入" : commandReview.risk === "unknown" && ruleBased ? "待核验" : RISK_LABELS[commandReview.risk] || RISK_LABELS.unknown}
        </em>
      </div>

      <div className="operation-review-layout">
        <section className="operation-history-section" aria-label="当前终端历史">
          <div className="operation-history-heading">
            <strong>当前终端历史</strong>
            <small>
              {history.length} 条
              {current.reviewed_at ? ` · ${formatTime(current.reviewed_at)} 已审查` : ""}
            </small>
          </div>

          <div className="operation-history-list">
            {history.length ? history.map((item) => {
              const finding = findings.get(item.seq);
              const itemRisk = finding?.risk || "unknown";
              const itemRiskLabel = itemRisk === "unknown" ? "待核验" : RISK_LABELS[itemRisk];
              return (
              <article
                className={`operation-history-item ${
                  item.authorized
                    ? "authorized"
                    : evidence.has(item.seq)
                      ? `evidence ${finding ? itemRisk : ""}`
                      : ""
                }`}
                key={item.seq}
              >
                <span>
                  <time>{formatTime(item.ts)}</time>
                  <small>{finding ? `${historyStatusLabel(item, false)} · ${itemRiskLabel}` : historyStatusLabel(item, evidence.has(item.seq))}</small>
                </span>
                <code>{item.command}</code>
                {finding ? (
                  <small className="operation-history-reason">{finding.reason} {finding.status === item.status ? finding.execution_note : ""}</small>
                ) : null}
              </article>
              );
            }) : (
              <div className="operation-history-empty">
                <ShieldCheck size={28} />
                <p>执行命令后，{ruleBased ? "规则审计" : "智能审查"}结果会显示在这里</p>
              </div>
            )}
          </div>
        </section>

        <section className="operation-review-details" aria-label="审查判断">
          {current.status !== "idle" ? (
            <div className={`operation-review-summary ${current.status || "idle"}`}>
              <Activity size={15} />
              <span>
                <strong>{current.status === "ready" ? "当前命令" : reviewSummaryText(current, ruleBased)}</strong>
                <small>{current.status === "ready" ? commandReview.reason : current.reason}</small>
              </span>
            </div>
          ) : null}

          {contextReview ? (
            <div className="operation-evidence-card operation-context-review">
              <strong>历史上下文 · {RISK_LABELS[contextReview.risk] || "待判定"}</strong>
              <span>{contextReview.intent}</span>
              <small>{contextReview.reason}</small>
            </div>
          ) : null}

          {current.status === "queued" ? (
            <ol className="review-process-steps" aria-label="智能审查过程" aria-live="polite">
              {process.map((step) => (
                <li className={step.state} key={step.id || step.label}>
                  <i />
                  <span>{step.state === "active" ? `正在${step.label}……` : step.label}</span>
                  <em>{({ pending: "等待", active: "进行中", complete: "已完成", error: "未完成" })[step.state] || "等待"}</em>
                </li>
              ))}
            </ol>
          ) : null}

          {persistedEvidence.length ? (
            <div className="operation-evidence-card">
              <strong>风险关联历史命令</strong>
              {persistedEvidence.map((command, index) => (
                <code key={`${command}-${index}`}>{command}</code>
              ))}
            </div>
          ) : null}
        </section>
      </div>
    </section>
  );
}
