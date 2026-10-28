from auditbox_agent.api_support import (
    append_public_timeline,
    public_event,
    tool_display_name,
)


def test_tool_display_name_supports_mcp_prefix_and_local_tools() -> None:
    assert tool_display_name("operations_send_terminal_command") == "终端命令输入"
    assert tool_display_name("search_knowledge_base") == "知识库检索"


def test_public_tool_event_contains_user_facing_name() -> None:
    event = public_event(
        {
            "event": "ToolCallStarted",
            "run_id": "run-1",
            "tool": {
                "tool_name": "operations_send_terminal_command",
                "tool_call_id": "call-1",
            },
        }
    )

    assert event["tool_display_name"] == "终端命令输入"

    timeline: list[dict[str, object]] = []
    append_public_timeline(timeline, event)
    assert timeline == [
        {
            "kind": "event",
            "event": "ToolCallStarted",
            "created_at": None,
            "tool_name": "operations_send_terminal_command",
            "tool_display_name": "终端命令输入",
            "tool_call_id": "call-1",
        }
    ]


def test_unknown_tool_name_remains_readable() -> None:
    assert tool_display_name("custom_export_device_data") == "custom export device data"


def test_non_tool_timeline_event_has_no_tool_display_name() -> None:
    timeline: list[dict[str, object]] = []
    append_public_timeline(timeline, {"event": "RunStarted", "created_at": 1})
    assert "tool_display_name" not in timeline[0]
