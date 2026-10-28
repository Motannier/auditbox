"""Gate terminal input before command execution."""

from __future__ import annotations

import logging
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from auditbox.audit.logger import AuditLogger
from auditbox.branding import product_name
from auditbox.device_profiles import LINUX, get_device_profile
from auditbox.relay.command_cleaner import _ESC_RE, clean_command
from auditbox.relay.terminal_line import TerminalLineSync
from auditbox.relay.input_frames import InputDecoder, INPUT_SOURCES
from auditbox.security.alert import check as alert_check

log = logging.getLogger(__name__)

_HISTORY_UP = (b"\x1b[A", b"\x1bOA")
_HISTORY_DOWN = (b"\x1b[B", b"\x1bOB")
_HISTORY_NAV_SEQUENCES = _HISTORY_UP + _HISTORY_DOWN
_SENSITIVE_PROMPT_RE = re.compile(
    r"(?i)(?:password|passwd|passphrase|口令|密码|验证码|token|secret)[^\r\n]{0,96}[:：]\s*$"
)

_SNAPSHOT_TIMEOUT = 2.0
_SNAPSHOT_SETTLE = 0.08


@dataclass
class _LineRead:
    purpose: str
    separator: bytes
    source: str
    started: float
    output_at: float | None = None
    notified: bool = False


