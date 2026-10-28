"""QuickStart builds locally and owns only its service process groups."""

import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest


REPO = Path(__file__).resolve().parents[2]
SERVICE_STUB = """import json, os, subprocess, sys, time
from pathlib import Path
root = Path(os.environ['QUICKSTART_TEST_ROOT'])
name = Path(sys.argv[0]).name.removeprefix('auditbox-')
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
(root / (name + '.pids.json')).write_text(json.dumps([os.getpid(), child.pid]))
while True:
    trigger = root / ('exit-' + name)
    if trigger.exists():
        os._exit(int(trigger.read_text()))
    time.sleep(0.02)
"""
COMMAND_STUB = """import json, os, shutil, sys
from pathlib import Path
root = Path(os.environ['QUICKSTART_TEST_ROOT'])
name = Path(sys.argv[0]).name
args = sys.argv[1:]
with (root / 'commands.jsonl').open('a') as output:
    output.write(json.dumps({'tool': name, 'args': args, 'cwd': os.getcwd()}) + '\\n')
stage = 'uv' if name == 'uv' else ('build' if args[-2:] == ['run', 'build'] else 'ci')
if name != 'python' and os.environ.get('QUICKSTART_FAIL_STAGE') == stage:
    sys.exit(23)
if name == 'npm' and stage == 'build':
    (root / 'dist').mkdir(exist_ok=True)
    (root / 'dist/index.html').write_text('<main>stub build</main>')
elif name == 'uv':
    binary = root / 'box/.venv/bin'
    binary.mkdir(parents=True, exist_ok=True)
    for service in ('proxy', 'gateway'):
        target = binary / ('auditbox-' + service)
        shutil.copyfile(root / 'service-stub', target)
        target.chmod(0o755)
elif name == 'python':
    os.execv(os.environ['QUICKSTART_REAL_PYTHON'], [os.environ['QUICKSTART_REAL_PYTHON'], *args])
"""


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    if sys.platform.startswith("linux"):
        try:
            return Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1][0] != "Z"
        except FileNotFoundError:
            return False
    return True


def wait_for(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.03)
    assert predicate(), "QuickStart did not reach the expected process state"


def service_pids(root):
    result = []
    for service in ("proxy", "gateway"):
        try:
            result.extend(json.loads((root / f"{service}.pids.json").read_text()))
        except (FileNotFoundError, json.JSONDecodeError):
            pass
    return result


def commands(root):
    path = root / "commands.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def assert_no_credentials_in_output(root, output):
    secret_fields = {"AUDITBOX_ADMIN_PASSWORD", "AUDITBOX_SUPER_ADMIN_PASSWORD", "AUDITBOX_AUTH_SECRET"}
    values = [line.partition("=")[2] for line in (root / "box/.env").read_text().splitlines()
              if line.partition("=")[0] in secret_fields]
    assert not any(value and value in output for value in values), "QuickStart printed a credential"


