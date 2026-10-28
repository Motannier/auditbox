import { Check, Circle, CircleDot, Clock3, OctagonX, SkipForward, Square } from "lucide-react";
import { GuideField } from "./GuideField.jsx";

const STATUS_PRESENTATION = {
  pending: { label: "待执行", Icon: Circle },
  filled: { label: "已填入，等待回车", Icon: CircleDot },
  submitted: { label: "已提交，等待终端就绪", Icon: Clock3 },
  executed: { label: "已执行", Icon: Check },
  skipped: { label: "已跳过", Icon: SkipForward },
  cancelled: { label: "已取消", Icon: OctagonX },
};

function progressForCommand(guideQueue, command, index) {
  const queued = guideQueue?.commands?.[index];
  if (!queued || queued.planned !== command) return { planned: command, status: "pending", actual: "" };
  return queued;
}

export function GuideForm({
  currentScenario,
  form,
  guideResult,
  guideQueue,
  dispatchError,
  onSubmit,
  onUpdateField,
  onContinue,
  onCancel,
  canRun,
  runDisabledReason,
}) {
  const queueInProgress = guideQueue && ["active", "waiting", "paused"].includes(guideQueue.status);
  return (
    <>
      <form className="guide-form" onSubmit={onSubmit}>
        {currentScenario.runtime && currentScenario.danger ? (
          <div className={`guide-package-context ${currentScenario.danger ? "danger" : ""}`}>
            <strong>{currentScenario.riskLabel}</strong>
            {currentScenario.risk_reason ? <span>{currentScenario.risk_reason}</span> : null}
          </div>
        ) : null}
        <div className="guide-fields">
          {currentScenario.fields.map((field) => (
            <GuideField
              field={field}
              key={field.key}
              value={form[field.key] ?? field.defaultValue ?? ""}
              onChange={(value) => onUpdateField(field.key, value)}
              disabled={queueInProgress}
            />
          ))}
        </div>

        {guideResult && !currentScenario.documentation_only && !guideResult.commands.length && guideResult.title ? (
          <p className="guide-validation-error">{guideResult.title}</p>
        ) : null}

        {guideResult?.commands.length > 0 ? (
          <div className="guide-command-list" aria-label="待下发命令">
            {guideResult.commands.map((command, index) => {
              const progress = progressForCommand(guideQueue, command, index);
              const presentation = STATUS_PRESENTATION[progress.status] || STATUS_PRESENTATION.pending;
              const StatusIcon = presentation.Icon;
              return (
                <div className={`guide-command-item ${progress.status}`} key={`${index}-${command}`}>
                  <span className="guide-command-state" title={presentation.label}>
                    <StatusIcon size={14} />
                    {presentation.label}
                  </span>
                  <code>{command}</code>
                  {progress.actual && progress.actual !== command ? (
                    <small>实际提交：<code>{progress.actual}</code></small>
                  ) : null}
                </div>
              );
            })}
          </div>
        ) : null}

        {!currentScenario.documentation_only ? <div className="guide-action-row">
          <button
            className={`guide-run ${currentScenario.danger ? "danger" : ""}`}
            type="submit"
            disabled={!canRun || !guideResult?.commands.length || queueInProgress}
            title={canRun ? "填入当前终端，等待您按回车执行" : runDisabledReason || "请先连接设备"}
          >
            <CircleDot size={16} />
            下发
          </button>
        </div> : null}
        {runDisabledReason ? <p className="guide-dispatch-notice" role="status">{runDisabledReason}</p> : null}
        {queueInProgress ? (
          <div className="guide-queue-actions">
            {guideQueue.status !== "active" ? (
              <button type="button" className="guide-queue-continue" onClick={onContinue}>
                <SkipForward size={14} />
                确认空闲并继续
              </button>
            ) : null}
            <button type="button" className="guide-queue-stop" onClick={onCancel}>
              <Square size={13} />
              停止队列
            </button>
          </div>
        ) : null}
        {dispatchError ? <p className="guide-dispatch-error">{dispatchError}</p> : null}
      </form>
    </>
  );
}
