import pytest
from pydantic import ValidationError

from auditbox.gateway.app import AssistantAgentMessageRequest, AssistantRunApprovalRequest


@pytest.mark.parametrize("mode,expected", [
    ("standard", "standard"), ("enhanced", "enhanced"), ("fast", "enhanced")
])
def test_gateway_forwards_canonical_mode_for_chat_and_approval(mode, expected):
    assert AssistantAgentMessageRequest(message="hello", mode=mode).model_dump()["mode"] == expected
    assert AssistantRunApprovalRequest(mode=mode, tools=[{}], approved=True).model_dump()["mode"] == expected


@pytest.mark.parametrize("schema,payload", [
    (AssistantAgentMessageRequest, {"message": "hello"}),
    (AssistantRunApprovalRequest, {"tools": [{}], "approved": False}),
])
def test_gateway_modes_default_to_standard_and_reject_unknown(schema, payload):
    assert schema(**payload).mode == "standard"
    with pytest.raises(ValidationError):
        schema(**payload, mode="turbo")
