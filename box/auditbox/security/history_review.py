"""Asynchronous model/rule review of recent terminal command history."""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from typing import Callable

from auditbox.device_profiles import LINUX, normalize_device_profile
from auditbox.env import (
    AUDITBOX_HISTORY_REVIEW_INTERVAL,
    AUDITBOX_HISTORY_REVIEW_LLM_URL,
    AUDITBOX_HISTORY_REVIEW_TIMEOUT,
    AUDITBOX_HISTORY_REVIEW_WINDOW,
)
from auditbox.inference.prompts import HISTORY_REVIEW_SYSTEM_PROMPT
from auditbox.inference.routing import (
    DEVICE_PROFILE_HEADER,
    resolve_inference_route,
)
from auditbox.security.review_result import advisory_review
from auditbox.security.switch_history_review import uses_rule_review, review_switch_history

log = logging.getLogger(__name__)

@dataclass
class _PendingReview:
    session_id: str
    protocol: str
    device_type: str
    device_profile: str
    history: list[dict]
    first_queued_at: float
    ready_at: float
    review_token: str = ""


class ReviewPreempted(Exception):
    """The assistant took priority; retry the latest snapshot afterwards."""


class HistoryRiskReviewer:
    """Coalesce command snapshots and review them on one background worker."""

    def __init__(self, callback: Callable[[str, dict], None], *, worker_name: str = "auditbox-history-review"):
        self._callback = callback
        self._condition = threading.Condition()
        self._pending: dict[str, _PendingReview] = {}
        self._discarded: set[str] = set()
        self._closed = False
        self._worker = threading.Thread(
            target=self._run,
            name=worker_name,
            daemon=True,
        )
        self._worker.start()

    def submit(
        self,
        session_id: str,
        protocol: str,
        device_type: str,
        history: list[dict],
        *,
        device_profile: str = LINUX,
        review_token: str = "",
    ) -> None:
        now = time.monotonic()
        snapshot = [dict(item) for item in history[-AUDITBOX_HISTORY_REVIEW_WINDOW:]]
        with self._condition:
            self._discarded.discard(session_id)
            previous = self._pending.get(session_id)
            first_queued_at = previous.first_queued_at if previous else now
            ready_at = min(
                now + AUDITBOX_HISTORY_REVIEW_INTERVAL,
                first_queued_at + max(AUDITBOX_HISTORY_REVIEW_INTERVAL * 2, 3.0),
            )
            self._pending[session_id] = _PendingReview(
                session_id=session_id,
                protocol=protocol,
                device_type=device_type,
                device_profile=normalize_device_profile(device_profile) or LINUX,
                history=snapshot,
                first_queued_at=first_queued_at,
                ready_at=ready_at,
                review_token=review_token,
            )
            self._condition.notify()

    def discard(self, session_id: str) -> None:
        with self._condition:
            self._discarded.add(session_id)
            self._pending.pop(session_id, None)

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def _run(self) -> None:
        while True:
            pending = self._next_pending()
            if pending is None:
                return
            try:
                result = _review_history(pending)
            except ReviewPreempted:
                self._requeue_after_preemption(pending)
                continue
            except Exception as exc:
                is_rule_review = uses_rule_review(pending.device_profile)
                log.warning("[%s] 旁路审查失败: %s", pending.session_id, exc)
                result = {
                    "status": "error",
                    "risk": "unknown",
                    "intent": "",
                    "reason": "规则审计暂时不可用，风险未判定" if is_rule_review else "本地 AI 暂时不可用，智能审查未完成",
                    "evidence": [],
                    "confidence": 0.0,
                    "lock": False,
                    "reviewed_at": _utc_now(),
                    "reviewed_through_seq": _last_sequence(pending.history),
                }
                if is_rule_review:
                    result.update(review_source="rules", device_profile=pending.device_profile,
                                  findings=[], unverified_count=len(pending.history))
            try:
                self._callback(pending.session_id, {**result, "review_token": pending.review_token})
            except Exception as exc:
                log.warning("[%s] 智能审查回调失败: %s", pending.session_id, exc)

    def _requeue_after_preemption(self, pending: _PendingReview) -> None:
        with self._condition:
            if self._closed or pending.session_id in self._discarded:
                return
            if pending.session_id not in self._pending:
                pending.first_queued_at = time.monotonic()
                pending.ready_at = pending.first_queued_at
                self._pending[pending.session_id] = pending
            self._condition.notify()
        log.info("[%s] 智能审查已为运维助手让路，稍后重试", pending.session_id)

    def _next_pending(self) -> _PendingReview | None:
        with self._condition:
            while not self._closed:
                if not self._pending:
                    self._condition.wait()
                    continue
                next_item = min(self._pending.values(), key=lambda item: item.ready_at)
                remaining = next_item.ready_at - time.monotonic()
                if remaining > 0:
                    self._condition.wait(timeout=remaining)
                    continue
                return self._pending.pop(next_item.session_id)
            return None


