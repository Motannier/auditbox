"""User actions in the existing behavior projection and JSONL export format."""

import fcntl
import hashlib
import json
from pathlib import Path

from auditbox.audit.store import now, redact, safe_audit
from auditbox.env import AUDITBOX_AUDIT_DIR


GUIDE_ACTIONS = {
    "guide_scenario_opened", "guide_batch_started", "guide_queue_resumed",
    "guide_command_edited", "guide_command_submitted", "guide_command_skipped",
    "guide_command_confirmation_accepted", "guide_command_confirmation_cancelled",
}
TERMINAL_ACTIONS = {"terminal_suggestion_applied", "terminal_copy", "terminal_paste", "terminal_input_cancelled"}


def bounded_details(value, depth=0):
    if depth > 4:
        return "[内容过长]"
    if isinstance(value, str):
        # Redact before truncation so a long secret cannot straddle the limit.
        if value.lstrip().startswith(("{", "[")):
            try:
                structured = json.loads(value)
                if isinstance(structured, (dict, list)):
                    value = json.dumps(redact(structured), ensure_ascii=False)
            except (ValueError, RecursionError):
                pass
        return redact(value)[:4096]
    if isinstance(value, dict):
        return redact({str(k)[:64]: bounded_details(v, depth + 1) for k, v in list(value.items())[:32]})
    if isinstance(value, list):
        return [bounded_details(v, depth + 1) for v in value[:50]]
    return value if isinstance(value, (bool, int, float)) or value is None else str(value)[:512]


def append_jsonl(path, record):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Proxy and gateway can append to the same session concurrently.
    with path.open("a", encoding="utf-8", newline="\n") as file:
        fcntl.flock(file, fcntl.LOCK_EX)
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def record_behavior(store, action, *, context=None, result="success", execution_id=None, audit_dir=None, **details):
    context = context or {}
    details = bounded_details(details)
    record = safe_audit(store.behavior, action, context=context, result=result,
                        execution_id=execution_id, **details)
    # A UI action without a terminal still belongs to its authenticated login.
    session = str(context.get("session_id") or "")
    if not session or not all(c.isascii() and (c.isalnum() or c in "-_") for c in session):
        identity = str(context.get("login_id") or context.get("account") or "anonymous")
        session = "user-" + hashlib.sha256(identity.encode()).hexdigest()[:24]
    safe_audit(append_jsonl, Path(audit_dir or AUDITBOX_AUDIT_DIR) / f"{session}.jsonl", {
        **context, "event": action, "ts": record["ts"] if record else now(), "result": result,
        "details": details,
    })
    return record