@pytest.fixture
def quickstart(tmp_path):
    root = tmp_path / "repo with spaces"
    for folder in ("scripts", "tools", "box", "web", "stub commands"):
        (root / folder).mkdir(parents=True)
    shutil.copyfile(REPO / "scripts/quickstart.sh", root / "scripts/quickstart.sh")
    shutil.copyfile(REPO / "tools/init_local_config.py", root / "tools/init_local_config.py")
    shutil.copyfile(REPO / "box/.env.example", root / "box/.env.example")
    (root / "box/pyproject.toml").write_text('[project]\nname="box"\nversion="0.0.0"\n')
    (root / "box/uv.lock").write_text("version = 1\n")
    (root / "web/package.json").write_text('{"name":"stub","version":"0.0.0"}')
    (root / "web/package-lock.json").write_text('{"lockfileVersion":3}')
    (root / "service-stub").write_text(f"#!{sys.executable}\n" + SERVICE_STUB)
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("AUDITBOX_", "QUICKSTART_"))}
    environment.update(QUICKSTART_TEST_ROOT=str(root), QUICKSTART_REAL_PYTHON=sys.executable,
                       PYTHONDONTWRITEBYTECODE="1", PYTHON_VERSION="3.10")
    for variable, name in (("UV", "uv"), ("NPM", "npm"), ("PYTHON", "python")):
        command = root / "stub commands" / name
        command.write_text(f"#!{sys.executable}\n" + COMMAND_STUB)
        command.chmod(0o755)
        environment[variable] = str(command)
    processes = []

    def launch(*args, overrides=None):
        process = subprocess.Popen(
            ["/bin/bash", str(root / "scripts/quickstart.sh"), *args],
            cwd=tmp_path, env={**environment, **(overrides or {})},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
        )
        processes.append(process)
        return process

    def run(*args, overrides=None):
        process = launch(*args, overrides=overrides)
        stdout, stderr = process.communicate(timeout=10)
        return SimpleNamespace(returncode=process.returncode, stdout=stdout, stderr=stderr)

    def unrelated():
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                                   start_new_session=True)
        processes.append(process)
        return process

    yield SimpleNamespace(root=root, run=run, launch=launch, unrelated=unrelated)
    for process in processes:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()
    for service in ("proxy", "gateway"):
        path = root / f"{service}.pids.json"
        if path.exists():
            try:
                os.killpg(json.loads(path.read_text())[0], signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_build_only_uses_locked_tools_from_any_working_directory(quickstart):
    result = quickstart.run("--build-only")
    assert result.returncode == 0
    calls = commands(quickstart.root)
    assert [call["tool"] for call in calls] == ["npm", "npm", "uv", "python"]
    assert calls[0]["args"] == ["ci"]
    assert calls[1]["args"] == ["run", "build"]
    assert calls[0]["cwd"] == calls[1]["cwd"] == str(quickstart.root / "web")
    assert calls[2]["args"] == ["sync", "--frozen", "--python", "3.10"]
    assert calls[2]["cwd"] == str(quickstart.root / "box")
    assert calls[3]["args"] == [str(quickstart.root / "tools/init_local_config.py")]
    config = quickstart.root / "box/.env"
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert "AUDITBOX_HISTORY_REVIEW_ENABLED=false" in config.read_text()
    assert not service_pids(quickstart.root)
    assert_no_credentials_in_output(quickstart.root, result.stdout + result.stderr)


def test_build_only_preserves_existing_configuration(quickstart):
    config = quickstart.root / "box/.env"
    original = b"# operator-owned\nAUDITBOX_HISTORY_REVIEW_ENABLED=true\n"
    config.write_bytes(original)
    config.chmod(0o640)
    assert quickstart.run("--build-only").returncode == 0
    assert config.read_bytes() == original
    assert stat.S_IMODE(config.stat().st_mode) == 0o640


@pytest.mark.parametrize("stage", ["ci", "build", "uv"])
def test_build_failure_never_starts_services_or_initializes_credentials(quickstart, stage):
    result = quickstart.run(overrides={"QUICKSTART_FAIL_STAGE": stage})
    assert result.returncode == 23
    assert not service_pids(quickstart.root)
    assert not (quickstart.root / "box/.env").exists()


@pytest.mark.parametrize("tool", ["NPM", "UV", "PYTHON"])
def test_missing_dependency_stops_before_building(quickstart, tool):
    result = quickstart.run(overrides={tool: str(quickstart.root / "missing command")})
    assert result.returncode == 1
    assert "Required command not found" in result.stderr
    assert not commands(quickstart.root)
    assert not service_pids(quickstart.root)


def test_start_only_requires_prepared_files_and_executable_services(quickstart):
    result = quickstart.run("--start-only")
    assert result.returncode == 1 and "--build-only" in result.stderr
    assert not commands(quickstart.root)
    assert quickstart.run("--build-only").returncode == 0
    (quickstart.root / "box/.venv/bin/auditbox-gateway").unlink()
    result = quickstart.run("--start-only")
    assert result.returncode == 1 and "Backend environment is missing" in result.stderr
    assert not service_pids(quickstart.root)


@pytest.mark.parametrize("shutdown,expected", [(signal.SIGTERM, 143), (signal.SIGINT, 130)])
def test_start_only_cleans_service_groups_and_preserves_unrelated_process(quickstart, shutdown, expected):
    assert quickstart.run("--build-only").returncode == 0
    before = commands(quickstart.root)
    unrelated = quickstart.unrelated()
    process = quickstart.launch("--start-only", overrides={
        key: str(quickstart.root / "missing command") for key in ("NPM", "UV", "PYTHON")
    })
    wait_for(lambda: len(service_pids(quickstart.root)) == 4)
    owned = service_pids(quickstart.root)
    assert all(alive(pid) for pid in owned)
    process.send_signal(shutdown)
    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == expected
    wait_for(lambda: not any(alive(pid) for pid in owned))
    assert commands(quickstart.root) == before
    assert unrelated.poll() is None
    assert_no_credentials_in_output(quickstart.root, stdout + stderr)


@pytest.mark.parametrize("failed_service", ["proxy", "gateway"])
def test_default_mode_preserves_component_exit_code_and_cleans_grandchildren(quickstart, failed_service):
    unrelated = quickstart.unrelated()
    process = quickstart.launch()
    wait_for(lambda: len(service_pids(quickstart.root)) == 4)
    owned = service_pids(quickstart.root)
    (quickstart.root / ("exit-" + failed_service)).write_text("7")
    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 7
    wait_for(lambda: not any(alive(pid) for pid in owned))
    assert [call["tool"] for call in commands(quickstart.root)] == ["npm", "npm", "uv", "python"]
    assert unrelated.poll() is None
    assert_no_credentials_in_output(quickstart.root, stdout + stderr)
