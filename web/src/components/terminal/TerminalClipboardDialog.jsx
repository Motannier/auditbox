import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { MAX_TERMINAL_PASTE_LENGTH, normalizeTerminalPaste } from "../../lib/terminalClipboard.js";

export function TerminalClipboardDialog({ dialog, onCancel, onSubmit }) {
  const panelRef = useRef(null);
  const inputRef = useRef(null);
  const [value, setValue] = useState(dialog.text || "");
  const [error, setError] = useState("");
  const copyOnly = dialog.kind === "copy";
  const multiline = !dialog.sensitive && /\n/.test(value.replace(/\n+$/, ""));
  const title = copyOnly ? "复制终端内容" : multiline ? "粘贴多行命令" : "粘贴到终端";

  useEffect(() => {
    inputRef.current?.focus();
    if (copyOnly) inputRef.current?.select();
    const handleKey = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        onCancel();
      }
      if (event.key === "Tab") {
        const items = [...panelRef.current.querySelectorAll("button, textarea, input")].filter((item) => !item.disabled);
        const index = items.indexOf(document.activeElement);
        const next = event.shiftKey ? (index <= 0 ? items.length - 1 : index - 1) : (index + 1) % items.length;
        event.preventDefault();
        items[next]?.focus();
      }
    };
    const panel = panelRef.current;
    panel.addEventListener("keydown", handleKey);
    return () => panel.removeEventListener("keydown", handleKey);
  }, []);

  function submit(event) {
    event.preventDefault();
    try {
      const normalized = normalizeTerminalPaste(value);
      if (!normalized.trim()) return setError("请先粘贴内容");
      if (dialog.sensitive && normalized.includes("\n")) return setError("密码输入只能粘贴一行");
      onSubmit(normalized);
    } catch (reason) {
      setError(reason.message);
    }
  }

  return (
    <div className="terminal-clipboard-backdrop">
      <form className="terminal-clipboard-dialog" ref={panelRef} role="dialog" aria-modal="true" aria-label={title} onSubmit={submit}>
        <header><strong>{title}</strong><button type="button" aria-label="关闭剪贴板窗口" onClick={onCancel}><X size={18} /></button></header>
        <p>{copyOnly ? "浏览器未允许自动复制。内容已选中，按 Ctrl+C 或 ⌘C 复制。"
          : multiline ? "按行填入当前终端，每条都需要按 Enter 执行。将替换当前未提交的输入。"
            : "在下方按 Ctrl+V 或 ⌘V 粘贴，填入后不会自动执行。"}</p>
        {dialog.sensitive ? (
          <input ref={inputRef} aria-label="待粘贴内容" type="password" autoComplete="off" value={value} onChange={(event) => setValue(event.target.value)} />
        ) : (
          <textarea ref={inputRef} aria-label={copyOnly ? "待复制内容" : "待粘贴内容"} value={value}
            readOnly={copyOnly} spellCheck={false} maxLength={copyOnly ? undefined : MAX_TERMINAL_PASTE_LENGTH + 1}
            onChange={(event) => { setValue(event.target.value); setError(""); }} />
        )}
        {error ? <p className="terminal-clipboard-error" role="alert">{error}</p> : null}
        <footer>
          <button type="button" className="ghost-button" onClick={onCancel}>{copyOnly ? "关闭" : "取消"}</button>
          {!copyOnly ? <button type="submit" className="connect-button" disabled={!value.trim()}>{multiline ? "按行填入" : "填入终端"}</button> : null}
        </footer>
      </form>
    </div>
  );
}
