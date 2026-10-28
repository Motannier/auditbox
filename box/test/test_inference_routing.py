import json
import time

import pytest

from auditbox.inference import routing
from auditbox.inference.routing import (
    DEVICE_PROFILE_HEADER,
    resolve_inference_route,
)
from auditbox.inference.server import _route
from auditbox.security import history_review


def test_assistant_is_shared_while_review_is_isolated_by_device_profile(monkeypatch) -> None:
    monkeypatch.setattr(routing, "AUDITBOX_INFERENCE_PROFILES", ["linux", "h3c_comware"])
    linux = resolve_inference_route("linux")
    h3c = resolve_inference_route("h3c_comware")

    assert linux.model_for("assistant") == "minicpm5-2b"
    assert linux.model_for("review") == "command-guard"
    assert linux.backend_url("assistant") == "http://127.0.0.1:18081"
    assert linux.backend_url("review") == "http://127.0.0.1:18082"
    assert h3c.model_for("assistant") == "minicpm5-2b"
    assert h3c.model_for("review") == "h3c-risk"
    assert h3c.backend_url("assistant") == "http://127.0.0.1:18081"
    assert h3c.backend_url("review") == "http://127.0.0.1:8083"

    assert _route("/assistant/v1/chat/completions", "linux") == (
        "assistant",
        "/v1/chat/completions",
        "http://127.0.0.1:18081",
        "minicpm5-2b",
    )
    assert _route("/review/v1/chat/completions", "h3c_comware") == (
        "review",
        "/v1/chat/completions",
        "http://127.0.0.1:8083",
        "h3c-risk",
    )


def test_unknown_model_profile_is_rejected_instead_of_falling_back() -> None:
    with pytest.raises(ValueError, match="unsupported inference device profile"):
        resolve_inference_route("cisco_ios")


class _ReviewResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def __iter__(self):
        review = json.dumps(
            {
                "risk": "low",
                "intent": "查看接口",
                "reason": "仅执行只读查询",
                "evidence": [1],
                "confidence": 0.99,
                "lock": False,
            },
            ensure_ascii=False,
        )
        event = json.dumps({"choices": [{"delta": {"content": review}}]})
        yield f"data: {event}\n\n".encode()
        yield b"data: [DONE]\n\n"


def test_h3c_history_review_sends_h3c_alias_and_profile_header(monkeypatch) -> None:
    monkeypatch.setattr(routing, "AUDITBOX_INFERENCE_PROFILES", ["linux", "h3c_comware"])
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return _ReviewResponse()

    monkeypatch.setattr(history_review.urllib.request, "urlopen", fake_urlopen)
    now = time.monotonic()
    pending = history_review._PendingReview(
        session_id="switch-session",
        protocol="ssh",
        device_type="ssh_h3c",
        device_profile="h3c_comware",
        history=[{"seq": 1, "command": "display interface brief"}],
        first_queued_at=now,
        ready_at=now,
    )

    result = history_review._review_history(pending)

    request = captured["request"]
    payload = json.loads(request.data.decode())
    headers = {key.lower(): value for key, value in request.header_items()}
    review_input = json.loads(payload["messages"][1]["content"])
    assert payload["model"] == "h3c-risk"
    assert headers[DEVICE_PROFILE_HEADER.lower()] == "h3c_comware"
    assert review_input["device_profile"] == "h3c_comware"
    assert result["status"] == "ready"
    assert result["lock"] is False
