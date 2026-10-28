"""Conservative, repeatable JSONL -> linked audit conversion.

Original files are never modified. A terminal session is NOT a login. Missing
input source and missing results remain unknown; chat links are never guessed.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from auditbox.audit.store import TABLES, AuditStore, now, redact

VERSION = 1
SESSION_ID = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")
ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return (
            parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)
            .astimezone(timezone.utc)
            .isoformat(timespec="microseconds")
        )
    except ValueError:
        return ""


def convert_session(box_id, session_id, rows, digest):
    """Return final projections. Position, never command text, identifies a row."""
    if not SESSION_ID.fullmatch(session_id):
        raise ValueError("Invalid legacy session ID")
    records, executions, inputs, buffers = [], [], {}, {}
    context = {"session_id": session_id, "login_id": None, "device_id": None}
    pending = current = None
    sequence = []

    def record(kind, index, raw, **fields):
        key = f"legacy-v{VERSION}|{box_id}|{session_id}|{kind}|{index}"
        value = {
            **context,
            "id": "legacy-" + hashlib.sha256(key.encode()).hexdigest()[:40],
            "ts": timestamp(raw.get("ts")),
            "legacy": {
                "version": VERSION,
                "session_id": session_id,
                "position": index,
                "sha256": digest,
                "login_note": "历史未记录登录关联",
            },
            **fields,
        }
        records.append((kind, value))
        return value

    def new_execution(index, raw):
        source = raw.get("input_source")
        source = source if source in {"human", "guide", "agent"} else "unknown"
        exe = record(
            "executions",
            index,
            raw,
            command=redact(str(raw.get("command") or "")),
            source=source,
            status="unknown",
            risk=raw.get("level") or "unknown",
            risk_reason=raw.get("reason") or "",
            output="",
            result_basis="历史未记录明确执行结果",
            authorized=raw.get("authorized") is True,
        )
        origin = raw.get("audit_context")
        origin = origin if source == "agent" and isinstance(origin, dict) else {}
        inp = record(
            "inputs",
            index,
            raw,
            execution_id=exe["id"],
            source=source,
            label={"human": "手动输入", "guide": "手动输入", "agent": "AI 会话"}.get(
                source, "历史未记录输入来源"
            ),
            **{
                k: v
                for k, v in origin.items()
                if k in {"conversation_id", "run_id", "question", "purpose"}
            },
        )
        exe["input_id"] = inp["id"]
        inputs[exe["id"]] = inp
        executions.append(exe)
        return exe

    def matches(exe, raw):
        return exe is not None and exe["command"] == redact(
            str(raw.get("command") or "")
        )

    def behavior(index, raw, action=None, exe=None):
        details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
        details = {
            **details,
            **{
                k: raw[k]
                for k in ("command", "level", "reason", "authorization")
                if k in raw
            },
        }
        return record(
            "behaviors",
            index,
            raw,
            action=action or raw.get("event", "legacy_event"),
            result="unknown",
            execution_id=exe["id"] if exe else None,
            details=details,
        )

    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise TypeError(f"Legacy record {index + 1} is not an object")
        if raw.get("session_id") and raw["session_id"] != session_id:
            raise ValueError(f"Legacy session identity mismatch at record {index + 1}")
        for key in ("account", "target", "protocol"):
            if raw.get(key):
                context[key] = str(raw[key])
        event = str(raw.get("event") or "unknown")
        if event == "terminal_input":
            # A keystroke, paste or Enter is not proof of a completed command.
            continue
        if event == "terminal_output":
            if current is not None:
                try:
                    data = (
                        base64.b64decode(raw["data_b64"], validate=True)
                        if "data_b64" in raw
                        else str(raw.get("data", "")).encode()
                    )
                except (binascii.Error, ValueError, TypeError) as exc:
                    raise ValueError(
                        f"Invalid terminal output at record {index + 1}"
                    ) from exc
                previous, total = buffers.get(current["id"], (b"", 0))
                buffers[current["id"]] = ((previous + data)[-65536:], total + len(data))
            continue
        if event == "command_confirmation_required":
            pending = new_execution(index, raw)
            pending["result_basis"] = "历史记录要求二次确认，未记录后续下发结果"
            current = None
            behavior(index, raw, "command_confirmation_required", pending)
        elif event == "command_confirmed":
            matched = pending if matches(pending, raw) else None
            if matched:
                matched["authorized"] = True
                matched["confirmed_at"] = timestamp(raw.get("ts"))
            behavior(index, raw, "command_confirmed", matched)
        elif event == "command_confirmation_cancelled":
            exe = pending if matches(pending, raw) else new_execution(index, raw)
            exe.update(
                status="cancelled", result_basis="历史明确记录取消二次确认，未下发"
            )
            behavior(index, raw, "command_cancelled", exe)
            sequence.append(exe)
            pending = current = None
        elif event in {"command", "blocked_command"}:
            exe = pending if matches(pending, raw) else new_execution(index, raw)
            pending = None
            source = raw.get("input_source")
            if source in {"human", "guide", "agent"}:
                exe["source"] = inputs[exe["id"]]["source"] = source
                inputs[exe["id"]]["label"] = (
                    "AI 会话" if source == "agent" else "手动输入"
                )
            if raw.get("authorized") is True:
                exe["authorized"] = True
            exe["legacy"]["command_position"] = index
            exe["status"] = "blocked" if event == "blocked_command" else "unknown"
            exe["result_basis"] = (
                "历史明确记录命令被拦截，未下发"
                if event == "blocked_command"
                else "历史仅记录命令下发，没有明确执行结果"
            )
            if event == "command":
                exe["submitted_at"] = timestamp(raw.get("ts"))
            current = exe if event == "command" else None
            sequence.append(exe)
        elif event == "command_result":
            if matches(current, raw):
                current["status"] = (
                    "failed" if raw.get("status") == "failed" else "unknown"
                )
                current["result_basis"] = (
                    "历史识别到设备错误提示"
                    if raw.get("status") == "failed"
                    else "旧版以提示符推断成功，未记录明确退出码"
                )
                current["completed_at"] = timestamp(raw.get("ts"))
                current["legacy"]["result_position"] = index
                current = None
            else:
                behavior(index, raw, "legacy_unmatched_result")
        elif event == "session_end":
            if pending:
                pending.update(
                    status="unknown",
                    result_basis="终端会话结束，历史未记录该命令的最终下发结果",
                )
            pending = current = None
            behavior(index, raw, "legacy_terminal_session_ended")
        elif event == "session_start":
            behavior(index, raw, "legacy_terminal_session_started")
        else:
            item = behavior(index, raw)
            if event in {"security_model_review", "security_rule_review"}:
                details = item["details"]
                evidence = details.get("evidence", [])
                evidence = evidence if isinstance(evidence, list) else []
                linked = []
                for seq in evidence:
                    if str(seq).isdigit() and 0 < int(seq) <= len(sequence):
                        exe = sequence[int(seq) - 1]
                        if "current" not in details:
                            exe.update(
                                risk=details.get("risk") or "unknown",
                                risk_reason=details.get("reason", ""),
                                review_source="rules" if "rule" in event else "model",
                            )
                        linked.append(exe["id"])
                for finding in (
                    details.get("findings", [])
                    if isinstance(details.get("findings"), list)
                    else []
                ):
                    match = re.search(r"seq=(\d+) risk=(\w+)", str(finding))
                    if match and 0 < int(match[1]) <= len(sequence):
                        exe = sequence[int(match[1]) - 1]
                        exe.update(
                            risk=match[2],
                            risk_reason=(details.get("current", {}).get("reason", "")
                                if str(details.get("current", {}).get("seq")) == match[1] else str(finding)),
                            review_source="rules" if "rule" in event else "model",
                        )
                        linked.append(exe["id"])
                item["details"]["execution_ids"] = sorted(set(linked))
                if len(set(linked)) == 1:
                    item["execution_id"] = linked[0]

    for exe in executions:
        data, total = buffers.get(exe["id"], (b"", 0))
        text = CONTROL.sub("", ANSI.sub("", data.decode("utf-8", errors="replace")))
        text = redact(text)
        exe["output"] = text[-16000:]
        exe["output_truncated"] = total > 65536 or len(text) > 16000
        if total:
            exe["legacy"]["output_note"] = (
                "按旧日志事件顺序归并，可能包含提示符、输入回显或异步设备输出"
            )
    return [(kind, redact(value)) for kind, value in records]


def read_session(path):
    if path.is_symlink() or path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError(f"Unsafe or oversized legacy file: {path.name}")
    content = path.read_bytes()
    try:
        lines = content.decode("utf-8").splitlines()
        if len(lines) > 200000 or any(not line.strip() for line in lines):
            raise ValueError("Too many records or blank line")
        rows = [json.loads(line) for line in lines]
    except (UnicodeError, ValueError) as exc:
        raise ValueError(f"Invalid legacy JSONL: {path.name}") from exc
    return rows, hashlib.sha256(content).hexdigest()


def migrate(store, directory, *, apply=False):
    """Dry-run by default; validate every file, then atomically write projections."""
    directory = Path(directory)
    if not directory.is_dir():
        raise ValueError("Legacy audit directory does not exist")
    box_id = store.info()["box_id"]
    with store.connect() as conn:
        has_marker = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='audit_legacy_imports'"
        ).fetchone()
        imported = (
            {
                r["session_id"]: r["sha256"]
                for r in conn.execute("SELECT * FROM audit_legacy_imports")
            }
            if has_marker
            else {}
        )
        native = {
            r[0]
            for table in TABLES.values()
            for r in conn.execute(
                f"SELECT DISTINCT session_id FROM {table} WHERE session_id IS NOT NULL"
            )
        }
    plans, skipped, existing = [], 0, 0
    counts = Counter()
    source_rows = 0
    for path in sorted(directory.glob("*.jsonl")):
        sid = path.stem
        rows, digest = read_session(path)
        if sid in imported:
            if imported[sid] != digest:
                raise ValueError(f"Previously converted source changed: {path.name}")
            existing += 1
            continue
        if sid in native:
            skipped += 1
            continue
        projections = convert_session(box_id, sid, rows, digest)
        plans.append((path, digest, len(rows), projections))
        counts.update(kind for kind, _ in projections)
        source_rows += len(rows)
    report = {
        "version": VERSION,
        "box_id": box_id,
        "applied": apply,
        "sessions": len(plans),
        "source_rows": source_rows,
        "records": dict(counts),
        "already_converted": existing,
        "native_sessions_skipped": skipped,
    }
    if not apply:
        return report
    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS audit_legacy_imports (session_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, source_rows INTEGER NOT NULL, converted_at TEXT NOT NULL, version INTEGER NOT NULL)"
        )
        for path, digest, count, projections in plans:
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError(f"Source changed during conversion: {path.name}")
            marker = conn.execute(
                "SELECT sha256 FROM audit_legacy_imports WHERE session_id=?",
                (path.stem,),
            ).fetchone()
            if marker:
                raise ValueError("Another conversion has run; retry the dry run")
            if any(
                conn.execute(
                    f"SELECT 1 FROM {table} WHERE session_id=? LIMIT 1", (path.stem,)
                ).fetchone()
                for table in TABLES.values()
            ):
                raise ValueError(
                    "Native records appeared during conversion; retry the dry run"
                )
            for kind, value in projections:
                store._put(conn, kind, value)
            conn.execute(
                "INSERT INTO audit_legacy_imports VALUES(?,?,?,?,?)",
                (path.stem, digest, count, now(), VERSION),
            )
    return report


if __name__ == "__main__":
    import argparse

    from auditbox.env import AUDITBOX_AUDIT_DIR

    parser = argparse.ArgumentParser(
        description="将历史 JSONL 转换为关联日志；默认只预览，不删除原文件"
    )
    parser.add_argument("--directory", default=str(AUDITBOX_AUDIT_DIR))
    parser.add_argument("--apply", action="store_true", help="完成一致性备份后正式写入")
    args = parser.parse_args()
    print(
        json.dumps(
            migrate(AuditStore(), args.directory, apply=args.apply), ensure_ascii=False
        )
    )