class CommandGate:
    """Forward typed bytes, but decide whether to forward the final Enter."""

    def __init__(
        self,
        audit: AuditLogger,
        target_send: Callable[[bytes], object],
        client_send: Callable[[bytes], object],
        device_profile: str = LINUX,
        input_framing: bool = False,
    ):
        self._audit = audit
        self._target_send = target_send
        self._client_send = client_send
        profile = get_device_profile(device_profile) or get_device_profile(LINUX)
        self._device_profile = profile.key
        self._line_clear_sequence = profile.line_clear_sequence
        self._line_redraw_sequence = profile.line_redraw_sequence
        self._pager_prompt_re = (
            re.compile(profile.pager_prompt_pattern, re.IGNORECASE)
            if profile.pager_prompt_pattern
            else None
        )
        self._command_error_re = (
            re.compile(profile.command_error_pattern)
            if profile.command_error_pattern
            else None
        )
        self._ready_prompt_re = (
            re.compile(profile.ready_prompt_pattern)
            if profile.ready_prompt_pattern
            else None
        )
        self._input_buf = bytearray()
        self._line_sync = TerminalLineSync()
        self._output_tail = ""
        self._used_history_nav = False
        self._requires_remote_line = False
        self._line_read: _LineRead | None = None
        self._confirmation_from_echo = False
        self._pending_confirmation: tuple[str, str, bytes, str] | None = None
        self._input_decoder = InputDecoder(input_framing)
        self._input_source = "human"
        self._sensitive_input = False
        self._pager_active = False
        self._pending_result_command: str | None = None
        self._pending_result_failed = False
        self._lock = threading.RLock()

    @property
    def poll_interval(self) -> float:
        return 0.05 if self._line_read else 1.0

    def feed_transport(self, data: bytes) -> None:
        for decoded, source, context in self._input_decoder.feed_with_context(data):
            with self._lock:
                if hasattr(self._audit, "set_input_context"):
                    self._audit.set_input_context(context if source == "agent" else {})
                self.feed(decoded, source=source)

    def feed(self, data: bytes, *, source: str = "human") -> None:
        if source not in INPUT_SOURCES:
            raise ValueError("Invalid terminal input source")
        with self._lock:
            if self._pending_confirmation or self._line_read:
                self._log_terminal_input(data, forwarded=False)
                # A cancelled line is never submitted to obtain a fresh prompt.
                # Ctrl+C remains available if the device has not reprompted yet.
                if self._line_read and self._line_read.purpose == "clear" and data == b"\x03":
                    self._target_send(data)
                return
            self._log_terminal_input(data, sensitive=self._sensitive_input)
            i = 0
            while i < len(data):
                if self._pending_confirmation or self._line_read:
                    return
                if self._pager_active:
                    if data.startswith(b"\r\n", i):
                        self._target_send(b"\r\n")
                        self._pager_active = False
                        i += 2
                        continue
                    pager_key = data[i:i + 1]
                    if pager_key in {b" ", b"q", b"Q", b"\r", b"\n", b"\x03"}:
                        self._target_send(pager_key)
                        self._pager_active = False
                        i += 1
                        continue
                if self._sensitive_input:
                    if data.startswith(b"\r\n", i):
                        self._target_send(b"\r\n")
                        self._finish_sensitive_input()
                        i += 2
                        continue
                    byte = data[i:i + 1]
                    self._target_send(byte)
                    if byte in (b"\r", b"\n"):
                        self._finish_sensitive_input()
                    i += 1
                    continue
                # A guide-filled line stays guide-originated even when the
                # user edits it or presses Enter. Clearing/recalling starts a
                # fresh line; source is independent of execution permission.
                if source != "human":
                    self._input_source = source
                if data.startswith(b"\r\n", i):
                    self._handle_separator(b"\r\n")
                    i += 2
                    continue

                matched = False
                for seq in _HISTORY_NAV_SEQUENCES:
                    if data.startswith(seq, i):
                        self._input_source = source
                        self._handle_history_nav(seq)
                        i += len(seq)
                        matched = True
                        break
                if matched:
                    continue

                byte = data[i:i + 1]
                if byte < b" " and byte not in (b"\r", b"\n", b"\x03", b"\x08", b"\x15"):
                    self._requires_remote_line = True
                if byte == b"\x15" and self._device_profile != LINUX:
                    self._requires_remote_line = True
                if byte in (b"\x03", b"\x15", b"\x18"):
                    self._input_source = source
                if byte == b"\x03":
                    self._input_buf.clear()
                    self._requires_remote_line = False
                    self._used_history_nav = False
                    self._line_sync.reset()
                if byte in (b"\r", b"\n"):
                    self._handle_separator(byte)
                    i += 1
                    continue

                self._input_buf.extend(byte)
                self._target_send(byte)
                i += 1

    def feed_output(self, data: bytes) -> None:
        """解析远端 PTY 回显，跟踪历史召回后的真实命令行。"""
        with self._lock:
            if data:
                self._log_terminal_output(data)
                self._line_sync.feed(data)
                if self._line_read:
                    self._line_read.output_at = time.monotonic()
                decoded = _ESC_RE.sub("", data.decode(errors="replace"))
                self._output_tail = (self._output_tail + decoded)[-512:]
                self._pager_active = bool(
                    self._pager_prompt_re
                    and self._pager_prompt_re.search(self._output_tail)
                )
                if _SENSITIVE_PROMPT_RE.search(self._output_tail):
                    self._sensitive_input = True
                if self._pending_result_command:
                    if self._command_error_re and self._command_error_re.search(
                        self._output_tail
                    ):
                        self._pending_result_failed = True
                    if self._ready_prompt_re and self._ready_prompt_re.search(
                        self._output_tail
                    ):
                        self._finish_pending_command_result()

    def confirm(self, confirmation_id: str) -> bool:
        """Execute a pending command after a matching one-time confirmation."""
        with self._lock:
            pending = self._pending_confirmation
            if not pending:
                self._notify(f"\r\n\x1b[33m[{product_name()}] 没有待确认的命令\x1b[0m\r\n")
                return False
            token, command, separator, source = pending
            if not secrets.compare_digest(token, confirmation_id):
                self._notify(f"\r\n\x1b[33m[{product_name()}] 确认无效\x1b[0m\r\n")
                return False
            if self._line_read:
                return False
            if self._confirmation_from_echo or self._line_sync.command is not None:
                # Approval is bound to the snapshot shown in the dialog, not
                # any replacement line which appeared while it was open.
                self._start_line_read("confirm", separator, source)
                return True
            self._complete_confirmation(command, separator, source)
            return True

    def _complete_confirmation(self, command: str, separator: bytes, source: str) -> None:
        self._pending_confirmation = None
        self._confirmation_from_echo = False
        self._input_buf.clear()
        self._used_history_nav = False
        self._requires_remote_line = False
        self._audit_event("log_confirmation_result", command, True, source=source)
        log_authorized = getattr(self._audit, "log_authorized_command", None)
        if log_authorized:
            self._audit_event("log_authorized_command", command, "single_execution_confirmation", source=source)
        else:
            self._audit_event("log_command", command, source=source)
        self._line_sync.reset()
        self._output_tail = ""
        self._pager_active = False
        self._begin_command_result(command)
        self._notify(
            "\r\n\x1b[31m[安全审查] 已再次确认，仅授权本次高风险操作\x1b[0m\r\n"
        )
        # Typed bytes already reside in the device's editor. Replaying
        # them (especially Tab) can append or expand a different command.
        self._target_send(separator)

    def cancel_confirmation(self, confirmation_id: str) -> bool:
        with self._lock:
            pending = self._pending_confirmation
            if not pending or not secrets.compare_digest(pending[0], confirmation_id):
                return False
            _, command, separator, source = pending
            self._pending_confirmation = None
            self._line_read = None
            self._input_buf.clear()
            self._used_history_nav = False
            self._requires_remote_line = False
            verify_clear = self._confirmation_from_echo or self._line_sync.command is not None
            self._confirmation_from_echo = False
            self._line_sync.reset()
            self._audit_event("log_confirmation_result", command, False, source=source)
            self._notify("\r\n\x1b[33m[安全审查] 高风险操作已取消\x1b[0m\r\n")
            if verify_clear:
                self._line_read = _LineRead("clear", b"", source, time.monotonic())
            # End makes the clear-to-left operation independent of cursor
            # position. Ctrl+C reprompts; critically, there is NO Enter here.
            self._target_send(b"\x05" + self._line_clear_sequence + b"\x03")
            return True

    def mark_sensitive_input_complete(self) -> None:
        """Clear password-prompt state after credentials were supplied out of band."""
        with self._lock:
            self._finish_sensitive_input()

    def _handle_history_nav(self, seq: bytes) -> None:
        self._used_history_nav = True
        self._requires_remote_line = True
        self._input_buf.clear()
        self._target_send(seq)

    def _resolve_command(self, raw: bytes) -> str:
        self._line_sync.finalize_pending()
        decoded = raw.decode(errors="replace")
        typed = clean_command(decoded, strip_leading=False)
        if typed:
            remote = self._line_sync.command
            # For ordinary unedited input, an incomplete/late echo must not
            # replace the full submitted text. Native edits use a fresh redraw.
            if remote is not None and remote.strip() == typed.strip():
                return remote.strip()
            return clean_command(decoded)
        # Remote output is only authoritative after the user explicitly asks
        # the terminal to redraw a history entry.  Treating an arbitrary last
        # screen line as input makes pager prompts (for example
        # ``---- More ----``) and command output enter audit/recommendation when
        # the user presses an empty Enter to continue.
        return ""

    def _handle_separator(self, separator: bytes) -> None:
        source = self._input_source
        if self._requires_remote_line:
            self._start_line_read("submit", separator, source)
            return
        self._input_source = "human"
        raw = bytes(self._input_buf)
        self._input_buf.clear()

        command = self._resolve_command(raw)
        self._used_history_nav = False

        self._submit_command(command, separator, source, from_echo=False)

    def _submit_command(self, command: str, separator: bytes, source: str, *, from_echo: bool) -> None:
        self._input_source = "human"
        self._input_buf.clear()
        self._used_history_nav = False
        self._requires_remote_line = False

        if not command:
            self._line_sync.reset()
            self._target_send(separator)
            return

        # Reaching another submitted command means the prior prompt was ready
        # even if its exact rendering was not recognized.
        self._finish_pending_command_result()

        alert = alert_check(command, device_profile=self._device_profile)
        if alert and alert.get("action") == "block":
            self._audit_event("log_blocked_command", command, alert, source=source)
            self._clear_remote_line(verify=from_echo or self._line_sync.command == command)
            self._notify_blocked(command, alert)
            return

        if alert and alert.get("action") == "confirm":
            self._request_confirmation(command, separator, alert, source=source,
                                       from_echo=from_echo or self._line_sync.command == command)
            return

        self._audit_event("log_command", command, source=source)
        self._line_sync.reset()
        self._output_tail = ""
        self._pager_active = False
        self._begin_command_result(command)
        self._target_send(separator)

    def _clear_remote_line(self, *, verify: bool = False) -> None:
        try:
            if verify:
                self._line_read = _LineRead("clear", b"", "human", time.monotonic())
            if self._line_redraw_sequence or not verify:
                self._line_sync.reset()
            self._target_send(b"\x05" + self._line_clear_sequence)
            if verify:
                self._target_send(self._line_redraw_sequence or b"\x01\x05")
        except Exception as exc:
            log.warning("[%s] 清除远端输入行失败: %s", self._audit.session_id, exc)

    def _notify_blocked(self, command: str, alert: dict) -> None:
        reason = alert.get("reason", "高危命令")
        self._notify(
            f"\r\n\x1b[31m[安全审查] 检测到高风险命令，已暂停执行：{command} - {reason}\x1b[0m\r\n"
        )

    def _audit_event(self, name: str, *args, source: str) -> None:
        callback = getattr(self._audit, name)
        # Retain compatibility with raw-client audit adapters.
        callback(*args, **({"source": source} if source != "human" else {}))

    def _request_confirmation(self, command: str, separator: bytes, alert: dict, *, source: str,
                              from_echo: bool = False) -> None:
        confirmation_id = secrets.token_urlsafe(18)
        self._pending_confirmation = (
            confirmation_id,
            command,
            separator,
            source,
        )
        self._confirmation_from_echo = from_echo
        self._audit_event(
            "log_confirmation_required",
            command,
            alert,
            confirmation_id,
            source=source,
        )
        self._notify(
            f"\r\n\x1b[33m[安全审查] 检测到高风险命令，已暂停执行，请在页面中二次确认："
            f"{command}\x1b[0m\r\n"
        )

    def _start_line_read(self, purpose: str, separator: bytes, source: str) -> None:
        self._line_read = _LineRead(purpose, separator, source, time.monotonic())
        if self._line_redraw_sequence:
            self._line_sync.reset()
            self._target_send(b"\x05" + self._line_redraw_sequence)
        else:
            # On readline Ctrl+R starts history SEARCH, unlike network CLIs.
            # Probe End/Home/End instead, retaining the parsed current line.
            # The echoed cursor movement is an ordered fence after prior edits.
            self._target_send(b"\x05\x01\x05")

    def poll(self) -> None:
        """Advance a redraw without blocking the relay which must read its echo."""
        with self._lock:
            read = self._line_read
            if not read:
                return
            now = time.monotonic()
            command = self._line_sync.command
            ready = (read.output_at is not None
                     and (not self._line_redraw_sequence or self._line_sync.redrawn)
                     and command is not None and now - read.output_at >= _SNAPSHOT_SETTLE)
            if ready and read.purpose == "clear":
                if not command:
                    self._line_read = None
                    self._line_sync.reset()
                    return
            elif ready:
                self._line_read = None
                command = command.strip()
                if read.purpose == "submit":
                    if command:
                        self._submit_command(command, read.separator, read.source, from_echo=True)
                    else:
                        self._input_buf.clear()
                        self._requires_remote_line = False
                        self._used_history_nav = False
                        self._input_source = "human"
                        self._notify("\r\n\x1b[33m[安全审查] 待提交命令为空，未执行\x1b[0m\r\n")
                elif self._pending_confirmation:
                    _, approved, separator, source = self._pending_confirmation
                    if command == approved:
                        self._complete_confirmation(command, separator, source)
                    else:
                        self._pending_confirmation = None
                        self._audit_event("log_confirmation_result", approved, False, source=source)
                        if command:
                            alert = alert_check(command, device_profile=self._device_profile) or {
                                "action": "confirm", "level": "danger", "lock_terminal": False,
                                "reason": "待提交内容已变化，请重新确认当前命令。",
                            }
                            if alert.get("action") == "block":
                                self._audit_event("log_blocked_command", command, alert, source=source)
                                self._clear_remote_line(verify=True)
                                self._notify_blocked(command, alert)
                            else:
                                self._request_confirmation(command, separator, alert, source=source, from_echo=True)
                        else:
                            self._notify("\r\n\x1b[33m[安全审查] 待提交命令已清空，本次授权已取消\x1b[0m\r\n")
                return
            if now - read.started >= _SNAPSHOT_TIMEOUT and not read.notified:
                read.notified = True
                if read.purpose == "clear":
                    self._notify("\r\n\x1b[33m[安全审查] 正在等待设备清空输入；可按 Ctrl+C 恢复提示符\x1b[0m\r\n")
                    return
                self._line_read = None
                if read.purpose == "confirm" and self._pending_confirmation:
                    _, approved, _, source = self._pending_confirmation
                    self._pending_confirmation = None
                    self._input_buf = bytearray(approved.encode("utf-8"))
                    self._audit_event("log_confirmation_result", approved, False, source=source)
                self._requires_remote_line = True
                self._notify("\r\n\x1b[33m[安全审查] 未取得完整待提交命令，未发送回车；请等待设备回显后重试，或按 Ctrl+C 取消\x1b[0m\r\n")

    def _notify(self, message: str) -> None:
        try:
            self._client_send(message.encode("utf-8"))
        except Exception as exc:
            log.warning("[%s] 命令安全提示发送失败: %s", self._audit.session_id, exc)

    def _finish_sensitive_input(self) -> None:
        self._input_source = "human"
        self._sensitive_input = False
        self._input_buf.clear()
        self._used_history_nav = False
        self._requires_remote_line = False
        self._line_sync.reset()
        self._output_tail = ""
        self._pager_active = False

    def _begin_command_result(self, command: str) -> None:
        if not self._ready_prompt_re:
            return
        self._pending_result_command = command
        self._pending_result_failed = False

    def _finish_pending_command_result(self) -> None:
        command = self._pending_result_command
        if not command:
            return
        failed = self._pending_result_failed
        self._pending_result_command = None
        self._pending_result_failed = False
        callback = getattr(self._audit, "log_command_result", None)
        if callback:
            callback(command, success=not failed)

    def _log_terminal_input(
        self,
        data: bytes,
        *,
        sensitive: bool = False,
        forwarded: bool = True,
    ) -> None:
        callback = getattr(self._audit, "log_terminal_input", None)
        if callback:
            callback(data, sensitive=sensitive, forwarded=forwarded)

    def _log_terminal_output(self, data: bytes) -> None:
        callback = getattr(self._audit, "log_terminal_output", None)
        if callback:
            callback(data)
