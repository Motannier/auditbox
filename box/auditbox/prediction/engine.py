"""推荐引擎：按设备类型推送出厂流程建议并保留可选 Markov 预测。

- 默认使用确定性的 Linux/H3C 运维流程图，厂商命名空间相互隔离
- MarkovPredictor 及其历史数据保留，通过显式开关才能参与结果
- 命令写入审计日志后调用 on_command()，将推荐推送给当前终端
"""

import json
import logging
import socket
import threading
from datetime import datetime
from uuid import uuid4

from auditbox.device_profiles import (
    H3C_COMWARE,
    LINUX,
    default_device_profile,
    normalize_device_profile,
    recommendation_namespace,
    require_device_profile,
)
from auditbox.audit.store import redact
from auditbox.env import AUDITBOX_HISTORY_REVIEW_ENABLED
from auditbox.prediction.markov import MarkovPredictor, DB_PATH
from auditbox.recommendation import (
    MAX_RECOMMENDATIONS,
    advance_recommendation_session,
    create_recommendation_session,
    recommend_next_commands,
)
from auditbox.security.alert import check as alert_check
from auditbox.security.history_review import HistoryRiskReviewer
from auditbox.security.review_result import advisory_review
from auditbox.security.switch_history_review import uses_rule_review

log = logging.getLogger(__name__)


