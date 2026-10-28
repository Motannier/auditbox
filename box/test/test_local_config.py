"""Local setup creates private credentials once and keeps operator settings intact."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "init_local_config.py"
init_local_config = runpy.run_path(str(SCRIPT))["init_local_config"]
SECRET_FIELDS = ("AUDITBOX_ADMIN_PASSWORD", "AUDITBOX_SUPER_ADMIN_PASSWORD", "AUDITBOX_AUTH_SECRET")


@pytest.fixture
def root(tmp_path):
    directory = tmp_path / "repo"
    (directory / "box").mkdir(parents=True)
    shutil.copyfile(REPO / "box" / ".env.example", directory / "box" / ".env.example")
    return directory


def env_values(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines()
                if line and not line.startswith("#") and "=" in line)


def test_first_run_creates_private_independent_credentials_and_preserves_settings(root):
    template = root / "box" / ".env.example"
    original = template.read_bytes()
    result = init_local_config(root)
    config = root / "box" / ".env"
    values = env_values(config)
    assert result == {"path": str(config), "status": "created", "accounts": ["opsadmin", "superadmin"]}
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert len({values[key] for key in SECRET_FIELDS}) == 3
    assert all(re.fullmatch(r"[A-Za-z0-9_-]{32,}", values[key]) for key in SECRET_FIELDS[:2])
    assert re.fullmatch(r"[A-Za-z0-9_-]{64,}", values["AUDITBOX_AUTH_SECRET"])
    assert values["AUDITBOX_HISTORY_REVIEW_ENABLED"] == "false"
    assert {key: value for key, value in values.items()
            if key not in {*SECRET_FIELDS, "AUDITBOX_HISTORY_REVIEW_ENABLED"}} == {
        key: value for key, value in env_values(template).items()
        if key not in {*SECRET_FIELDS, "AUDITBOX_HISTORY_REVIEW_ENABLED"}
    }
    assert template.read_bytes() == original


def test_existing_config_is_preserved_without_reading_template_or_original_values(root):
    config = root / "box" / ".env"
    original = b"# operator-owned settings\nAUDITBOX_HISTORY_REVIEW_ENABLED=true\n"
    config.write_bytes(original)
    config.chmod(0o640)
    (root / "box" / ".env.example").unlink()
    assert init_local_config(root) == {"path": str(config), "status": "existing"}
    assert config.read_bytes() == original
    assert stat.S_IMODE(config.stat().st_mode) == 0o640


def test_missing_template_does_not_create_config(root):
    (root / "box" / ".env.example").unlink()
    with pytest.raises(FileNotFoundError):
        init_local_config(root)
    assert not (root / "box" / ".env").exists()


@pytest.mark.parametrize("missing", [*SECRET_FIELDS, "AUDITBOX_HISTORY_REVIEW_ENABLED",
                                     "AUDITBOX_ADMIN_USERNAME", "AUDITBOX_SUPER_ADMIN_USERNAME"])
def test_incomplete_template_does_not_create_config(root, missing):
    template = root / "box" / ".env.example"
    template.write_text("".join(line for line in template.read_text().splitlines(keepends=True)
                                if not line.startswith(missing + "=")))
    with pytest.raises(ValueError, match="Missing configuration template fields"):
        init_local_config(root)
    assert not (root / "box" / ".env").exists()


def test_duplicate_template_field_does_not_create_config(root):
    template = root / "box" / ".env.example"
    template.write_text(template.read_text() + "\nAUDITBOX_AUTH_SECRET=\n")
    with pytest.raises(ValueError, match="Duplicate configuration template field"):
        init_local_config(root)
    assert not (root / "box" / ".env").exists()


def test_empty_account_name_does_not_create_config(root):
    template = root / "box" / ".env.example"
    template.write_text(template.read_text().replace("AUDITBOX_ADMIN_USERNAME=opsadmin", "AUDITBOX_ADMIN_USERNAME="))
    with pytest.raises(ValueError, match="account names must not be empty"):
        init_local_config(root)
    assert not (root / "box" / ".env").exists()


def test_config_created_during_initialization_is_not_replaced(root, monkeypatch):
    original_open = os.open
    config = root / "box" / ".env"
    original = b"# written by another setup process\n"

    def racing_open(*args, **kwargs):
        config.write_bytes(original)
        return original_open(*args, **kwargs)

    monkeypatch.setattr(os, "open", racing_open)
    assert init_local_config(root) == {"path": str(config), "status": "existing"}
    assert config.read_bytes() == original


def test_failed_write_removes_partial_config(root, monkeypatch):
    original_fdopen = os.fdopen

    @contextmanager
    def failing_output(*args, **kwargs):
        with original_fdopen(*args, **kwargs) as output:
            def write(content):
                output.write(content[:10])
                output.flush()
                raise OSError("simulated write failure")
            yield SimpleNamespace(fileno=output.fileno, write=write)

    monkeypatch.setattr(os, "fdopen", failing_output)
    with pytest.raises(OSError, match="simulated write failure"):
        init_local_config(root)
    assert not (root / "box" / ".env").exists()


def test_cli_anchors_to_repository_and_never_prints_secrets(root, tmp_path):
    (root / "tools").mkdir()
    script = root / "tools" / SCRIPT.name
    shutil.copyfile(SCRIPT, script)
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    first = subprocess.run([sys.executable, str(script)], cwd=tmp_path, env=environment,
                           capture_output=True, text=True, check=True)
    config = root / "box" / ".env"
    values = env_values(config)
    assert json.loads(first.stdout) == {
        "path": str(config), "status": "created", "accounts": ["opsadmin", "superadmin"],
    }
    assert not any(values[key] in first.stdout + first.stderr for key in SECRET_FIELDS)
    original = config.read_bytes()
    second = subprocess.run([sys.executable, str(script)], cwd=tmp_path, env=environment,
                            capture_output=True, text=True, check=True)
    assert json.loads(second.stdout) == {"path": str(config), "status": "existing"}
    assert config.read_bytes() == original
    assert not any(values[key] in second.stdout + second.stderr for key in SECRET_FIELDS)
