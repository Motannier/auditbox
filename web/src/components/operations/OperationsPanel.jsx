import { useEffect, useRef, useState } from "react";
import { Bot, ListTree, ShieldCheck } from "lucide-react";
import { AssistantPanel } from "../assistant/AssistantPanel.jsx";
import { CommandPanel } from "../guide/CommandPanel.jsx";
import { OperationReviewPanel } from "./OperationReviewPanel.jsx";
import { currentCommandReview } from "../../lib/operationReview.js";

const PANEL_MIN_WIDTH = 300;
const PANEL_MAX_WIDTH = 640;
const PANEL_DEFAULT_WIDTH = 340;
const PANEL_WIDTH_KEY = "auditbox-operations-panel-width";

function clampPanelWidth(width) {
  return Math.min(PANEL_MAX_WIDTH, Math.max(PANEL_MIN_WIDTH, width));
}

function readStoredPanelWidth() {
  try {
    const stored = Number(window.localStorage.getItem(PANEL_WIDTH_KEY));
    return Number.isFinite(stored) && stored > 0
      ? clampPanelWidth(stored)
      : PANEL_DEFAULT_WIDTH;
  } catch {
    return PANEL_DEFAULT_WIDTH;
  }
}

export function OperationsPanel({
  onRunCommands,
  onContinueCommands,
  onCancelCommands,
  onGuideEvent,
  guideQueue,
  target,
  assistantTarget,
  canRun,
  review,
  reviewTarget,
}) {
  const [activeTab, setActiveTab] = useState("review");
  const [panelWidth, setPanelWidth] = useState(readStoredPanelWidth);
  const dragStateRef = useRef(null);
  const hasRiskAlert = ["high", "critical"].includes(currentCommandReview(review || {}).risk);

  useEffect(() => {
    document.documentElement.style.setProperty("--operations-panel-width", `${panelWidth}px`);
    try {
      window.localStorage.setItem(PANEL_WIDTH_KEY, String(panelWidth));
    } catch {
      // Resizing remains available when persistent browser storage is disabled.
    }
    return () => document.documentElement.style.removeProperty("--operations-panel-width");
  }, [panelWidth]);

  useEffect(() => () => document.body.classList.remove("resizing-operations"), []);

  function startResize(event) {
    if (event.button !== 0) return;
    // Older Chromium ignores :has(), so also guard the modal boundary in code.
    if (document.querySelector(".terminal-modal-backdrop, .assistant-capability-menu")) return;
    event.preventDefault();
    dragStateRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startWidth: panelWidth,
    };
    document.body.classList.add("resizing-operations");
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function resizePanel(event) {
    const dragState = dragStateRef.current;
    if (!dragState || dragState.pointerId !== event.pointerId) return;
    setPanelWidth(clampPanelWidth(
      dragState.startWidth + dragState.startX - event.clientX,
    ));
  }

  function stopResize(event) {
    if (dragStateRef.current?.pointerId !== event.pointerId) return;
    dragStateRef.current = null;
    document.body.classList.remove("resizing-operations");
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  function resizeWithKeyboard(event) {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    const direction = event.key === "ArrowLeft" ? 1 : -1;
    setPanelWidth((width) => clampPanelWidth(width + direction * 16));
  }

  return (
    <aside className="side-panel command-panel operations-panel">
      <div
        className="operations-panel-resizer"
        role="separator"
        aria-label="调整右侧工具栏宽度"
        aria-orientation="vertical"
        aria-valuemin={PANEL_MIN_WIDTH}
        aria-valuemax={PANEL_MAX_WIDTH}
        aria-valuenow={panelWidth}
        tabIndex={0}
        onPointerDown={startResize}
        onPointerMove={resizePanel}
        onPointerUp={stopResize}
        onPointerCancel={stopResize}
        onDoubleClick={() => setPanelWidth(PANEL_DEFAULT_WIDTH)}
        onKeyDown={resizeWithKeyboard}
      />
      <div className="operations-tabs" role="tablist" aria-label="右侧工具">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "review"}
          className={`${activeTab === "review" ? "active" : ""} ${hasRiskAlert ? "has-alert" : ""}`}
          onClick={() => setActiveTab("review")}
        >
          <ShieldCheck size={15} />
          智能审查
          {hasRiskAlert ? <i aria-label="存在高风险告警" /> : null}
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "assistant"}
          className={activeTab === "assistant" ? "active" : ""}
          onClick={() => setActiveTab("assistant")}
        >
          <Bot size={15} />
          运维助手
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === "commands"}
          className={activeTab === "commands" ? "active" : ""}
          onClick={() => setActiveTab("commands")}
        >
          <ListTree size={15} />
          指令速查
        </button>
      </div>
      <div className="operations-content">
        <div hidden={activeTab !== "review"} className="operations-tab-panel" role="tabpanel">
          <OperationReviewPanel
            review={review}
            target={reviewTarget || target}
          />
        </div>
        <div hidden={activeTab !== "assistant"} className="operations-tab-panel" role="tabpanel">
          <AssistantPanel
            target={assistantTarget || target}
          />
        </div>
        <div hidden={activeTab !== "commands"} className="operations-tab-panel" role="tabpanel">
          <CommandPanel
            onRunCommands={onRunCommands}
            onContinueCommands={onContinueCommands}
            onCancelCommands={onCancelCommands}
            onGuideEvent={onGuideEvent}
            guideQueue={guideQueue}
            target={target}
            canRun={canRun}
          />
        </div>
      </div>
    </aside>
  );
}