class PredictionEngine:
    def __init__(self, db_path=DB_PATH, *, enable_markov: bool = False):
        self._predictor = MarkovPredictor(db_path)
        self._enable_markov = enable_markov
        self._lock = threading.Lock()
        # session_id → {"device_type": str, "history": [str]}
        self._sessions: dict = {}
        # session_id → [socket, ...]
        self._subscribers: dict = {}
        # session_id → {"confirm": callable, "cancel": callable}
        self._confirmation_handlers: dict = {}
        # session_id → callable(event: str, details: dict)
        self._audit_handlers: dict = {}
        self._reviewer = (
            HistoryRiskReviewer(self._on_review_result)
            if AUDITBOX_HISTORY_REVIEW_ENABLED
            else None
        )
        # A long-running H3C/Linux model review must not queue rule-only devices
        # behind inference. Both workers retain session-local snapshot coalescing.
        self._rules_reviewer = (
            HistoryRiskReviewer(self._on_review_result, worker_name="auditbox-rule-history-review")
            if AUDITBOX_HISTORY_REVIEW_ENABLED
            else None
        )

    def register_session(
        self,
        session_id: str,
        protocol: str,
        target_key: str = "",
        device_type: str = "server",
        device_profile: str | None = None,
        recommendation_type: str | None = None,
    ) -> None:
        # recommendation_type is retained for old internal callers while all
        # current sessions provide the canonical device_profile.
        legacy_profile = {
            "linux": LINUX,
            "h3c": H3C_COMWARE,
        }.get(str(recommendation_type or "").strip().lower(), "")
        selected_profile = normalize_device_profile(
            device_profile or legacy_profile or default_device_profile(device_type)
        )
        profile = require_device_profile(device_type, selected_profile)
        namespace = recommendation_namespace(protocol, profile.key)
        with self._lock:
            self._sessions[session_id] = {
                "protocol":    protocol,
                "device_type": namespace,
                "device_category": profile.device_type,
                "device_profile": profile.key,
                "history":     [],
                "recommendation_state": create_recommendation_session(profile.key),
                "pending_recommendation_transition": None,
                "next_sequence": 1,
                "target_key": target_key,
                "review": _idle_review(),
            }
            self._subscribers[session_id] = []
        log.debug(
            "[%s] 预测会话已注册 protocol=%s namespace=%s",
            session_id,
            protocol,
            namespace,
        )

    def unregister_session(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)
            subs = self._subscribers.pop(session_id, [])
            self._confirmation_handlers.pop(session_id, None)
            self._audit_handlers.pop(session_id, None)
        if self._reviewer:
            self._reviewer.discard(session_id)
        if self._rules_reviewer:
            self._rules_reviewer.discard(session_id)
        for s in subs:
            try:
                s.close()
            except OSError:
                pass
        log.debug("[%s] 预测会话已注销", session_id)

    def add_subscriber(self, session_id: str, sock: socket.socket) -> bool:
        """terminal_app 订阅预测推送，返回是否成功（会话不存在返回 False）。"""
        with self._lock:
            if session_id not in self._subscribers:
                return False
            self._subscribers[session_id].append(sock)
            payload = self._review_payload_locked(session_id)
        try:
            sock.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode())
        except OSError:
            self.remove_subscriber(session_id, sock)
            return False
        log.info("[%s] 预测订阅者已加入", session_id)
        return True

    def remove_subscriber(self, session_id: str, sock: socket.socket) -> None:
        with self._lock:
            current = self._subscribers.get(session_id, [])
            self._subscribers[session_id] = [item for item in current if item is not sock]

    def register_confirmation_handler(self, session_id: str, confirm, cancel) -> None:
        with self._lock:
            if session_id in self._sessions:
                self._confirmation_handlers[session_id] = {
                    "confirm": confirm,
                    "cancel": cancel,
                }

    def register_audit_handler(self, session_id: str, callback) -> None:
        with self._lock:
            if session_id in self._sessions:
                self._audit_handlers[session_id] = callback

    def handle_control(self, session_id: str, payload: dict) -> bool:
        action = payload.get("type")
        if action == "guide_audit_event":
            event = str(payload.get("event") or "").strip()
            details = payload.get("details")
            if not event.startswith("guide_") or len(event) > 64 or not isinstance(details, dict):
                return False
            with self._lock:
                callback = self._audit_handlers.get(session_id)
            if not callback:
                return False
            callback(event, details)
            return True

        confirmation_id = str(payload.get("confirmation_id") or "")
        if action not in {"command_confirmation", "command_confirmation_cancel"}:
            return False
        if not confirmation_id or len(confirmation_id) > 128:
            return False
        with self._lock:
            handlers = self._confirmation_handlers.get(session_id)
        if not handlers:
            return False
        callback = handlers["confirm" if action == "command_confirmation" else "cancel"]
        return bool(callback(confirmation_id))

    def update_device_type(self, session_id: str, device_type: str) -> None:
        """Report banner detection without overriding the user's profile."""
        with self._lock:
            if session_id not in self._sessions:
                return
            session = self._sessions[session_id]
            profile = require_device_profile(
                session["device_category"],
                session["device_profile"],
            )
            namespace = session["device_type"]
            subs = list(self._subscribers.get(session_id, []))
        detected_type = str(device_type or "unknown").strip().lower() or "unknown"
        verified = detected_type in {"unknown", profile.key, profile.recommendation_namespace}
        log.info(
            "[%s] 设备横幅核验: selected=%s detected=%s verified=%s namespace=%s",
            session_id,
            profile.key,
            detected_type,
            verified,
            namespace,
        )
        if subs:
            data = (json.dumps({
                "type":         "device_type",
                "device_type":  profile.recommendation_namespace,
                "device_category": profile.device_type,
                "device_profile": profile.key,
                "detected_type": detected_type,
                "verified": verified,
                "display": profile.label,
            }) + "\n").encode()
            dead = []
            for s in subs:
                try:
                    s.sendall(data)
                except OSError:
                    dead.append(s)
            if dead:
                with self._lock:
                    current = self._subscribers.get(session_id, [])
                    self._subscribers[session_id] = [s for s in current if s not in dead]

    def on_command(
        self,
        session_id: str,
        cmd: str,
        metadata: dict | None = None,
    ) -> None:
        """命令经安全决策后，推送出厂流程推荐；Markov 默认不参与。"""
        metadata = metadata if isinstance(metadata, dict) else {}
        source = str(metadata.get("input_source") or "human")
        human_input = source == "human"
        status = str(metadata.get("status") or "executed").lower()
        if status not in {"executed", "blocked", "cancelled"}:
            status = "executed"
        with self._lock:
            sess = self._sessions.get(session_id)
            if sess is None:
                return
            recommendation_state_before = sess["recommendation_state"].snapshot()
            sess["pending_recommendation_transition"] = None
            previous_history = [
                item["command"]
                for item in sess["history"]
                if item.get("status") == "executed"
            ]
            entry = {
                "seq": sess["next_sequence"],
                "command": cmd,
                "ts": _utc_now(),
                "status": status,
                "authorized": bool(metadata.get("authorized")),
                "authorization": str(metadata.get("authorization") or "")[:64],
                "input_source": source,
            }
            sess["next_sequence"] += 1
            sess["history"].append(entry)
            sess["history"] = sess["history"][-100:]
            advance_recommendation_session(
                sess["recommendation_state"],
                cmd,
                status=status,
            )
            if status == "executed":
                sess["pending_recommendation_transition"] = {
                    "seq": entry["seq"],
                    "command": cmd,
                    "input_source": source,
                    "before": recommendation_state_before,
                }
            recommendation_state = sess["recommendation_state"].snapshot()
            sess["review"] = _queued_review(sess["review"], entry["seq"])
            history = [dict(item) for item in sess["history"]]
            device_type = sess["device_type"]
            device_profile = sess["device_profile"]
            protocol = sess["protocol"]
            subs = list(self._subscribers.get(session_id, []))
            review_payload = self._review_payload_locked(session_id)

        suggestions = (
            recommend_next_commands(
                cmd,
                device_profile=device_profile,
                limit=MAX_RECOMMENDATIONS,
                history=previous_history,
                session_state=recommendation_state,
            )
            if status == "executed" and human_input
            else []
        )

        if self._enable_markov and status == "executed" and human_input:
            self._predictor.learn(device_type, previous_history, cmd)
            new_history = [
                item["command"]
                for item in history
                if item.get("status") == "executed"
            ]
            markov = self._safe_suggestions(
                self._predictor.predict(device_type, new_history),
                device_profile=device_profile,
            )
            seen = {item["command"] for item in suggestions}
            suggestions.extend(
                item for item in markov
                if item["command"] not in seen
            )
            suggestions = suggestions[:MAX_RECOMMENDATIONS]

        log.debug("[%s] 出厂流程推荐(%d条): %s", session_id, len(suggestions), suggestions)

        # Empty results are pushed too, so an unrecognized command clears the
        # previous command's recommendations instead of leaving stale actions.
        if subs:
            self._push_payload(session_id, review_payload, subs)
            self._push(session_id, suggestions, subs, input_source=source)
        reviewer = self._rules_reviewer if uses_rule_review(device_profile) else self._reviewer
        if reviewer:
            reviewer.submit(
                session_id,
                protocol,
                device_type,
                history,
                device_profile=device_profile,
                review_token=review_payload["review_token"],
            )

    def on_command_result(
        self,
        session_id: str,
        cmd: str,
        *,
        success: bool,
    ) -> None:
        """Commit or roll back the latest switch state transition at its prompt."""
        with self._lock:
            sess = self._sessions.get(session_id)
            if sess is None:
                return
            pending = sess.get("pending_recommendation_transition")
            if not pending or pending.get("command") != cmd:
                return
            sess["pending_recommendation_transition"] = None
            if success:
                return

            sess["recommendation_state"] = pending["before"]
            sequence = pending["seq"]
            for entry in reversed(sess["history"]):
                if entry.get("seq") == sequence:
                    entry["status"] = "failed"
                    entry.pop("review", None)
                    break
            subs = list(self._subscribers.get(session_id, []))
            sess["review"] = _queued_review(sess["review"], sequence)
            review_payload = self._review_payload_locked(session_id)
            snapshot = ([dict(item) for item in sess["history"]], sess["protocol"],
                        sess["device_type"], sess["device_profile"])

        log.info("[%s] 设备拒绝命令，已回滚推荐会话状态: %s", session_id, cmd)
        if subs:
            self._push_payload(session_id, review_payload, subs)
            self._push(session_id, [], subs, input_source=pending.get("input_source", "human"))
        history, protocol, device_type, device_profile = snapshot
        reviewer = self._rules_reviewer if uses_rule_review(device_profile) else self._reviewer
        if reviewer:
            reviewer.submit(session_id, protocol, device_type, history, device_profile=device_profile,
                            review_token=review_payload["review_token"])

    def on_security_alert(
        self,
        session_id: str,
        cmd: str,
        alert: dict,
        blocked: bool = False,
    ) -> None:
        """Push a security alert that did not go through normal command logging."""
        with self._lock:
            subs = list(self._subscribers.get(session_id, []))
        if subs:
            self._push_alert(session_id, cmd, {**alert, "lock_terminal": False}, subs, blocked=blocked)

    def _push_alert(
        self,
        session_id: str,
        cmd: str,
        alert: dict,
        subs: list,
        blocked: bool = False,
    ) -> None:
        """
        通过旁路通道向订阅者推送危险命令告警。

        Args:
            session_id (str): 当前会话 ID，用于日志标注。
            cmd (str): 触发告警的原始命令文本。
            alert (dict): alert.check() 返回的告警信息，含 level 和 reason。
            subs (list): 当前会话的订阅 socket 列表。

        Returns:
            None。

        Raises:
            无，发送失败的 socket 从订阅列表中移除。

        Example:
            self._push_alert(sid, "rm -rf /", {"level": "danger", "reason": "..."}, subs)
        """
        log.warning("[%s] 危险命令告警 [%s]: %s - %s",
                    session_id, alert["level"], redact(cmd), redact(alert["reason"]))
        data = (json.dumps({
            "type":    "alert",
            "level":   alert["level"],
            "action":  alert.get("action", "block"),
            "command": cmd,
            "reason":  alert["reason"],
            "blocked": blocked,
            "confirmation_id": alert.get("confirmation_id"),
        }) + "\n").encode()
        dead = []
        for s in subs:
            try:
                s.sendall(data)
            except OSError:
                dead.append(s)
        if dead:
            with self._lock:
                current = self._subscribers.get(session_id, [])
                self._subscribers[session_id] = [s for s in current if s not in dead]

    def _push(self, session_id: str, suggestions: list, subs: list, *, input_source: str = "human") -> None:
        data = (json.dumps({"type": "recommendation", "suggestions": suggestions,
                            "input_source": input_source}) + "\n").encode()
        dead = []
        for s in subs:
            try:
                s.sendall(data)
            except OSError:
                dead.append(s)
        if dead:
            with self._lock:
                current = self._subscribers.get(session_id, [])
                self._subscribers[session_id] = [s for s in current if s not in dead]

    def _on_review_result(self, session_id: str, result: dict) -> None:
        result = advisory_review(result)
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return
            if (not result.get("review_token")
                    or result["review_token"] != session["review"].get("review_token")
                    or result.get("reviewed_through_seq") != session["next_sequence"] - 1):
                log.debug("[%s] 忽略过期旁路审查结果", session_id)
                return
            # A review belongs to this session only. It neither controls the
            # relay nor changes other sessions connected to the same target.
            for finding in result.get("findings", []):
                for item in session["history"]:
                    if item["seq"] == finding.get("seq") and item["status"] == finding.get("status"):
                        item["review"] = dict(finding)
            result["review_revision"] = session["review"]["review_revision"]
            session["review"] = result
            payload = self._review_payload_locked(session_id)
            subs = list(self._subscribers.get(session_id, []))
            audit_callback = self._audit_handlers.get(session_id)
            rule_based = uses_rule_review(session["device_profile"])

        if subs:
            self._push_payload(session_id, payload, subs)
        if audit_callback:
            # Reference existing redacted command logs, without copying raw
            # commands or credentials. Model risks remain auditable too.
            details = {
                "device_profile": payload["device_profile"],
                "risk": result.get("risk", "unknown"),
                "reason": result.get("reason", ""),
                "reviewed_through_seq": result.get("reviewed_through_seq", 0),
                "evidence": result.get("evidence", []),
                "current": result.get("current", {}),
                "findings": [
                    f"seq={item['seq']} risk={item['risk']} status={item['status']} "
                    f"rule={item['code']} source={item.get('rule_id', '')}"
                    for item in result.get("findings", [])
                ],
            }
            if rule_based:
                details.update({
                    "policy_version": result.get("policy_version", ""),
                })
            else:
                details["confidence"] = result.get("confidence", 0)
            audit_callback("security_rule_review" if rule_based else "security_model_review", details)

    def _review_payload_locked(self, session_id: str) -> dict:
        session = self._sessions[session_id]
        return {
            "type": "operation_review",
            **advisory_review(session["review"]),
            "review_source": "rules" if uses_rule_review(session["device_profile"]) else "model",
            "device_profile": session["device_profile"],
            "history": [dict(item) for item in session["history"][-20:]],
            "findings": [dict(item["review"]) for item in session["history"][-20:] if item.get("review")],
            "process": _review_process(session["review"]),
        }

    def _push_payload(self, session_id: str, payload: dict, subs: list) -> None:
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode()
        dead = []
        for subscriber in subs:
            try:
                subscriber.sendall(data)
            except OSError:
                dead.append(subscriber)
        if dead:
            with self._lock:
                current = self._subscribers.get(session_id, [])
                self._subscribers[session_id] = [item for item in current if item not in dead]

    def _safe_suggestions(
        self,
        suggestions: list[str],
        *,
        device_profile: str = LINUX,
    ) -> list[dict]:
        safe = []
        for command in suggestions:
            alert = alert_check(command, device_profile=device_profile)
            if alert and alert.get("action") == "block":
                continue
            safe.append({
                "command": command,
                "source": "prediction",
                "risk": "confirm" if alert else "normal",
            })
        return safe

    def close(self) -> None:
        if self._reviewer:
            self._reviewer.close()
        if self._rules_reviewer:
            self._rules_reviewer.close()
        self._predictor.close()


