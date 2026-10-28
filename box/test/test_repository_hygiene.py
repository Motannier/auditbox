"""Secrets belong to device configuration, not distributable source templates."""

import ast
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("relative, secret_keys", [
    ("box/.env.example", (
        "AUDITBOX_ADMIN_PASSWORD", "AUDITBOX_SUPER_ADMIN_PASSWORD",
        "AUDITBOX_AUTH_SECRET", "AUDITBOX_GUARDIAN_SECRET",
    )),
    ("agent/.env.example", (
        "AUDITBOX_AGENT_MODEL_API_KEY", "AUDITBOX_AGENT_EMBEDDING_API_KEY",
    )),
])
def test_templates_do_not_include_passwords_or_signing_keys(relative, secret_keys):
    values = {}
    for line in (REPO / relative).read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.strip()
    for key in secret_keys:
        assert values[key] == "", f"{relative}: {key} must be supplied by the operator"


def test_password_and_signing_defaults_are_empty():
    tree = ast.parse((REPO / "box/auditbox/env.py").read_text())
    defaults = {}
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Call):
            call = statement.value
            if isinstance(call.func, ast.Name) and call.func.id == "get_str" and len(call.args) == 2:
                for target in statement.targets:
                    if isinstance(target, ast.Name) and (
                        target.id.endswith("_PASSWORD") or target.id == "AUDITBOX_AUTH_SECRET"
                    ):
                        defaults[target.id] = ast.literal_eval(call.args[1])
    assert defaults == {
        "AUDITBOX_ADMIN_PASSWORD": "", "AUDITBOX_SUPER_ADMIN_PASSWORD": "",
        "AUDITBOX_AUTH_SECRET": "",
    }


def test_production_frontend_config_uses_only_public_same_origin_setting():
    settings = [line.strip() for line in (REPO / "web/.env.production").read_text().splitlines()
                if line.strip() and not line.lstrip().startswith("#")]
    assert settings == ["VITE_AUDITBOX_API_BASE_URL="]
