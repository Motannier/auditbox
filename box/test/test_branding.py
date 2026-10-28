"""Public branding stays configurable without exposing private settings."""

import importlib

import pytest
from fastapi.testclient import TestClient

from auditbox.branding import DEFAULT_PRODUCT_NAME, product_name
from auditbox.gateway import app as gateway
from auditbox.inference import prompts


@pytest.mark.parametrize("value", [None, "", "  ", "x" * 81, "name\x1b[0m", "name\nother", "name\x85other"])
def test_invalid_branding_uses_safe_default(monkeypatch, value):
    monkeypatch.delenv("AUDITBOX_PRODUCT_NAME", raising=False)
    if value is not None:
        monkeypatch.setenv("AUDITBOX_PRODUCT_NAME", value)
    assert product_name() == DEFAULT_PRODUCT_NAME


def test_public_config_is_one_field_and_not_cached(monkeypatch):
    monkeypatch.setenv("AUDITBOX_PRODUCT_NAME", "  客户定制调试工具  ")
    response = TestClient(gateway.app).get("/api/branding")
    assert response.status_code == 200
    assert response.json() == {"product_name": "客户定制调试工具"}
    assert response.headers["Cache-Control"] == "no-store"


def test_local_prompts_share_configured_name(monkeypatch):
    try:
        with monkeypatch.context() as config:
            config.setenv("AUDITBOX_PRODUCT_NAME", "定制工具")
            importlib.reload(prompts)
            assert "你是“定制工具”" in prompts.assistant_system_prompt("linux")
            assert "你是定制工具的实时安全审查器" in prompts.HISTORY_REVIEW_SYSTEM_PROMPT
            assert '{"risk":' in prompts.HISTORY_REVIEW_SYSTEM_PROMPT
    finally:
        importlib.reload(prompts)
