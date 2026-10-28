"""Server defaults stay local even without an operator configuration file."""

import json
import os
from pathlib import Path
import subprocess
import sys


DEFAULTS = {
    "AUDITBOX_PORT": 19000,
    "AUDITBOX_PROXY_HOST": "127.0.0.1",
    "AUDITBOX_PROXY_PORT": 19000,
    "AUDITBOX_PREDICT_PORT": 19001,
    "AUDITBOX_GATEWAY_HOST": "127.0.0.1",
    "AUDITBOX_GATEWAY_HOSTS": ["127.0.0.1"],
    "AUDITBOX_GATEWAY_PORT": 18080,
    "AUDITBOX_GATEWAY_CORS_ORIGINS": ["http://127.0.0.1:18080", "http://127.0.0.1:5173"],
    "AUDITBOX_PUBLIC_URL": "http://127.0.0.1:18080",
    "AUDITBOX_BROWSER_CLOCK_SYNC_ENABLED": False,
    "AUDITBOX_NETWORK_SETTINGS_ENABLED": False,
    "AUDITBOX_GUARDIAN_REQUIRED": False,
    "AUDITBOX_TARGET_PREPARE_SOCKET": "",
    "AUDITBOX_ADMIN_PASSWORD": "",
    "AUDITBOX_SUPER_ADMIN_PASSWORD": "",
    "AUDITBOX_AUTH_SECRET": "",
    "AUDITBOX_INFERENCE_HOST": "127.0.0.1",
    "AUDITBOX_INFERENCE_PORT": 18091,
    "AUDITBOX_INFERENCE_PROFILES": ["linux"],
    "AUDITBOX_ASSISTANT_BACKEND_URL": "http://127.0.0.1:18081",
    "AUDITBOX_REVIEW_BACKEND_URL": "http://127.0.0.1:18082",
    "AUDITBOX_LLM_URL": "http://127.0.0.1:18091/assistant",
    "AUDITBOX_HISTORY_REVIEW_LLM_URL": "http://127.0.0.1:18091/review",
    "AUDITBOX_ASSISTANT_LLM_MODEL": "minicpm5-2b",
    "AUDITBOX_LINUX_REVIEW_LLM_MODEL": "command-guard",
    "AUDITBOX_AGENT_URL": "http://127.0.0.1:18765",
}


def read_configuration(tmp_path, overrides=None):
    environment = {key: value for key, value in os.environ.items() if not key.startswith("AUDITBOX_")}
    environment.update(overrides or {})
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-c", """
import json
import sys
from unittest.mock import patch

# Ignore all developer .env files while checking missing configuration.
with patch("dotenv.load_dotenv", return_value=False):
    from auditbox import env
print(json.dumps({name: getattr(env, name) for name in json.loads(sys.argv[1])}))
""", json.dumps(list(DEFAULTS))],
        cwd=tmp_path, env=environment, capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def test_unconfigured_server_uses_local_endpoints_and_disables_host_changes(tmp_path):
    assert read_configuration(tmp_path) == DEFAULTS


def test_operator_configuration_can_override_defaults_and_enable_optional_features(tmp_path):
    values = read_configuration(tmp_path, {
        "AUDITBOX_PORT": "29000",
        "AUDITBOX_GATEWAY_HOST": "192.0.2.10",
        "AUDITBOX_GATEWAY_PORT": "28080",
        "AUDITBOX_PUBLIC_URL": "http://192.0.2.10:28080",
        "AUDITBOX_GATEWAY_CORS_ORIGINS": "http://192.0.2.10:28080",
        "AUDITBOX_BROWSER_CLOCK_SYNC_ENABLED": "true",
        "AUDITBOX_NETWORK_SETTINGS_ENABLED": "true",
        "AUDITBOX_INFERENCE_PROFILES": "linux,h3c_comware",
        "AUDITBOX_AGENT_URL": "http://127.0.0.1:28765",
    })
    assert values == {**DEFAULTS,
        "AUDITBOX_PORT": 29000, "AUDITBOX_PROXY_PORT": 29000,
        "AUDITBOX_GATEWAY_HOST": "192.0.2.10", "AUDITBOX_GATEWAY_HOSTS": ["192.0.2.10"],
        "AUDITBOX_GATEWAY_PORT": 28080,
        "AUDITBOX_PUBLIC_URL": "http://192.0.2.10:28080",
        "AUDITBOX_GATEWAY_CORS_ORIGINS": ["http://192.0.2.10:28080"],
        "AUDITBOX_BROWSER_CLOCK_SYNC_ENABLED": True,
        "AUDITBOX_NETWORK_SETTINGS_ENABLED": True,
        "AUDITBOX_INFERENCE_PROFILES": ["linux", "h3c_comware"],
        "AUDITBOX_AGENT_URL": "http://127.0.0.1:28765",
    }
