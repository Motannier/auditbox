import pytest
from pydantic import ValidationError

from auditbox_agent.schemas import ChatRequest, RunApprovalRequest


@pytest.mark.parametrize("mode,expected", [
    ("standard", "standard"), ("enhanced", "enhanced"), ("fast", "enhanced")
])
def test_chat_and_approval_normalize_same_mode(mode, expected):
    assert ChatRequest(message="hello", mode=mode).model_dump()["mode"] == expected
    assert RunApprovalRequest(mode=mode, tools=[{}], approved=True).model_dump()["mode"] == expected


@pytest.mark.parametrize("schema,payload", [
    (ChatRequest, {"message": "hello"}),
    (RunApprovalRequest, {"tools": [{}], "approved": False}),
])
def test_modes_default_to_standard_and_reject_unknown(schema, payload):
    assert schema(**payload).mode == "standard"
    with pytest.raises(ValidationError):
        schema(**payload, mode="turbo")