def _review_history(pending: _PendingReview) -> dict:
    if uses_rule_review(pending.device_profile):
        return review_switch_history(pending.history, pending.device_profile)
    route = resolve_inference_route(pending.device_profile)
    commands = [
        {
            "seq": item.get("seq"),
            "command": item.get("command", ""),
            "status": item.get("status", "executed"),
            "authorized": bool(item.get("authorized")),
            "authorization": item.get("authorization", ""),
        }
        for item in pending.history
    ]
    payload = {
        "model": route.model_for("review"),
        "messages": [
            {"role": "system", "content": HISTORY_REVIEW_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "protocol": pending.protocol,
                        "device_type": pending.device_type,
                        "device_profile": route.device_profile,
                        "current_seq": _last_sequence(commands),
                        "history": commands,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            },
        ],
        "temperature": 0,
        "top_p": 0.1,
        "max_tokens": 320,
        "stream": True,
        "cache_prompt": True,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    request = urllib.request.Request(
        f"{AUDITBOX_HISTORY_REVIEW_LLM_URL}/v1/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            DEVICE_PROFILE_HEADER: route.device_profile,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=AUDITBOX_HISTORY_REVIEW_TIMEOUT) as response:
            content_parts: list[str] = []
            finished = False
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line.removeprefix("data:").strip()
                if not data:
                    continue
                if data == "[DONE]":
                    finished = True
                    break
                event = json.loads(data)
                error = event.get("error")
                if isinstance(error, dict):
                    if error.get("code") == "review_preempted":
                        raise ReviewPreempted()
                    raise OSError(str(error.get("message") or "本地 AI 审查失败"))
                delta = event.get("choices", [{}])[0].get("delta", {})
                text = delta.get("content")
                if text:
                    content_parts.append(str(text))
            if not finished:
                raise OSError("本地 AI 审查流提前结束")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise OSError(f"llama.cpp HTTP {exc.code}: {detail}") from exc

    content = "".join(content_parts)
    parsed = _parse_review_json(content)
    risk = str(parsed.get("risk") or "unknown").lower()
    if risk not in {"low", "medium", "high", "critical"}:
        risk = "unknown"
    confidence = min(max(float(parsed.get("confidence") or 0), 0.0), 1.0)
    valid_sequences = {int(item["seq"]) for item in commands if item.get("seq") is not None}
    evidence = []
    for value in parsed.get("evidence") or []:
        try:
            sequence = int(value)
        except (TypeError, ValueError):
            continue
        if sequence in valid_sequences and sequence not in evidence:
            evidence.append(sequence)
    intent = str(parsed.get("intent") or "")[:300]
    reason = str(parsed.get("reason") or "")[:600]
    sequence = _last_sequence(commands)
    current = parsed.get("current")
    if not isinstance(current, dict) or current.get("seq") != sequence:
        # A legacy window verdict is unambiguous only for a single command.
        current = {"seq": sequence, "risk": risk if len(commands) == 1 else "unknown",
                   "reason": reason if len(commands) == 1 else "模型未返回当前命令的独立结论"}
    current = {"seq": sequence,
               "risk": current.get("risk") if current.get("risk") in {"low", "medium", "high", "critical"} else "unknown",
               "reason": str(current.get("reason") or "当前命令风险未判定")[:600]}
    latest = next((item for item in commands if item.get("seq") == sequence), {})
    if len(commands) > 1 and current["risk"] != "low":
        # Small fine-tuned models can ignore the scope even with correct JSON.
        # Recheck only this command, retaining the original window/chain verdict.
        try:
            isolated = _review_history(replace(pending, history=[latest]))
            current = isolated["current"]
        except ReviewPreempted:
            raise
        except Exception as exc:
            log.warning("[%s] 当前命令独立审查失败: %s", pending.session_id, exc)
            current = {"seq": sequence, "risk": "unknown", "reason": "当前命令独立审查未完成"}
    # The trained response schema can still contain `lock`; it is advisory
    # metadata, never authority to freeze a session or downgrade its risk.
    return advisory_review({
        "status": "ready",
        "risk": risk,
        "intent": intent,
        "reason": reason,
        "evidence": evidence,
        "confidence": confidence,
        "current": current,
        "findings": [{**current, "status": latest.get("status", "executed"), "code": "model_current"}],
        "reviewed_at": _utc_now(),
        "reviewed_through_seq": _last_sequence(pending.history),
    })


def _parse_review_json(content: str) -> dict:
    text = content.strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```")
        text = text.removesuffix("```").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("AI 未返回 JSON 审查结果")
    parsed = json.loads(text[start:end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("AI 审查结果不是对象")
    return parsed


def _utc_now() -> str:
    from datetime import datetime

    return datetime.utcnow().isoformat() + "Z"


def _last_sequence(history: list[dict]) -> int:
    sequences = []
    for item in history:
        try:
            sequences.append(int(item.get("seq")))
        except (TypeError, ValueError):
            continue
    return max(sequences, default=0)
