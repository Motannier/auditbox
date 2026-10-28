"""审计日志：将命令、终端转录和界面操作写入 JSONL 文件。"""

import json
import logging
import re
import threading
import time
from datetime import datetime
from functools import wraps
from pathlib import Path

from auditbox.audit.store import AuditStore, redact, safe_audit
from auditbox.db.models import CommandAudit
from auditbox.db.repositories import CommandAuditRepository
from auditbox.env import AUDITBOX_AUDIT_DIR

log = logging.getLogger(__name__)

AUDIT_DIR = AUDITBOX_AUDIT_DIR

_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(\b(?:password|passwd|passphrase|token|secret|api[_-]?key)\b\s*=\s*)([^\s;&|]+)"
)
_SECRET_OPTION_RE = re.compile(
    r"(?i)(--(?:password|passwd|passphrase|token|secret|api[_-]?key)(?:=|\s+))([^\s;&|]+)"
)
_AUTHORIZATION_RE = re.compile(
    r"(?i)(authorization\s*:\s*(?:bearer|basic)\s+)([^\s\"']+)"
)


def _synchronized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._state_lock:
            return method(self, *args, **kwargs)
    return wrapped


class AuditLogger:
    def __init__(self, session_id: str, target: str, protocol: str,
                 account: str = "未登录",
                 command_hook=None, security_hook=None,
                 command_result_hook=None, login_id="", device_id="",
                 device_name="", device_profile=""):
        self.session_id   = session_id
        self.target       = target
        self.protocol     = protocol
        self.account      = account
        self._lock        = threading.Lock()
        self._state_lock = threading.RLock()
        self._closed = False
        self._last_output_at = 0.0
        self._command_hook = command_hook   # callable(cmd: str, metadata: dict) | None
        self._security_hook = security_hook # callable(cmd: str, alert: dict, blocked: bool) | None
        self._command_result_hook = command_result_hook
        self._command_repo = CommandAuditRepository()
        self._store = safe_audit(AuditStore)
        self._context = {"login_id": login_id or None, "session_id": session_id,
                         "account": account, "device_id": device_id or None,
                         "device_name": device_name, "target": target,
                         "protocol": protocol, "device_profile": device_profile}
        self._input_context = {}
        self._pending_execution = None
        self._current_execution = None
        self._output_text = ""
        self._output_timer = None
        self._execution_sequence = []

        Path(AUDIT_DIR).mkdir(parents=True, exist_ok=True)
        self._path = str(Path(AUDIT_DIR) / f"{session_id}.jsonl")

        self._write({
            "event":      "session_start",
            "session_id": session_id,
            "target":     target,
            "protocol":   protocol,
        })
        log.info("[%s] 审计日志: %s", session_id, self._path)

    @_synchronized
    def set_input_context(self, context):
        self._input_context = dict(context)

    @_synchronized
    def _business_command(self, command, source, status, *, authorized=False):
        if not self._store:
            return
        self._flush_output()
        self._output_text = ""
        if self._pending_execution and self._pending_execution.get("command") == redact(command):
            record = self._pending_execution
            safe_audit(self._store.update, "executions", record["id"], status=status,
                       authorized=authorized, submitted_at=_utc_now() if status == "submitted" else None)
        else:
            origin = self._input_context if source == "agent" else {}
            actor = origin.get("actor") if isinstance(origin.get("actor"), dict) else {}
            context = {**self._context, **{k: v for k, v in actor.items() if k in {"account", "login_id"}}}
            record = safe_audit(self._store.execution, context, command, source, status=status, origin=origin)
        if record:
            self._current_execution = record if status == "submitted" else None
            self._pending_execution = record if status == "awaiting_confirmation" else None
            if status != "awaiting_confirmation":
                self._execution_sequence.append(record["id"])
        return record

    @_synchronized
    def _flush_output(self):
        if self._store and self._current_execution and self._output_text:
            safe_audit(self._store.update, "executions", self._current_execution["id"],
                       output=redact(self._output_text), output_truncated=len(self._output_text) >= 16000)

    @_synchronized
    def _persist_output(self):
        if self._closed:
            return
        if time.monotonic() - self._last_output_at < 0.5:
            self._output_timer = threading.Timer(0.5, self._persist_output)
            self._output_timer.daemon = True
            self._output_timer.start()
            return
        try:
            self._flush_output()
        finally:
            self._output_timer = None

    def log_command(self, command: str, *, source: str = "human") -> None:
        self._log_command(command, authorized=False, authorization="", source=source)

    def log_authorized_command(
        self,
        command: str,
        authorization: str = "single_execution_confirmation",
        *, source: str = "human",
    ) -> None:
        """Record a command that the user explicitly authorized for one execution."""
        self._log_command(
            command,
            authorized=True,
            authorization=authorization,
            source=source,
        )

    def _log_command(
        self,
        command: str,
        *,
        authorized: bool,
        authorization: str,
        source: str,
    ) -> None:
        if not command.strip():
            return
        stored_command = _redact_command(command)
        self._business_command(command, source, "submitted", authorized=authorized)
        ts = _utc_now()
        record = {
            "event":   "command",
            "command": stored_command,
            "redacted": stored_command != command,
            "authorized": authorized,
            "input_source": source,
        }
        if authorization:
            record["authorization"] = authorization
        self._write(record, ts=ts)
        self._write_command_db(stored_command, ts)
        log.info(
            "[%s] CMD%s: %s",
            self.session_id,
            " [仅本次授权]" if authorized else "",
            stored_command,
        )
        self._notify_command_hook(
            stored_command,
            status="executed",
            authorized=authorized,
            authorization=authorization,
            source=source,
        )

    def log_blocked_command(self, command: str, alert: dict, *, source: str = "human") -> None:
        if not command.strip():
            return
        stored_command = _redact_command(command)
        record = self._business_command(command, source, "blocked")
        if record:
            safe_audit(self._store.update, "executions", record["id"], risk=alert.get("level"), risk_reason=alert.get("reason", ""))
        ts = _utc_now()
        self._write({
            "event":   "blocked_command",
            "input_source": source,
            "command": stored_command,
            "redacted": stored_command != command,
            "level":   alert.get("level", "danger"),
            "reason":  alert.get("reason", ""),
            "lock_terminal": False,
        }, ts=ts)
        self._write_command_db(stored_command, ts)
        log.warning(
            "[%s] BLOCKED CMD [%s]: %s - %s",
            self.session_id,
            alert.get("level", "danger"),
            stored_command,
            alert.get("reason", ""),
        )
        self._notify_command_hook(
            stored_command,
            status="blocked",
            authorized=False,
            authorization="",
            source=source,
        )
        if self._security_hook:
            try:
                self._security_hook(command, alert, True)
            except Exception as e:
                log.warning("[%s] security_hook 异常: %s", self.session_id, e)

    def log_confirmation_required(
        self,
        command: str,
        alert: dict,
        confirmation_id: str,
        *, source: str = "human",
    ) -> None:
        if not command.strip():
            return
        stored_command = _redact_command(command)
        record = self._business_command(command, source, "awaiting_confirmation")
        if record:
            safe_audit(self._store.update, "executions", record["id"], risk=alert.get("level"), risk_reason=alert.get("reason", ""))
        self._write({
            "event": "command_confirmation_required",
            "input_source": source,
            "command": stored_command,
            "redacted": stored_command != command,
            "level": alert.get("level", "danger"),
            "reason": alert.get("reason", ""),
        })
        if self._security_hook:
            try:
                self._security_hook(
                    command,
                    {
                        **alert,
                        "confirmation_id": confirmation_id,
                    },
                    False,
                )
            except Exception as e:
                log.warning("[%s] security_hook 异常: %s", self.session_id, e)

    @_synchronized
    def log_confirmation_result(self, command: str, confirmed: bool, *, source: str = "human") -> None:
        if not command.strip():
            return
        stored_command = _redact_command(command)
        pending = self._pending_execution
        if self._store and pending:
            safe_audit(self._store.behavior, "command_confirmed" if confirmed else "command_cancelled",
                       context={**self._context, "login_id": pending.get("login_id"), "account": pending.get("account")},
                       execution_id=pending["id"], command=stored_command)
            if not confirmed:
                self._business_command(command, source, "cancelled")
        ts = _utc_now()
        self._write({
            "event": "command_confirmed" if confirmed else "command_confirmation_cancelled",
            "input_source": source,
            "command": stored_command,
            "redacted": stored_command != command,
            "authorization": "single_execution_confirmation" if confirmed else "",
        }, ts=ts)
        if not confirmed:
            self._write_command_db(stored_command, ts)
            self._notify_command_hook(
                stored_command,
                status="cancelled",
                authorized=False,
                authorization="",
                source=source,
            )

    @_synchronized
    def log_command_result(self, command: str, *, success: bool) -> None:
        """Record the device's explicit acceptance/error result for a command."""
        if not command.strip():
            return
        stored_command = _redact_command(command)
        self._flush_output()
        if self._store and self._current_execution:
            safe_audit(self._store.update, "executions", self._current_execution["id"],
                       status="unknown" if success else "failed", completed_at=_utc_now(),
                       result_basis="已返回提示符，未获得明确退出码" if success else "识别到设备错误提示")
            self._current_execution = None
        self._write({
            "event": "command_result",
            "command": stored_command,
            "redacted": stored_command != command,
            "status": "succeeded" if success else "failed",
        })
        if not self._command_result_hook:
            return
        try:
            self._command_result_hook(stored_command, success)
        except Exception as exc:
            log.warning("[%s] command_result_hook 异常: %s", self.session_id, exc)

    def log_terminal_input(
        self,
        data: bytes,
        *,
        sensitive: bool = False,
        forwarded: bool = True,
    ) -> None:
        """Record browser-to-target bytes without ever persisting hidden input."""
        if not data:
            return
        record = {
            "event": "terminal_input",
            "byte_count": len(data),
            "forwarded": forwarded,
            "redacted": sensitive,
        }
        if sensitive:
            record["data"] = "[敏感输入已隐藏]"
        else:
            record["data"] = "[输入内容见命令记录]"
        self._write(record)

    @_synchronized
    def log_terminal_output(self, data: bytes) -> None:
        """Record target-to-browser bytes losslessly for later session playback."""
        if not data:
            return
        # Buffer only a bounded command result; no per-keystroke database writes.
        if self._current_execution:
            self._last_output_at = time.monotonic()
            self._output_text = (self._output_text + data.decode("utf-8", errors="replace"))[-16000:]
            if self._output_timer is None:
                self._output_timer = threading.Timer(0.5, self._persist_output)
                self._output_timer.daemon = True
                self._output_timer.start()

    def log_operation(self, event: str, details: dict | None = None) -> None:
        """Record a validated UI operation associated with this terminal session."""
        normalized_event = str(event or "").strip()
        if (
            not normalized_event.startswith(("guide_", "security_"))
            or len(normalized_event) > 64
        ):
            return
        self._write({
            "event": normalized_event,
            "details": _bounded_details(details or {}),
        })
        if self._store and normalized_event.startswith("security_"):
            details = details or {}
            safe_audit(self._store.behavior, normalized_event, context=self._context, **redact(details))
            for seq in ([] if "current" in details else details.get("evidence", [])):
                if isinstance(seq, int) and 0 < seq <= len(self._execution_sequence):
                    safe_audit(self._store.update, "executions", self._execution_sequence[seq - 1],
                               risk=details.get("risk", "unknown"), risk_reason=details.get("reason", ""),
                               review_source="rules" if "rule" in normalized_event else "model")
            for finding in details.get("findings", []):
                match = re.search(r"seq=(\d+) risk=(\w+)", str(finding))
                if match and 0 < int(match[1]) <= len(self._execution_sequence):
                    safe_audit(self._store.update, "executions", self._execution_sequence[int(match[1]) - 1],
                               risk=match[2], risk_reason=(details.get("current", {}).get("reason", "")
                                   if str(details.get("current", {}).get("seq")) == match[1] else str(finding)),
                               review_source="rules" if "rule" in normalized_event else "model")

    @_synchronized
    def close(self) -> None:
        self._closed = True
        if self._output_timer:
            self._output_timer.cancel()
        self._flush_output()
        if self._store:
            safe_audit(self._store.finish_session, self.session_id)
        self._write({"event": "session_end"})

    def _write(self, record: dict, ts: str | None = None) -> None:
        record.setdefault("account", self.account)
        record["ts"] = ts or _utc_now()
        try:
            with self._lock:
                with open(self._path, "a", encoding="utf-8", newline="\n") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            log.exception("[%s] 审计文件写入失败；记录可能不完整", self.session_id)

    def _write_command_db(self, command: str, ts: str) -> None:
        try:
            self._command_repo.create(CommandAudit(
                session_id=self.session_id,
                target=self.target,
                protocol=self.protocol,
                command=command,
                ts=ts,
            ))
        except Exception as e:
            log.warning("[%s] 命令数据库写入失败: %s", self.session_id, e)

    def _notify_command_hook(
        self,
        command: str,
        *,
        status: str,
        authorized: bool,
        authorization: str,
        source: str = "human",
    ) -> None:
        if not self._command_hook:
            return
        try:
            self._command_hook(command, {
                "status": status,
                "authorized": authorized,
                "authorization": authorization,
                "input_source": source,
            })
        except Exception as e:
            log.warning("[%s] command_hook 异常: %s", self.session_id, e)


def _utc_now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _redact_command(command: str) -> str:
    redacted = _SECRET_ASSIGNMENT_RE.sub(r"\1[已隐藏]", command)
    redacted = _SECRET_OPTION_RE.sub(r"\1[已隐藏]", redacted)
    return redact(_AUTHORIZATION_RE.sub(r"\1[已隐藏]", redacted))


def _bounded_details(details: dict) -> dict:
    """Keep client-originated metadata useful without allowing unbounded records."""
    bounded: dict = {}
    for key, value in list(details.items())[:24]:
        safe_key = str(key)[:64]
        if isinstance(value, str):
            text = value[:2048]
            bounded[safe_key] = _redact_command(text) if "command" in safe_key else text
        elif isinstance(value, (bool, int, float)) or value is None:
            bounded[safe_key] = value
        elif isinstance(value, list):
            items = [str(item)[:1024] for item in value[:64]]
            bounded[safe_key] = [_redact_command(item) for item in items] if "command" in safe_key else items
        elif isinstance(value, dict):
            bounded[safe_key] = {
                str(child_key)[:64]: str(child_value)[:1024]
                for child_key, child_value in list(value.items())[:24]
            }
        else:
            bounded[safe_key] = str(value)[:2048]
    return bounded
