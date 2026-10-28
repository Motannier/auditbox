"""Compact, session-scoped terminal evidence carried across Agent turns."""

from __future__ import annotations

import json
from typing import Any

VERIFIED_TERMINAL_STATE_KEY = "_verified_terminal_state"
MAX_OBSERVATIONS = 3
MAX_OUTPUT_CHARACTERS = 700


def update_verified_terminal_state(
    current: dict[str, Any] | None,
    event: dict[str, Any],
) -> dict[str, Any]:
    """Return updated state after one successful terminal command event."""
    if event.get("event") != "ToolCallCompleted":
        return dict(current or {})
    tool = event.get("tool") or event.get("tool_call") or {}
    if not isinstance(tool, dict):
        return dict(current or {})
    tool_name = str(tool.get("tool_name") or tool.get("name") or "")
    if not tool_name.endswith("send_terminal_command") or tool.get("tool_call_error"):
        return dict(current or {})
    tool_args = tool.get("tool_args") or tool.get("arguments") or {}
    if not isinstance(tool_args, dict):
        return dict(current or {})
    result = tool.get("result")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError:
            return dict(current or {})
    if not isinstance(result, dict) or result.get("status") not in {
        "completed",
        "completed_after_quiet",
    }:
        return dict(current or {})

    session_id = str(result.get("session_id") or tool_args.get("target_session_id") or "")
    command = str(result.get("command") or tool_args.get("command") or "").strip()
    if not session_id or not command:
        return dict(current or {})
    output = str(result.get("output") or "")[-MAX_OUTPUT_CHARACTERS:]
    existing = dict(current or {})
    observations = (
        list(existing.get("observations") or [])
        if str(existing.get("session_id") or "") == session_id
        else []
    )
    observations.append({"command": command, "output": output})
    return {
        "session_id": session_id,
        "connection_status": str(result.get("connection_status") or "unknown"),
        "observations": observations[-MAX_OBSERVATIONS:],
    }
