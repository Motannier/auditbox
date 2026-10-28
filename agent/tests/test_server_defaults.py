"""Settings use the server stack without reading local configuration files."""

import os

import pytest

from auditbox_agent.config import Settings


@pytest.fixture(autouse=True)
def isolated_agent_environment(monkeypatch):
    for key in tuple(os.environ):
        if key.startswith("AUDITBOX_AGENT_"):
            monkeypatch.delenv(key)


def test_default_settings_use_local_server_services():
    settings = Settings(_env_file=None)
    assert settings.app_name == "AuditBox Agent"
    assert settings.host == "127.0.0.1"
    assert settings.port == 18765
    assert settings.gateway_internal_url == "http://127.0.0.1:18080"
    assert settings.standard_model == "minicpm5-2b"
    assert settings.model_backend == settings.resolved_model_backend == "openai"
    assert settings.model_base_url_for_mode("standard") == "http://127.0.0.1:18081/v1"
    assert settings.model_base_url_for_mode("enhanced") == "http://127.0.0.1:18081/v1"
    assert settings.model_api_key is None
    assert settings.execution_enabled is False


def test_environment_can_override_server_defaults(monkeypatch):
    values = {
        "APP_NAME": "Custom Agent", "PORT": "28765",
        "GATEWAY_INTERNAL_URL": "http://127.0.0.1:28080",
        "MODEL_BASE_URL": "http://127.0.0.1:11434", "MODEL_BACKEND": "ollama",
    }
    for key, value in values.items():
        monkeypatch.setenv("AUDITBOX_AGENT_" + key, value)
    settings = Settings(_env_file=None)
    assert settings.app_name == "Custom Agent"
    assert settings.port == 28765
    assert settings.gateway_internal_url == "http://127.0.0.1:28080"
    assert settings.model_base_url_for_mode("standard") == "http://127.0.0.1:11434"
    assert settings.model_backend == settings.resolved_model_backend == "ollama"
