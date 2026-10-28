from auditbox_agent.terminal_state import (
    VERIFIED_TERMINAL_STATE_KEY,
    update_verified_terminal_state,
)


def completed(command: str, output: str, session_id: str = "terminal-1") -> dict:
    return {
        "event": "ToolCallCompleted",
        "tool": {
            "tool_name": "send_terminal_command",
            "tool_args": {"command": command, "target_session_id": session_id},
            "result": (
                '{"status":"completed","session_id":"'
                + session_id
                + '","command":"'
                + command
                + '","output":"'
                + output
                + '","connection_status":"connected"}'
            ),
        },
    }


def test_verified_terminal_state_keeps_three_recent_observations() -> None:
    state = {}
    for index in range(4):
        state = update_verified_terminal_state(state, completed(f"cmd-{index}", f"out-{index}"))

    assert state["session_id"] == "terminal-1"
    assert [item["command"] for item in state["observations"]] == ["cmd-1", "cmd-2", "cmd-3"]


def test_verified_terminal_state_resets_when_terminal_changes() -> None:
    state = update_verified_terminal_state({}, completed("pwd", "/root"))
    state = update_verified_terminal_state(state, completed("hostname", "router", "terminal-2"))

    assert state["session_id"] == "terminal-2"
    assert state["observations"] == [{"command": "hostname", "output": "router"}]


def test_failed_or_unrelated_tool_does_not_change_state() -> None:
    state = {"session_id": "terminal-1", "observations": [{"command": "pwd", "output": "/root"}]}

    assert update_verified_terminal_state(
        state, {"event": "ToolCallCompleted", "tool": {"tool_name": "read_terminal"}}
    ) == state


def test_verified_state_is_injected_only_for_matching_active_terminal(runtime) -> None:
    state = update_verified_terminal_state({}, completed("pwd", "/root"))
    message = runtime.context_manager._request_context(
        {
            "target": {"address": "192.168.88.1", "session_id": "terminal-1"},
            VERIFIED_TERMINAL_STATE_KEY: state,
        }
    )

    assert message is not None
    assert "<verified_terminal_state>" in str(message.content)
    assert '"output":"/root"' in str(message.content)
    assert message.temporary is False

    mismatch = runtime.context_manager._request_context(
        {
            "target": {"address": "192.168.88.1", "session_id": "terminal-2"},
            VERIFIED_TERMINAL_STATE_KEY: state,
        }
    )
    assert mismatch is not None
    assert "<verified_terminal_state>" not in str(mismatch.content)
