import json

from agno.run.base import RunContext

from auditbox_agent.config import Settings
from auditbox_agent.runtime import TerminalWorkspaceTools


def test_terminal_tool_propagates_server_owned_login_and_exact_chat_turn(tmp_path, monkeypatch):
    tools = TerminalWorkspaceTools(Settings(data_dir=tmp_path))
    captured = {}

    def request(method, path, **kwargs):
        captured.update(kwargs["payload"])
        return json.dumps({"status": "completed"})

    monkeypatch.setattr(tools, "_request", request)
    context = RunContext(
        run_id="run-actual",
        session_id="chat-actual",
        metadata={
            "display_message": "查询版本",
            "audit_actor": {"account": "opsadmin", "login_id": "login-actual"},
        },
    )
    tools.send_terminal_command(
        "display version", "terminal-actual", purpose="读取版本", run_context=context
    )
    assert captured["audit_context"] == {
        "conversation_id": "chat-actual",
        "run_id": "run-actual",
        "question": "查询版本",
        "purpose": "读取版本",
        "actor": {"account": "opsadmin", "login_id": "login-actual"},
    }
    function = tools.functions["send_terminal_command"]
    function.process_entrypoint()
    assert "run_context" not in function.parameters.get("properties", {})
    assert function.requires_confirmation is True
