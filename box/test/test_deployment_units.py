"""在临时目录验证服务器安装产物，不触碰主机服务或网络。"""

import configparser
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO = Path(__file__).resolve().parents[2]


def load_script(relative):
    path = REPO / relative
    spec = importlib.util.spec_from_file_location(f"server_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def installation(tmp_path, monkeypatch):
    module = load_script("deploy/server/install.py")
    root = tmp_path / "opt/auditbox-server"
    release = root / "releases/test"
    for relative in ("box/auditbox/gateway/app.py", "agent/pyproject.toml", "dist/index.html"):
        path = release / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    (root / "bin").mkdir()
    (root / "bin/uv").touch()
    config, units = tmp_path / "config", tmp_path / "units"
    units.mkdir()
    calls = []
    original_write = module.write

    def write(path, content, mode=0o644):
        if path.is_relative_to("/etc/systemd/system"):
            path = units / path.name
        original_write(path, content, mode)

    monkeypatch.setattr(module, "ROOT", root)
    monkeypatch.setattr(module, "CONFIG", config)
    monkeypatch.setattr(module, "STATE", tmp_path / "state")
    monkeypatch.setattr(module, "LOGS", tmp_path / "logs")
    account = SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid())
    monkeypatch.setattr(module.pwd, "getpwnam", lambda _: account)
    monkeypatch.setattr(module.os, "getuid", lambda: 0)
    monkeypatch.setattr(module.os, "chown", lambda *args: None)
    monkeypatch.setattr(module, "run", lambda *args: calls.append(args))
    monkeypatch.setattr(module, "write", write)
    monkeypatch.setattr("sys.argv", ["install.py", "--release", str(release), "--address", "192.0.2.10"])
    module.main()
    values = dict(line.split("=", 1) for line in (config / "auditbox.env").read_text().splitlines())
    return module, root, release, config, units, calls, values


def test_server_installation_keeps_host_controls_disabled(installation):
    _, root, release, config, _, calls, values = installation
    assert root.joinpath("current").resolve() == release
    for key in ("GUARDIAN_REQUIRED", "BROWSER_CLOCK_SYNC_ENABLED", "NETWORK_SETTINGS_ENABLED", "AGENT_EXECUTION_ENABLED"):
        assert values[f"AUDITBOX_{key}"] == "false"
    assert values["AUDITBOX_TARGET_PREPARE_SOCKET"] == ""
    assert values["AUDITBOX_INFERENCE_PROFILES"] == "linux"
    assert values["AUDITBOX_GATEWAY_HOSTS"] == "127.0.0.1,192.0.2.10"
    assert values["AUDITBOX_AGENT_HOST"] == values["AUDITBOX_PROXY_HOST"] == "127.0.0.1"
    assert values["AUDITBOX_AGENT_MODEL_BASE_URL"] == "http://127.0.0.1:18081/v1"
    credentials = json.loads((config / "initial-credentials.json").read_text())
    assert credentials["opsadmin"] == values["AUDITBOX_ADMIN_PASSWORD"]
    assert len(values["AUDITBOX_AUTH_SECRET"]) >= 32
    assert (config / "initial-credentials.json").stat().st_mode & 0o777 == 0o600
    assert not any(command[:2] == ("systemctl", "start") for command in calls)


def test_application_units_are_unprivileged_and_start_in_order(installation):
    _, root, _, _, units, _, _ = installation
    for name in ("gateway", "proxy", "inference", "agent"):
        # systemd 允许重复的 Environment 指令。
        config = configparser.ConfigParser(interpolation=None, strict=False)
        config.read(units / f"auditbox-server-{name}.service")
        assert config["Service"]["User"] == "auditbox-server"
        assert config["Service"]["NoNewPrivileges"] == "true"
        assert config["Service"]["CapabilityBoundingSet"] == ""
        module = "agent" if name == "agent" else "box"
        assert config["Service"]["ExecStart"].startswith(str(root / "current" / module / ".venv/bin/python"))
        if name == "gateway":
            assert "auditbox-server-proxy.service" in config["Unit"]["After"]
        if name in ("agent", "inference"):
            assert "auditbox-server-models.service" in config["Unit"]["After"]
    models = (units / "auditbox-server-models.service").read_text()
    assert "After=docker.service auditbox-server-gateway.service" in models
    assert "start_models.py" in models


def test_upgrade_preserves_existing_credentials_and_configuration(installation):
    module, _, _, config, _, _, _ = installation
    before = {p.name: p.read_bytes() for p in config.iterdir()}
    module.main()
    assert {p.name: p.read_bytes() for p in config.iterdir()} == before


def test_failed_upgrade_does_not_change_previous_release_dependencies(installation, monkeypatch):
    module, root, previous, _, _, calls, _ = installation
    for name in ("box", "agent"):
        python = previous / name / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        python.write_text("previous environment")
    release = root / "releases/next"
    for relative in ("box/auditbox/gateway/app.py", "agent/pyproject.toml", "dist/index.html"):
        path = release / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    calls.clear()

    def run(*command):
        calls.append(command)
        if command[1:3] == ("pip", "install") and str(release / "agent/.venv/bin/python") in command:
            raise RuntimeError("simulated dependency failure")

    monkeypatch.setattr(module, "run", run)
    monkeypatch.setattr("sys.argv", ["install.py", "--release", str(release), "--address", "192.0.2.10"])
    with pytest.raises(RuntimeError, match="dependency failure"):
        module.main()
    assert (root / "current").resolve() == previous
    for name in ("box", "agent"):
        assert (previous / name / ".venv/bin/python").read_text() == "previous environment"
        assert ("python3", "-m", "venv", str(release / name / ".venv")) in calls
        export = next(command for command in calls if "export" in command and str(release / name) in command)
        assert export[-1] == str(release / name / ".venv/requirements.txt")
    assert not any(str(previous) in part for command in calls for part in command)