def _idle_review() -> dict:
    return {
        "status": "idle",
        "risk": "unknown",
        "intent": "",
        "reason": "等待产生终端命令",
        "evidence": [],
        "confidence": 0.0,
        "locked": False,
        "reviewed_at": "",
        "review_revision": 0,
    }


def _queued_review(previous: dict, sequence: int) -> dict:
    context = ({key: previous.get(key) for key in ("risk", "intent", "reason", "evidence", "reviewed_through_seq")}
               if previous.get("status") == "ready" else previous.get("context_review"))
    return {**_idle_review(), "status": "queued", "reason": "正在审查当前命令",
            "current": {"seq": sequence, "risk": "unknown", "reason": "正在审查当前命令"},
            "context_review": context, "review_token": uuid4().hex,
            "review_revision": previous.get("review_revision", 0) + 1}


def _review_process(review: dict) -> list[dict[str, str]]:
    """Expose real review-state progress instead of a timer-based visual simulation."""
    status = str(review.get("status") or "idle")
    if status == "ready":
        states = ("complete", "complete", "complete", "complete")
    elif status == "queued":
        # Command capture and deterministic rules finish synchronously before
        # HistoryRiskReviewer is queued; model or side-channel grading is active.
        states = ("complete", "complete", "active", "pending")
    elif status == "error":
        states = ("complete", "complete", "error", "pending")
    else:
        states = ("pending", "pending", "pending", "pending")
    labels = (
        "识别命令内容",
        "匹配安全规则",
        "分析可能影响",
        "生成处置结论",
    )
    return [
        {"id": f"step-{index + 1}", "label": label, "state": states[index]}
        for index, label in enumerate(labels)
    ]


def _utc_now() -> str:
    return datetime.utcnow().isoformat() + "Z"
