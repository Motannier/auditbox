import React, { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { ClipboardPaste, Copy, RefreshCw, Square } from "lucide-react";
import { fetchCommandSuggestions } from "../../services/commandService.js";
import { terminalTheme } from "../../config/terminalTheme.js";
import { connectionEndpointLabel, protocolDisplayName } from "../../lib/connection.js";
import { advisoryReview, staleReviewUpdate } from "../../lib/operationReview.js";
import { DangerConfirmDialog } from "../guide/DangerConfirmDialog.jsx";
import { CommandSuggestions } from "./CommandSuggestions.jsx";
import { PRODUCT_NAME } from "../../constants/branding.js";
import { normalizeTerminalPaste, terminalClipboardShortcut, writeTerminalClipboard } from "../../lib/terminalClipboard.js";
import { TerminalClipboardDialog } from "./TerminalClipboardDialog.jsx";

const TERMINAL_ESCAPE_RE = /\x1b(?:\[[0-9;?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|.)/g;

function createGuideBatchId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `guide-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function cloneGuideQueue(queue) {
  return {
    ...queue,
    commands: (queue.commands || []).map((command) => ({ ...command })),
  };
}

function guideQueueSuppressesRecommendations(queue) {
  return Boolean(queue && !["completed", "cancelled"].includes(queue.status));
}

function isRecommendationSuggestion(item) {
  return ["factory", "prediction", "markov"].includes(item?.source);
}

function stripTerminalControl(text) {
  return String(text || "")
    .replace(TERMINAL_ESCAPE_RE, "")
    .replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, "");
}

function looksLikeSensitivePrompt(text) {
  const tail = String(text || "").replace(/\r/g, "").split("\n").at(-1) || "";
  return /(?:password|passwd|passphrase|口令|密码|验证码|token|secret)[^\n]{0,96}[:：]\s*$/i.test(tail);
}

function looksLikeTerminalPrompt(text) {
  const lines = String(text || "").replace(/\r/g, "").split("\n");
  const tail = (lines.at(-1) || "").trimEnd();
  if (!tail || tail.length > 120 || looksLikeSensitivePrompt(tail)) return false;
  return /(?:^|\s)[^\n]{0,110}[#$>%]\s*$/.test(tail) || /^\[[^\]\n]{1,100}\]\s*$/.test(tail);
}

export const TerminalPanel = forwardRef(function TerminalPanel(
  { target, active, onStatusChange, onGuideQueueChange, onReviewChange, onReconnect },
  ref,
) {
  const terminalFrameRef = useRef(null);
  const hostRef = useRef(null);
  const termRef = useRef(null);
  const fitAddonRef = useRef(null);
  const socketRef = useRef(null);
  const connectionFailureRef = useRef(null);
  const targetRef = useRef(target);
  const onStatusChangeRef = useRef(onStatusChange);
  const onGuideQueueChangeRef = useRef(onGuideQueueChange);
  const onReviewChangeRef = useRef(onReviewChange);
  const lastReviewRef = useRef({});
  const decoderRef = useRef(new TextDecoder());
  const currentCommandRef = useRef("");
  const currentCommandSourceRef = useRef("human");
  const recommendationEligibleRef = useRef(false);
  const commandCursorRef = useRef(0);
  const suggestionTimerRef = useRef(null);
  const suggestionSeqRef = useRef(0);
  const suggestionsRef = useRef([]);
  const recommendationSuggestionsRef = useRef([]);
  const inputTrackingValidRef = useRef(true);
  const pendingConfirmationRef = useRef(null);
  const selectedSuggestionRef = useRef(-1);
  const suggestionReserveHeightRef = useRef(0);
  const suggestionPlacementRef = useRef(null);
  const shellFocusPaddingTimerRef = useRef(null);
  const shellFocusPaddingSignatureRef = useRef("");
  const terminalOutputRevisionRef = useRef(0);
  const pendingTerminalWritesRef = useRef(0);
  const guideQueueRef = useRef(null);
  const guidePromptTimerRef = useRef(null);
  const guideOutputTailRef = useRef("");
  const guideSensitiveInputRef = useRef(false);
  const terminalSensitiveInputRef = useRef(false);
  const terminalOutputTailRef = useRef("");
  const activeRef = useRef(active);
  const clipboardRequestRef = useRef(0);
  const [suggestions, setSuggestions] = useState([]);
  const [selectedSuggestion, setSelectedSuggestion] = useState(-1);
  const [suggestionPlacement, setSuggestionPlacement] = useState(null);
  const [suggestionReserveHeight, setSuggestionReserveHeight] = useState(0);
  const [pendingConfirmation, setPendingConfirmation] = useState(null);
  const [hasSelection, setHasSelection] = useState(false);
  const [clipboardDialog, setClipboardDialog] = useState(null);
  const [clipboardMenu, setClipboardMenu] = useState(null);
  const [clipboardNotice, setClipboardNotice] = useState("");
  const [clipboardQueue, setClipboardQueue] = useState(null);

  activeRef.current = active;
  targetRef.current = target;
  onStatusChangeRef.current = onStatusChange;
  onGuideQueueChangeRef.current = onGuideQueueChange;
  onReviewChangeRef.current = onReviewChange;

  useImperativeHandle(ref, () => ({
    executeCommand(command) {
      return executeCommand(command);
    },
    startCommandQueue(batch) {
      return startCommandQueue(batch);
    },
    continueCommandQueue() {
      return continueCommandQueue();
    },
    cancelCommandQueue(reason) {
      return cancelCommandQueue(reason);
    },
    auditGuideEvent(event, details) {
      return sendGuideAuditEvent(event, details);
    },
    focus() {
      termRef.current?.focus();
    },
    disconnect() {
      cancelCommandQueue("终端标签已关闭");
      closeWebSocket(1000, "标签已关闭");
    },
  }));

  useEffect(() => {
    clearCommandSuggestions();
    resetTerminal();
    connectWebSocket();
  }, [target.websocketUrl]);

  useEffect(() => {
    if (!active || pendingConfirmation) return undefined;
    const frame = window.requestAnimationFrame(() => {
      resizeTerminal();
      termRef.current?.focus();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [active, pendingConfirmation]);

  useEffect(() => {
    if (!active || target.status !== "connected" || pendingConfirmation) invalidateClipboard();
  }, [active, target.status, pendingConfirmation]);

  useEffect(() => {
    if (!clipboardMenu) return undefined;
    const close = (event) => {
      if (!event.target.closest?.(".terminal-clipboard-menu")) setClipboardMenu(null);
    };
    const escape = (event) => {
      if (event.key === "Escape") {
        setClipboardMenu(null);
        termRef.current?.focus();
      }
    };
    window.addEventListener("pointerdown", close);
    window.addEventListener("keydown", escape);
    return () => {
      window.removeEventListener("pointerdown", close);
      window.removeEventListener("keydown", escape);
    };
  }, [clipboardMenu]);

  useEffect(() => {
    if (
      target.status !== "connected"
      || pendingConfirmation
    ) {
      clearCommandSuggestions();
    }
  }, [target.status, pendingConfirmation]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => resizeTerminal());
    return () => window.cancelAnimationFrame(frame);
  }, [suggestionReserveHeight]);

  useEffect(() => {
    if (!hostRef.current) return;

    let disposed = false;
    let terminal = null;
    let disposable = null;
    let selectionDisposable = null;
    let resizeObserver = null;
    const host = hostRef.current;
    host.addEventListener("paste", handleNativePaste, true);
    host.addEventListener("contextmenu", handleClipboardContextMenu, true);

    async function bootTerminal() {
      const [{ Terminal: XTerminal }, { FitAddon }] = await Promise.all([
        import("@xterm/xterm"),
        import("@xterm/addon-fit"),
      ]);
      if (disposed || !hostRef.current) return;

      terminal = new XTerminal({
        cursorBlink: true,
        cursorStyle: "block",
        fontFamily: '"SFMono-Regular", Consolas, "Liberation Mono", monospace',
        fontSize: 13,
        lineHeight: 1.45,
        rows: 28,
        cols: 96,
        convertEol: true,
        scrollback: 2000,
        theme: terminalTheme,
      });

      fitAddonRef.current = new FitAddon();
      terminal.loadAddon(fitAddonRef.current);
      terminal.open(hostRef.current);
      termRef.current = terminal;
      resizeTerminal();
      resetTerminal();
      connectWebSocket();
      terminal.focus();

      disposable = terminal.onData(handleTerminalData);
      selectionDisposable = terminal.onSelectionChange(() => setHasSelection(terminal.hasSelection()));
      terminal.attachCustomKeyEventHandler((event) => {
        const shortcut = terminalClipboardShortcut(event, terminal.hasSelection());
        if (shortcut === "copy") {
          event.preventDefault();
          if (!event.repeat) copyTerminalSelection();
          return false;
        }
        // Let the browser generate its native paste event (also works over HTTP),
        // but don't let xterm interpret the shortcut as terminal control input.
        if (shortcut === "paste") return false;
        return true;
      });
      resizeObserver = new ResizeObserver(resizeTerminal);
      resizeObserver.observe(hostRef.current);
      window.addEventListener("resize", resizeTerminal);
    }

    bootTerminal();

    return () => {
      disposed = true;
      disposable?.dispose();
      selectionDisposable?.dispose();
      host.removeEventListener("paste", handleNativePaste, true);
      host.removeEventListener("contextmenu", handleClipboardContextMenu, true);
      resizeObserver?.disconnect();
      window.removeEventListener("resize", resizeTerminal);
      clearShellFocusPaddingTimer();
      clearCommandSuggestions();
      closeWebSocket();
      terminal?.dispose();
      termRef.current = null;
      fitAddonRef.current = null;
    };
  }, []);

  function invalidateClipboard() {
    clipboardRequestRef.current += 1;
    setClipboardDialog(null);
    setClipboardMenu(null);
    setClipboardNotice("");
  }

  function canPaste() {
    return activeRef.current && targetRef.current?.status === "connected"
      && socketRef.current?.readyState === WebSocket.OPEN && !pendingConfirmationRef.current;
  }

  function clipboardRequestIsCurrent(request, socket) {
    return request === clipboardRequestRef.current && socket === socketRef.current && canPaste();
  }

  async function copyTerminalSelection() {
    const text = termRef.current?.getSelection();
    setClipboardMenu(null);
    if (!text) return;
    const request = ++clipboardRequestRef.current;
    const copied = await writeTerminalClipboard(text);
    if (request !== clipboardRequestRef.current || !activeRef.current || !termRef.current) return;
    if (copied) {
      setClipboardNotice("已复制");
      termRef.current.focus();
    } else {
      setClipboardDialog({ kind: "copy", text });
    }
  }

  async function requestTerminalPaste() {
    setClipboardMenu(null);
    if (!canPaste()) return;
    const socket = socketRef.current;
    const request = ++clipboardRequestRef.current;
    try {
      if (!navigator.clipboard?.readText) throw new Error("clipboard unavailable");
      const text = await navigator.clipboard.readText();
      if (clipboardRequestIsCurrent(request, socket)) receiveTerminalPaste(text, request, socket);
    } catch {
      if (clipboardRequestIsCurrent(request, socket)) {
        setClipboardDialog({ kind: "paste", text: "", sensitive: terminalSensitiveInputRef.current, request, socket });
      }
    }
  }

  function handleNativePaste(event) {
    // Capture before xterm: otherwise xterm sends newlines/bracketed-paste
    // markers straight to the relay and a pasted command can execute immediately.
    event.preventDefault();
    event.stopImmediatePropagation();
    setClipboardMenu(null);
    if (!canPaste()) return;
    const request = ++clipboardRequestRef.current;
    receiveTerminalPaste(event.clipboardData?.getData("text/plain") || "", request, socketRef.current);
  }

  function receiveTerminalPaste(raw, request, socket) {
    if (!clipboardRequestIsCurrent(request, socket)) return;
    try {
      const text = normalizeTerminalPaste(raw);
      if (!text) { setClipboardNotice("剪贴板没有文本"); return; }
      if (terminalSensitiveInputRef.current && text.includes("\n")) {
        setClipboardNotice("密码输入只能粘贴一行");
        return;
      }
      if (text.includes("\n")) {
        clearCommandSuggestions();
        setClipboardDialog({ kind: "paste", text, request, socket });
        return;
      }
      insertClipboardText(text);
    } catch (error) {
      setClipboardNotice(error.message);
    }
  }

  function insertClipboardText(text) {
    if (!canPaste()) return false;
    if (guideQueueRef.current?.status === "waiting" && !terminalSensitiveInputRef.current) {
      setClipboardNotice("当前命令尚未结束，请稍后再粘贴");
      return false;
    }
    if (guideQueueSuppressesRecommendations(guideQueueRef.current)) {
      pauseGuideQueue("已粘贴手动输入，原队列已暂停");
    }
    clearCommandSuggestions();
    currentCommandSourceRef.current = "human";
    handleTerminalData(text);
    setClipboardNotice("已粘贴，按 Enter 提交");
    termRef.current?.focus();
    return true;
  }

  function submitClipboardDialog(text) {
    const dialog = clipboardDialog;
    if (!dialog || !clipboardRequestIsCurrent(dialog.request, dialog.socket)) {
      setClipboardDialog(null);
      return;
    }
    setClipboardDialog(null);
    if (text.includes("\n")) {
      if (guideQueueRef.current?.status === "waiting" || terminalSensitiveInputRef.current) {
        setClipboardNotice("当前命令尚未结束，请稍后再粘贴多行命令");
        termRef.current?.focus();
        return;
      }
      startCommandQueue({ kind: "clipboard", title: "粘贴命令", commands: text.split("\n") });
    } else {
      insertClipboardText(text);
    }
  }

  function handleClipboardContextMenu(event) {
    if (event.shiftKey) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (!activeRef.current) return;
    const rect = terminalFrameRef.current.getBoundingClientRect();
    setClipboardMenu({
      left: Math.max(4, Math.min(event.clientX - rect.left, rect.width - 172)),
      top: Math.max(4, Math.min(event.clientY - rect.top, rect.height - 100)),
    });
  }

  function resizeTerminal() {
    const fitAddon = fitAddonRef.current;
    const terminal = termRef.current;
    const host = hostRef.current;
    if (!fitAddon || !terminal || !host || host.clientWidth === 0 || host.clientHeight === 0) return;

    const proposed = fitAddon.proposeDimensions();
    if (!proposed || Number.isNaN(proposed.cols) || Number.isNaN(proposed.rows)) return;

    const rows = Math.max(1, proposed.rows - 1);
    if (terminal.cols !== proposed.cols || terminal.rows !== rows) {
      terminal.resize(proposed.cols, rows);
    }
    scheduleShellFocusPadding(terminalOutputRevisionRef.current, 120);
    updateCommandSuggestionPlacement();
  }

  function clearShellFocusPaddingTimer() {
    if (!shellFocusPaddingTimerRef.current) return;
    window.clearTimeout(shellFocusPaddingTimerRef.current);
    shellFocusPaddingTimerRef.current = null;
  }

  function scheduleShellFocusPadding(revision, delay = 360) {
    clearShellFocusPaddingTimer();
    if (
      revision !== terminalOutputRevisionRef.current
      || !looksLikeTerminalPrompt(terminalOutputTailRef.current)
      || terminalSensitiveInputRef.current
    ) return;
    shellFocusPaddingTimerRef.current = window.setTimeout(() => {
      shellFocusPaddingTimerRef.current = null;
      applyShellFocusPadding(revision);
    }, delay);
  }

  function applyShellFocusPadding(revision) {
    const terminal = termRef.current;
    const frame = terminalFrameRef.current;
    if (
      !terminal
      || !frame
      || revision !== terminalOutputRevisionRef.current
      || pendingTerminalWritesRef.current > 0
      || pendingConfirmationRef.current
      || terminalSensitiveInputRef.current
      || !looksLikeTerminalPrompt(terminalOutputTailRef.current)
      || socketRef.current?.readyState !== WebSocket.OPEN
    ) return;

    const buffer = terminal.buffer.active;
    if (
      buffer.type !== "normal"
      || buffer.viewportY < buffer.baseY
      || terminal.rows < 12
      || frame.clientHeight < 260
    ) return;

    const paddingRows = Math.max(4, Math.floor(terminal.rows * 0.38));
    const targetRow = terminal.rows - 1 - paddingRows;
    if (buffer.cursorY <= targetRow) return;

    const signature = `${revision}:${terminal.cols}:${terminal.rows}`;
    if (shellFocusPaddingSignatureRef.current === signature) return;
    shellFocusPaddingSignatureRef.current = signature;

    const cursorColumn = Math.min(buffer.cursorX, Math.max(0, terminal.cols - 1));
    const localPadding = `${"\r\n".repeat(paddingRows)}\x1b[${paddingRows}A\x1b[${
      cursorColumn + 1
    }G`;
    terminal.write(localPadding, () => {
      if (suggestionsRef.current.length) scheduleCommandSuggestionPlacement();
    });
  }

  function scheduleCommandSuggestionPlacement() {
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(updateCommandSuggestionPlacement);
    });
  }

  function updateCommandSuggestionPlacement() {
    const frame = terminalFrameRef.current;
    const host = hostRef.current;
    if (!frame || !host || !suggestionsRef.current.length) {
      commitCommandSuggestionPlacement(null);
      return;
    }

    const cursor = host.querySelector(".xterm-cursor, .xterm-cursor-block");
    if (!cursor) {
      commitCommandSuggestionPlacement(null);
      return;
    }

    const frameRect = frame.getBoundingClientRect();
    const cursorRect = cursor.getBoundingClientRect();
    const gap = 8;
    const edgePadding = 12;
    const preferredHeight = Math.min(252, 42 + suggestionsRef.current.length * 38);
    const minimumUsefulHeight = Math.min(preferredHeight, 80);
    const spaceBelow = frameRect.bottom - cursorRect.bottom - gap - edgePadding;

    if (spaceBelow < preferredHeight && suggestionReserveHeightRef.current === 0) {
      const maxReserve = Math.max(0, Math.floor(frameRect.height - 150));
      const reserveHeight = Math.min(preferredHeight + gap + edgePadding, maxReserve);
      if (reserveHeight >= minimumUsefulHeight) {
        suggestionReserveHeightRef.current = reserveHeight;
        setSuggestionReserveHeight(reserveHeight);
        return;
      }
    }

    const panelWidth = Math.min(560, Math.max(0, frameRect.width - edgePadding * 2));
    const cursorLeft = cursorRect.right - frameRect.left + gap;
    const left = Math.max(
      edgePadding,
      Math.min(cursorLeft, frameRect.width - panelWidth - edgePadding),
    );
    const horizontalPlacement = {
      left: `${left}px`,
      width: `${panelWidth}px`,
    };
    commitCommandSuggestionPlacement({
      ...horizontalPlacement,
      top: `${Math.max(edgePadding, cursorRect.bottom - frameRect.top + gap)}px`,
      bottom: "auto",
      maxHeight: `${Math.max(minimumUsefulHeight, Math.min(preferredHeight, spaceBelow))}px`,
    });
  }

  function commitCommandSuggestionPlacement(nextPlacement) {
    const current = suggestionPlacementRef.current;
    const keys = ["left", "width", "top", "bottom", "maxHeight"];
    if (
      current === nextPlacement ||
      (current && nextPlacement && keys.every((key) => current[key] === nextPlacement[key]))
    ) {
      return;
    }
    suggestionPlacementRef.current = nextPlacement;
    setSuggestionPlacement(nextPlacement);
  }

  function resetTerminal() {
    const terminal = termRef.current;
    if (!terminal) return;
    const current = targetRef.current;
    clearShellFocusPaddingTimer();
    terminalOutputRevisionRef.current += 1;
    shellFocusPaddingSignatureRef.current = "";
    terminal.reset();
    pendingConfirmationRef.current = null;
    setPendingConfirmation(null);
    recommendationSuggestionsRef.current = [];
    clearGuidePromptTimer();
    guideOutputTailRef.current = "";
    guideSensitiveInputRef.current = false;
    terminalSensitiveInputRef.current = false;
    terminalOutputTailRef.current = "";
    if (guideQueueRef.current && !["completed", "cancelled"].includes(guideQueueRef.current.status)) {
      pauseGuideQueue("终端已重新初始化，请确认连接后继续");
    }
        terminal.writeln(`\x1b[32m${PRODUCT_NAME}会话已初始化\x1b[0m`);
    terminal.writeln(
      `\x1b[33m正在通过${protocolDisplayName(current.protocol)}连接 ${
        current.remark || connectionEndpointLabel(current)
      }\x1b[0m`,
    );
    terminal.writeln("\x1b[36m正在打开终端通道...\x1b[0m");
  }

  function executeCommand(commandText) {
    const command = commandText.trim();
    if (
      !command
      || socketRef.current?.readyState !== WebSocket.OPEN
    ) return false;
    resetCommandInput();
    suppressProgrammaticSuggestions();
    sendTerminalData(`${command}\r`, "guide");
    termRef.current?.focus();
    return true;
  }

  function startCommandQueue(batch) {
    const commands = (batch?.commands || []).map((command) => String(command).trim()).filter(Boolean);
    if (
      !commands.length
      || socketRef.current?.readyState !== WebSocket.OPEN
    ) return false;

    if (guideQueueRef.current && !["completed", "cancelled"].includes(guideQueueRef.current.status)) {
      cancelCommandQueue("已开始新的指令队列");
    }

    const batchId = createGuideBatchId();
    const queue = {
      batchId,
      kind: batch.kind === "clipboard" ? "clipboard" : "guide",
      scenarioId: String(batch.scenarioId || ""),
      title: String(batch.title || "指令速查"),
      status: "active",
      currentIndex: 0,
      message: "第 1 条命令已填入终端，请核对后按回车执行",
      commands: commands.map((command) => ({ planned: command, actual: "", status: "pending" })),
    };
    publishGuideQueue(queue);
    sendGuideAuditEvent("guide_batch_started", {
      batch_id: batchId,
      scenario_id: queue.scenarioId,
      title: queue.title,
      commands,
      parameters: batch.parameters || {},
    });
    return fillGuideCommand(queue, 0);
  }

  function fillGuideCommand(queue, index) {
    const item = queue.commands[index];
    if (!item) {
      completeGuideBatch(queue);
      return true;
    }
    if (socketRef.current?.readyState !== WebSocket.OPEN) {
      pauseGuideQueue("终端连接不可用，队列已暂停");
      return false;
    }

    clearGuidePromptTimer();
    guideOutputTailRef.current = "";
    guideSensitiveInputRef.current = false;
    suppressProgrammaticSuggestions();
    if (currentCommandRef.current || !inputTrackingValidRef.current) {
      clearQueueInput(queue);
    }
    currentCommandRef.current = item.planned;
    currentCommandSourceRef.current = queue.kind === "clipboard" ? "human" : "guide";
    commandCursorRef.current = item.planned.length;
    inputTrackingValidRef.current = true;
    sendTerminalData(item.planned, currentCommandSourceRef.current);

    const next = cloneGuideQueue(queue);
    next.status = "active";
    next.currentIndex = index;
    next.message = `第 ${index + 1} 条命令已填入终端，请核对后按回车执行`;
    next.commands[index] = { ...next.commands[index], actual: "", status: "filled" };
    publishGuideQueue(next);
    sendGuideAuditEvent("guide_command_filled", {
      batch_id: next.batchId,
      scenario_id: next.scenarioId,
      index,
      planned_command: item.planned,
    });
    window.requestAnimationFrame(() => termRef.current?.focus());
    return true;
  }

  function clearQueueInput(queue) {
    if (queue.kind !== "clipboard") {
      sendTerminalData("\x15", "guide");
      return;
    }
    // Clear the remote editor too, including text expanded by native Tab.
    const clear = targetRef.current?.deviceProfile === "h3c_comware" ? "\x18" : "\x15";
    sendTerminalData(`\x1b[F${clear}`);
  }

  function continueCommandQueue() {
    const queue = guideQueueRef.current;
    if (
      !queue
      || ["completed", "cancelled"].includes(queue.status)
    ) return false;
    if (socketRef.current?.readyState !== WebSocket.OPEN) {
      pauseGuideQueue("终端连接不可用，暂时不能继续");
      return false;
    }

    const item = queue.commands[queue.currentIndex];
    sendGuideAuditEvent("guide_queue_resumed", {
      batch_id: queue.batchId,
      index: queue.currentIndex,
      previous_status: item?.status || "pending",
    });
    if (!item || ["executed", "skipped", "cancelled"].includes(item.status)) {
      return fillGuideCommand(queue, queue.currentIndex + 1);
    }
    if (item.status === "submitted") {
      completeGuideCommand(queue, { manuallyConfirmed: true });
      return true;
    }
    return fillGuideCommand(queue, queue.currentIndex);
  }

  function cancelCommandQueue(reason = "用户停止队列") {
    const queue = guideQueueRef.current;
    if (!queue || ["completed", "cancelled"].includes(queue.status)) return false;
    clearGuidePromptTimer();
    const next = cloneGuideQueue(queue);
    const current = next.commands[next.currentIndex];
    if (current?.status === "filled") {
      if (socketRef.current?.readyState === WebSocket.OPEN) clearQueueInput(queue);
      resetCommandInput();
      current.status = "cancelled";
    }
    next.commands = next.commands.map((item, index) => (
      index > next.currentIndex && item.status === "pending" ? { ...item, status: "cancelled" } : item
    ));
    next.status = "cancelled";
    next.message = reason;
    publishGuideQueue(next);
    sendGuideAuditEvent("guide_batch_cancelled", {
      batch_id: next.batchId,
      index: next.currentIndex,
      reason,
    });
    return true;
  }

  function pauseGuideQueue(message) {
    const queue = guideQueueRef.current;
    if (!queue || ["completed", "cancelled", "paused"].includes(queue.status)) return;
    clearGuidePromptTimer();
    const next = cloneGuideQueue(queue);
    next.status = "paused";
    next.message = message;
    publishGuideQueue(next);
    sendGuideAuditEvent("guide_queue_paused", {
      batch_id: next.batchId,
      index: next.currentIndex,
      reason: message,
    });
  }

  function handleGuideSubmission(actualCommand) {
    const queue = guideQueueRef.current;
    if (!queue || queue.status !== "active") return;
    const index = queue.currentIndex;
    const current = queue.commands[index];
    if (!current || current.status !== "filled") return;

    const actual = actualCommand.trim();
    const next = cloneGuideQueue(queue);
    next.status = "waiting";
    next.commands[index] = {
      ...next.commands[index],
      actual,
      status: actual ? "submitted" : "skipped",
    };
    next.message = actual
      ? `第 ${index + 1} 条命令已提交，正在等待终端返回提示符`
      : `第 ${index + 1} 条命令已跳过，正在等待终端就绪`;
    publishGuideQueue(next);
    guideOutputTailRef.current = "";
    guideSensitiveInputRef.current = false;

    if (actual && actual !== current.planned) {
      sendGuideAuditEvent("guide_command_edited", {
        batch_id: next.batchId,
        index,
        planned_command: current.planned,
        actual_command: actual,
      });
    }
    sendGuideAuditEvent(actual ? "guide_command_submitted" : "guide_command_skipped", {
      batch_id: next.batchId,
      index,
      planned_command: current.planned,
      actual_command: actual,
    });
  }

  function trackGuideTerminalOutput(text) {
    const queue = guideQueueRef.current;
    if (!queue || queue.status !== "waiting" || !text || pendingConfirmationRef.current) return;
    const plain = stripTerminalControl(text);
    guideOutputTailRef.current = `${guideOutputTailRef.current}${plain}`.slice(-4096);
    guideSensitiveInputRef.current = looksLikeSensitivePrompt(guideOutputTailRef.current);
    clearGuidePromptTimer();
    if (!guideSensitiveInputRef.current && looksLikeTerminalPrompt(guideOutputTailRef.current)) {
      completeGuideCommand(queue);
      return;
    }
    guidePromptTimerRef.current = window.setTimeout(() => {
      guidePromptTimerRef.current = null;
      const currentQueue = guideQueueRef.current;
      if (!currentQueue || currentQueue.status !== "waiting" || guideSensitiveInputRef.current) return;
      if (looksLikeTerminalPrompt(guideOutputTailRef.current)) {
        completeGuideCommand(currentQueue);
      }
    }, 450);
  }

  function completeGuideCommand(queue, { manuallyConfirmed = false } = {}) {
    const index = queue.currentIndex;
    const current = queue.commands[index];
    if (!current) return;
    clearGuidePromptTimer();
    const next = cloneGuideQueue(queue);
    if (next.commands[index].status === "submitted") next.commands[index].status = "executed";
    sendGuideAuditEvent("guide_command_completed", {
      batch_id: next.batchId,
      index,
      planned_command: current.planned,
      actual_command: current.actual,
      prompt_detected: !manuallyConfirmed,
    });
    const nextIndex = index + 1;
    if (nextIndex >= next.commands.length) {
      completeGuideBatch(next);
      return;
    }
    fillGuideCommand(next, nextIndex);
  }

  function completeGuideBatch(queue) {
    const next = cloneGuideQueue(queue);
    next.status = "completed";
    next.message = "本组命令已全部处理完成";
    publishGuideQueue(next);
    sendGuideAuditEvent("guide_batch_completed", {
      batch_id: next.batchId,
      command_count: next.commands.length,
    });
  }

  function publishGuideQueue(queue) {
    guideQueueRef.current = queue;
    setClipboardQueue(queue.kind === "clipboard" ? cloneGuideQueue(queue) : null);
    if (queue.kind === "clipboard" && ["completed", "cancelled"].includes(queue.status)) {
      setClipboardNotice(queue.status === "completed" ? "粘贴队列已完成" : "粘贴队列已停止");
    }
    if (guideQueueSuppressesRecommendations(queue)) {
      if (suggestionsRef.current.some(isRecommendationSuggestion)) {
        clearCommandSuggestions();
      }
    } else {
      restoreCommandRecommendations();
    }
    onGuideQueueChangeRef.current?.(cloneGuideQueue(queue));
  }

  function sendGuideAuditEvent(event, details = {}) {
    const socket = socketRef.current;
    if (socket?.readyState !== WebSocket.OPEN) return false;
    socket.send(JSON.stringify({ type: "guide_audit_event", event, details }));
    return true;
  }

  function clearGuidePromptTimer() {
    if (!guidePromptTimerRef.current) return;
    window.clearTimeout(guidePromptTimerRef.current);
    guidePromptTimerRef.current = null;
  }

  function handleTerminalData(data) {
    if (pendingConfirmationRef.current) return;
    clearShellFocusPaddingTimer();
    if (terminalSensitiveInputRef.current) {
      clearCommandSuggestions();
      sendTerminalData(data);
      if (data === "\r" || data === "\n") {
        terminalSensitiveInputRef.current = false;
        terminalOutputTailRef.current = "";
        resetCommandInput();
      }
      return;
    }
    if (handleSuggestionKey(data)) return;
    const queue = guideQueueRef.current;
    const isEnter = data === "\r" || data === "\n";
    const submittedCommand = isEnter ? currentCommandRef.current : "";
    const source = currentCommandSourceRef.current;
    if (isEnter) recommendationEligibleRef.current = source === "human";
    if (data === "\x03" && queue && !["completed", "cancelled"].includes(queue.status)) {
      cancelCommandQueue("用户通过 Ctrl+C 停止了指令队列");
    } else if (
      queue?.status === "waiting" &&
      !guideSensitiveInputRef.current &&
      (isPrintableInput(data) || isHistoryArrow(data))
    ) {
      pauseGuideQueue("检测到手动终端操作，队列已暂停以避免误下发");
    }
    trackCommandInput(data);
    if (isEnter) handleGuideSubmission(submittedCommand);
    sendTerminalData(data, source);
  }

  function handleSuggestionKey(data) {
    if (data === "\x03" || data === "\x15") {
      resetCommandInput();
      inputTrackingValidRef.current = true;
      clearCommandSuggestions();
      return false;
    }
    if (!suggestionsRef.current.length) {
      return false;
    }
    if (isUpArrow(data) || isDownArrow(data)) {
      moveSuggestionSelection(isDownArrow(data) ? 1 : -1);
      return true;
    }
    if (data === "\r" || data === "\n" || data === "\t") {
      // Accepting a suggestion only fills the line. A later Enter, after
      // the popup closes, submits it through the normal risk-confirmation gate.
      applySelectedSuggestion(selectedSuggestionRef.current >= 0 ? selectedSuggestionRef.current : 0);
      return true;
    }
    if (isRightArrow(data) && commandCursorRef.current === currentCommandRef.current.length) {
      applySelectedSuggestion(selectedSuggestionRef.current >= 0 ? selectedSuggestionRef.current : 0);
      return true;
    }
    if (data === "\x1b") {
      clearCommandSuggestions();
      return true;
    }
    return false;
  }

  function trackCommandInput(data) {
    if (data === "\r" || data === "\n") {
      resetCommandInput();
      inputTrackingValidRef.current = true;
      clearCommandSuggestions();
      return;
    }
    if (isHistoryArrow(data) || data === "\t") {
      resetCommandInput();
      inputTrackingValidRef.current = false;
      clearCommandSuggestions();
      return;
    }
    if (isLeftArrow(data)) {
      commandCursorRef.current = Math.max(0, commandCursorRef.current - 1);
      scheduleCommandSuggestions();
      return;
    }
    if (isRightArrow(data)) {
      commandCursorRef.current = Math.min(currentCommandRef.current.length, commandCursorRef.current + 1);
      scheduleCommandSuggestions();
      return;
    }
    if (isHomeKey(data)) {
      commandCursorRef.current = 0;
      scheduleCommandSuggestions();
      return;
    }
    if (isEndKey(data)) {
      commandCursorRef.current = currentCommandRef.current.length;
      scheduleCommandSuggestions();
      return;
    }
    if (data === "\x1b[3~") {
      const cursor = commandCursorRef.current;
      currentCommandRef.current =
        currentCommandRef.current.slice(0, cursor) + currentCommandRef.current.slice(cursor + 1);
      scheduleCommandSuggestions();
      return;
    }
    if (data === "\x7f" || data === "\b") {
      const cursor = commandCursorRef.current;
      if (cursor > 0) {
        currentCommandRef.current =
          currentCommandRef.current.slice(0, cursor - 1) + currentCommandRef.current.slice(cursor);
        commandCursorRef.current = cursor - 1;
      }
      scheduleCommandSuggestions();
      return;
    }
    if (isPrintableInput(data)) {
      const cursor = commandCursorRef.current;
      currentCommandRef.current =
        currentCommandRef.current.slice(0, cursor) + data + currentCommandRef.current.slice(cursor);
      commandCursorRef.current = cursor + data.length;
      scheduleCommandSuggestions();
    }
  }

  function isLeftArrow(data) {
    return data === "\x1b[D" || data === "\x1bOD";
  }

  function isRightArrow(data) {
    return data === "\x1b[C" || data === "\x1bOC";
  }

  function isHistoryArrow(data) {
    return isUpArrow(data) || isDownArrow(data);
  }

  function isUpArrow(data) {
    return data === "\x1b[A" || data === "\x1bOA";
  }

  function isDownArrow(data) {
    return data === "\x1b[B" || data === "\x1bOB";
  }

  function isHomeKey(data) {
    return data === "\x01" || data === "\x1b[H" || data === "\x1bOH" || data === "\x1b[1~";
  }

  function isEndKey(data) {
    return data === "\x05" || data === "\x1b[F" || data === "\x1bOF" || data === "\x1b[4~";
  }

  function isPrintableInput(data) {
    return data.length > 0 && !/[\x00-\x1f\x7f]/.test(data);
  }

  function scheduleCommandSuggestions() {
    if (
      currentCommandSourceRef.current !== "human"
      || (guideQueueRef.current?.kind === "clipboard" && guideQueueSuppressesRecommendations(guideQueueRef.current))
      || !inputTrackingValidRef.current
      || pendingConfirmationRef.current
      || socketRef.current?.readyState !== WebSocket.OPEN
    ) {
      clearCommandSuggestions();
      return;
    }
    const prefix = currentCommandRef.current.slice(0, commandCursorRef.current).trimStart();
    if (suggestionTimerRef.current) {
      window.clearTimeout(suggestionTimerRef.current);
      suggestionTimerRef.current = null;
    }
    if (prefix.length < 1) {
      clearCommandSuggestions();
      return;
    }
    const seq = suggestionSeqRef.current + 1;
    suggestionSeqRef.current = seq;
    suggestionTimerRef.current = window.setTimeout(async () => {
      try {
        const items = await fetchCommandSuggestions({
          prefix,
          target: targetRef.current,
          limit: 8,
        });
        if (suggestionSeqRef.current !== seq) return;
        const seen = new Set();
        const filtered = items.filter((item) => {
          if (
            guideQueueSuppressesRecommendations(guideQueueRef.current)
            && isRecommendationSuggestion(item)
          ) return false;
          if (item.command === prefix || seen.has(item.command)) return false;
          seen.add(item.command);
          return true;
        });
        suggestionsRef.current = filtered;
        selectedSuggestionRef.current = -1;
        setSuggestions(filtered);
        setSelectedSuggestion(-1);
        scheduleCommandSuggestionPlacement();
      } catch {
        if (suggestionSeqRef.current !== seq) return;
        clearCommandSuggestions();
      }
    }, 140);
  }

  function restoreCommandRecommendations() {
    if (
      !recommendationEligibleRef.current
      || guideQueueSuppressesRecommendations(guideQueueRef.current)
      || !inputTrackingValidRef.current
      || currentCommandRef.current
    ) return;
    const normalized = recommendationSuggestionsRef.current;
    suggestionsRef.current = normalized;
    selectedSuggestionRef.current = -1;
    setSuggestions(normalized);
    setSelectedSuggestion(-1);
    if (normalized.length && pendingTerminalWritesRef.current === 0) {
      scheduleCommandSuggestionPlacement();
    } else {
      commitCommandSuggestionPlacement(null);
    }
  }

  function applySelectedSuggestion(index = selectedSuggestionRef.current) {
    const item = suggestionsRef.current[index];
    if (!item) return;
    const currentCommand = currentCommandRef.current;
    const cursor = commandCursorRef.current;
    const replacement = item.command;
    clearShellFocusPaddingTimer();
    clearCommandSuggestions();
    currentCommandRef.current = replacement;
    commandCursorRef.current = replacement.length;
    inputTrackingValidRef.current = true;
    if (cursor === currentCommand.length && replacement.startsWith(currentCommand)) {
      sendTerminalData(replacement.slice(currentCommand.length));
    } else {
      sendTerminalData(
        `${"\x1b[C".repeat(Math.max(0, currentCommand.length - cursor))}${"\x7f".repeat(
          currentCommand.length,
        )}${replacement}`,
      );
    }
    // The popup disappears (and may resize the terminal) after this event.
    // Restore input focus after that render; the remote echo owns cursor placement.
    window.requestAnimationFrame(() => {
      const terminal = termRef.current;
      terminal?.clearSelection();
      terminal?.scrollToBottom();
      terminal?.focus();
    });
  }

  function moveSuggestionSelection(delta) {
    const count = suggestionsRef.current.length;
    if (!count) return;
    const current = selectedSuggestionRef.current;
    const next = current < 0
      ? (delta > 0 ? 0 : count - 1)
      : (current + delta + count) % count;
    selectedSuggestionRef.current = next;
    setSelectedSuggestion(next);
    window.requestAnimationFrame(() => {
      terminalFrameRef.current
        ?.querySelector('.command-suggestions [aria-selected="true"]')
        ?.scrollIntoView({ block: "nearest" });
    });
  }

  function resetCommandInput() {
    currentCommandRef.current = "";
    currentCommandSourceRef.current = "human";
    commandCursorRef.current = 0;
  }

  function suppressProgrammaticSuggestions() {
    recommendationEligibleRef.current = false;
    recommendationSuggestionsRef.current = [];
    clearCommandSuggestions();
  }

  function clearCommandSuggestions() {
    if (suggestionTimerRef.current) {
      window.clearTimeout(suggestionTimerRef.current);
      suggestionTimerRef.current = null;
    }
    suggestionSeqRef.current += 1;
    suggestionsRef.current = [];
    selectedSuggestionRef.current = -1;
    setSuggestions([]);
    setSelectedSuggestion(-1);
    commitCommandSuggestionPlacement(null);
    if (suggestionReserveHeightRef.current !== 0) {
      suggestionReserveHeightRef.current = 0;
      setSuggestionReserveHeight(0);
    }
  }

  function sendTerminalData(data, source = "human") {
    const socket = socketRef.current;
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(source === "human" ? data : JSON.stringify({ type: "terminal_input", source, data }));
      return;
    }
    termRef.current?.writeln("\r\n\x1b[31m终端通道尚未连接\x1b[0m");
  }

  function connectWebSocket() {
    const terminal = termRef.current;
    const current = targetRef.current;
    if (!terminal || !current?.websocketUrl) return;
    closeWebSocket();
    connectionFailureRef.current = null;
    lastReviewRef.current = {};

    onStatusChangeRef.current?.("connecting", "", "");

    const socket = new WebSocket(current.websocketUrl);
    socket.binaryType = "arraybuffer";
    socketRef.current = socket;

    socket.onopen = () => {
      terminal.writeln("\x1b[32m终端通道已连接\x1b[0m");
    };
    socket.onmessage = async (event) => {
      if (typeof event.data === "string") {
        writeSocketText(event.data);
        return;
      }
      if (event.data instanceof Blob) {
        const buffer = await event.data.arrayBuffer();
        writeTerminalOutput(decoderRef.current.decode(buffer));
        return;
      }
      writeTerminalOutput(decoderRef.current.decode(event.data));
    };
    socket.onerror = () => {
      terminal.writeln("\r\n\x1b[31m终端通道发生错误\x1b[0m");
      if (socketRef.current === socket && !connectionFailureRef.current) {
        reportConnectionFailure("终端通道发生错误", "channel_error");
      }
    };
    socket.onclose = () => {
      if (socketRef.current === socket) {
        terminal.writeln("\r\n\x1b[33m终端通道已关闭\x1b[0m");
        socketRef.current = null;
        if (!connectionFailureRef.current) {
          reportConnectionFailure("终端通道已关闭", "channel_closed");
        }
      }
    };
  }

  function reportConnectionFailure(message, code = "connection_failed") {
    connectionFailureRef.current = { message, code };
    recommendationSuggestionsRef.current = [];
    clearCommandSuggestions();
    pauseGuideQueue("连接已断开，指令队列已暂停");
    onStatusChangeRef.current?.("disconnected", message, code);
  }

  function writeSocketText(text) {
    const terminal = termRef.current;
    if (!terminal) return;
    try {
      const event = JSON.parse(text);
      if (event.type === "handshake") {
        if (event.status === "ok") {
          connectionFailureRef.current = null;
          terminal.writeln(`\x1b[32m代理会话 ${event.session_id} 已就绪\x1b[0m`);
          if (String(targetRef.current?.protocol || "").toLowerCase() === "console") {
            terminal.writeln(
              "\x1b[36mConsole 串口已连接；设备暂无输出时，请按 Enter 键唤醒控制台。\x1b[0m",
            );
          }
          onStatusChangeRef.current?.("connected", "", "", String(event.session_id || ""));
        } else {
          const message = event.message || "代理握手失败";
          terminal.writeln(`\x1b[31m代理握手失败：${message}\x1b[0m`);
          reportConnectionFailure(message, event.code || "handshake_failed");
        }
        return;
      }
      if (event.type === "error") {
        const message = event.message || "终端通道错误";
        terminal.writeln(`\x1b[31m${message}\x1b[0m`);
        reportConnectionFailure(message, event.code || "channel_error");
        return;
      }
      if (event.type === "input_error") {
        terminal.writeln(`\r\n\x1b[31m${event.message || "终端输入失败"}\x1b[0m`);
        suppressProgrammaticSuggestions();
        pauseGuideQueue("输入来源通道不可用，请重新连接后继续");
        return;
      }
      if (event.type === "terminal_input_source" && event.input_source === "agent") {
        suppressProgrammaticSuggestions();
        resetCommandInput();
        return;
      }
      if (event.type === "recommendation" || event.type === "prediction") {
        if (
          !recommendationEligibleRef.current
          || (event.input_source && event.input_source !== "human")
          || pendingConfirmationRef.current
          || targetRef.current?.status !== "connected"
        ) {
          recommendationSuggestionsRef.current = [];
          clearCommandSuggestions();
          return;
        }
        const normalized = (event.suggestions || [])
          .map((item) => (typeof item === "string" ? {
            command: item,
            source: event.type === "recommendation" ? "factory" : "prediction",
            risk: "normal",
            usage_count: 0,
          } : {
            ...item,
            source: item.source || (event.type === "recommendation" ? "factory" : "prediction"),
            risk: item.risk || "normal",
            usage_count: Number(item.usage_count) || 0,
          }))
          .filter((item) => item.command);
        recommendationSuggestionsRef.current = normalized;
        if (guideQueueSuppressesRecommendations(guideQueueRef.current)) {
          if (suggestionsRef.current.some(isRecommendationSuggestion)) {
            clearCommandSuggestions();
          }
          return;
        }
        if (inputTrackingValidRef.current && !currentCommandRef.current) {
          restoreCommandRecommendations();
        }
        return;
      }
      if (event.type === "device_type") {
        return;
      }
      if (event.type === "operation_review") {
        applyOperationReview(event);
        return;
      }
      if (event.type === "alert") {
        const color = event.level === "danger" ? "31" : "33";
        const action = event.action === "confirm" ? "等待确认" : "已阻断";
        terminal.writeln(
          `\r\n\x1b[${color}m[安全审查 ${action}] ${event.command || ""} - ${event.reason || ""}\x1b[0m`,
        );
        if (event.action === "confirm" && event.confirmation_id) {
          const nextConfirmation = {
            confirmationId: event.confirmation_id,
            command: event.command || "",
            reason: event.reason || "该命令可能造成业务中断或数据变更",
          };
          clearCommandSuggestions();
          pendingConfirmationRef.current = nextConfirmation;
          setPendingConfirmation(nextConfirmation);
          const queue = guideQueueRef.current;
          if (queue?.status === "waiting") {
            const nextQueue = cloneGuideQueue(queue);
            nextQueue.message = "高危命令已暂停，等待您二次确认";
            publishGuideQueue(nextQueue);
            sendGuideAuditEvent("guide_command_confirmation_required", {
              batch_id: nextQueue.batchId,
              index: nextQueue.currentIndex,
              command: event.command || "",
              reason: event.reason || "",
            });
          }
        } else if (event.action !== "confirm") {
          cancelCommandQueue(event.reason || "命令已被安全策略阻断");
        }
        return;
      }
      if (event.type === "confirmation_error") {
        terminal.writeln(`\r\n\x1b[31m${event.message || "高危命令确认失败"}\x1b[0m`);
        return;
      }
    } catch {
      // Not a gateway control message; write it as terminal text.
    }
    writeTerminalOutput(text);
  }

  function writeTerminalOutput(text) {
    const terminal = termRef.current;
    if (!terminal || !text) return;
    clearShellFocusPaddingTimer();
    const outputRevision = terminalOutputRevisionRef.current + 1;
    terminalOutputRevisionRef.current = outputRevision;
    shellFocusPaddingSignatureRef.current = "";
    const plain = stripTerminalControl(text);
    terminalOutputTailRef.current = `${terminalOutputTailRef.current}${plain}`.slice(-512);
    if (looksLikeSensitivePrompt(terminalOutputTailRef.current)) terminalSensitiveInputRef.current = true;
    if (looksLikeTerminalPrompt(terminalOutputTailRef.current)) terminalSensitiveInputRef.current = false;
    trackGuideTerminalOutput(text);
    pendingTerminalWritesRef.current += 1;
    terminal.write(text, () => {
      pendingTerminalWritesRef.current = Math.max(0, pendingTerminalWritesRef.current - 1);
      scheduleShellFocusPadding(outputRevision);
      if (suggestionsRef.current.length) scheduleCommandSuggestionPlacement();
    });
  }

  function closeWebSocket(code = 1000, reason = "终端已关闭") {
    const socket = socketRef.current;
    socketRef.current = null;
    invalidateClipboard();
    clearShellFocusPaddingTimer();
    suppressProgrammaticSuggestions();
    resetCommandInput();
    pendingConfirmationRef.current = null;
    setPendingConfirmation(null);
    if (socket && socket.readyState !== WebSocket.CLOSED) {
      try {
        socket.close(code, reason);
      } catch {
        // A socket can transition between CONNECTING and CLOSED during rapid tab operations.
      }
    }
  }

  function applyOperationReview(review) {
    const normalized = advisoryReview(review);
    if (staleReviewUpdate(normalized, lastReviewRef.current)) return;
    lastReviewRef.current = normalized;
    onReviewChangeRef.current?.(normalized);
  }

  function resolvePendingConfirmation(confirmed) {
    const pending = pendingConfirmationRef.current;
    const socket = socketRef.current;
    if (!pending || socket?.readyState !== WebSocket.OPEN) return;
    socket.send(JSON.stringify({
      type: confirmed ? "command_confirmation" : "command_confirmation_cancel",
      confirmation_id: pending.confirmationId,
    }));
    const queue = guideQueueRef.current;
    if (queue?.status === "waiting") {
      if (confirmed) {
        const next = cloneGuideQueue(queue);
        next.message = "高危命令已确认，正在等待终端执行完成";
        publishGuideQueue(next);
        sendGuideAuditEvent("guide_command_confirmation_accepted", {
          batch_id: next.batchId,
          index: next.currentIndex,
          command: pending.command,
        });
      } else {
        sendGuideAuditEvent("guide_command_confirmation_cancelled", {
          batch_id: queue.batchId,
          index: queue.currentIndex,
          command: pending.command,
        });
        cancelCommandQueue("高危命令已取消，后续队列未继续");
      }
    }
    pendingConfirmationRef.current = null;
    setPendingConfirmation(null);
    window.requestAnimationFrame(() => termRef.current?.focus());
  }

  return (
    <>
      <div
        className={`terminal-frame ${suggestionReserveHeight ? "has-suggestion-space" : ""}`}
        ref={terminalFrameRef}
        style={suggestionReserveHeight
          ? { "--suggestion-reserve-height": `${suggestionReserveHeight}px` }
          : undefined}
      >
        <div className="terminal-clipboard-toolbar">
          <span className="terminal-clipboard-notice" role="status">
            {clipboardQueue && !["completed", "cancelled"].includes(clipboardQueue.status)
              ? `粘贴 ${clipboardQueue.currentIndex + 1}/${clipboardQueue.commands.length} · ${clipboardQueue.status === "paused" ? "已暂停" : clipboardQueue.status === "waiting" ? "等待返回" : "按 Enter 提交"}`
              : clipboardNotice || "选中文本可复制"}
          </span>
          <div className="terminal-clipboard-actions">
            {clipboardQueue?.status === "paused" && clipboardQueue.commands[clipboardQueue.currentIndex]?.status !== "submitted" ? (
              <button type="button" disabled={!canPaste()} onClick={continueCommandQueue}>继续填入</button>
            ) : null}
            {clipboardQueue && !["completed", "cancelled"].includes(clipboardQueue.status) ? (
              <button type="button" disabled={Boolean(pendingConfirmation)} onClick={() => { cancelCommandQueue("已停止粘贴队列"); termRef.current?.focus(); }}><Square size={13} />停止填入</button>
            ) : null}
            <button type="button" aria-label="复制终端选中文本" title="复制选中文本（Ctrl+Shift+C / ⌘C）"
              disabled={!hasSelection} onMouseDown={(event) => event.preventDefault()} onClick={copyTerminalSelection}><Copy size={14} />复制</button>
            <button type="button" aria-label="粘贴到终端" title="粘贴（Ctrl+Shift+V / ⌘V）"
              disabled={!canPaste()} onMouseDown={(event) => event.preventDefault()} onClick={requestTerminalPaste}><ClipboardPaste size={14} />粘贴</button>
          </div>
        </div>
        <div className="xterm-host" ref={hostRef} aria-label="终端输入区" />
        {clipboardMenu ? (
          <div className="terminal-clipboard-menu" role="menu" aria-label="终端剪贴板" style={clipboardMenu}>
            <button type="button" role="menuitem" disabled={!hasSelection} onClick={copyTerminalSelection}><Copy size={14} />复制选中文本</button>
            <button type="button" role="menuitem" disabled={!canPaste()} onClick={requestTerminalPaste}><ClipboardPaste size={14} />粘贴</button>
          </div>
        ) : null}
        {clipboardDialog ? (
          <TerminalClipboardDialog key={clipboardDialog.request || "copy"} dialog={clipboardDialog}
            onCancel={() => { setClipboardDialog(null); termRef.current?.focus(); }} onSubmit={submitClipboardDialog} />
        ) : null}
        <CommandSuggestions
          suggestions={suggestionPlacement ? suggestions : []}
          selectedIndex={selectedSuggestion}
          placement={suggestionPlacement}
          deviceProfile={target?.deviceProfile}
          onSelect={(index) => {
            selectedSuggestionRef.current = index;
            setSelectedSuggestion(index);
          }}
          onApply={(index) => {
            selectedSuggestionRef.current = index;
            applySelectedSuggestion(index);
          }}
        />
        {pendingConfirmation ? (
          <DangerConfirmDialog
            pendingDanger={pendingConfirmation}
            target={target}
            onCancel={() => resolvePendingConfirmation(false)}
            onConfirm={() => resolvePendingConfirmation(true)}
            canRun={socketRef.current?.readyState === WebSocket.OPEN}
          />
        ) : null}
        {target.status === "disconnected" ? (
          <div className="terminal-reconnect">
            <strong>{target.errorCode === "auth_failed" ? "SSH 认证失败" : "连接已断开"}</strong>
            <p>{target.error || "目标设备或网络连接已中断"}</p>
            <button type="button" className="connect-button" onClick={onReconnect}>
              <RefreshCw size={16} />
              {target.errorCode === "auth_failed" ? "重新输入密码" : "重新连接"}
            </button>
          </div>
        ) : null}
      </div>
    </>
  );
});
