"""Exercise the shell installer in temporary directories with a fake install.py."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest


REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def installer(tmp_path):
    source = tmp_path / "source with spaces"
    root = tmp_path / "private installation"
    models = tmp_path / "input models"
    commands = tmp_path / "commands"
    recorded = tmp_path / "installer-call.json"
    for directory in (source / "deploy/server", source / "tools", source / "models", models, commands):
        directory.mkdir(parents=True)

    def put(relative, content="source"):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    script = put("deploy/server/install.sh", (REPO / "deploy/server/install.sh").read_text().replace(
        "ROOT=/opt/auditbox-server", f"ROOT='{root}'"))
    for name in ("models.compose.yaml", "models.env.example"):
        shutil.copy2(REPO / "deploy/server" / name, source / "deploy/server" / name)
    shutil.copy2(REPO / "tools/check_models.py", source / "tools/check_models.py")
    put("deploy/server/install.py", """import json
from pathlib import Path
import sys
root = Path(sys.argv[sys.argv.index('--release') + 1]).parents[1]
root.joinpath('current').symlink_to(sys.argv[sys.argv.index('--release') + 1])
Path(%r).write_text(json.dumps(sys.argv))
""" % str(recorded))
    for relative in ("dist/index.html", "box/auditbox/gateway/app.py", "agent/src/auditbox_agent/main.py",
                     "box/auditbox/reference/data/catalog.json.gz", "agent/defaults/skills/sample/SKILL.md",
                     "scripts/quickstart.sh", "Makefile", "README.md", "LICENSE", "COPYING", "NOTICE", "box/.env.example",
                     "agent/.env.example", "web/.env.example", "web/.env.production"):
        put(relative)
    manifest = []
    for name, content in (("assistant.gguf", b"assistant model"), ("review.gguf", b"review model")):
        (models / name).write_bytes(content)
        manifest.append({"file": name, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
    put("models/manifest.json", json.dumps({"models": manifest}))
    for relative in ("box/.env", "agent/.env", "web/.env.local", "box/.venv/bin/python",
                     "web/node_modules/package/index.js", "box/data/state.json", "agent/data/state.json",
                     "box/__pycache__/cache.pyc", "box/.pytest_cache/state", "box/identity.pem",
                     "box/id_rsa", "agent/id_ed25519", "web/id_ecdsa", "tools/id_dsa", "box/.DS_Store"):
        put(relative, "synthetic private state")
    for name, content in {
        "id": "#!/bin/sh\necho 0\n",
        "uname": '#!/bin/sh\nif [ "$1" = -s ]; then echo Linux; else echo x86_64; fi\n',
        "uv": "#!/bin/sh\necho 'uv test-only'\n",
        "systemctl": "#!/bin/sh\necho 'must not call systemctl' >&2\nexit 97\n",
        "docker": "#!/bin/sh\necho 'must not call docker' >&2\nexit 98\n",
    }.items():
        path = commands / name
        path.write_text(content)
        path.chmod(0o755)
    (commands / "python3").symlink_to(sys.executable)
    env = {**os.environ, "PATH": str(commands) + os.pathsep + os.environ["PATH"],
           "PYTHONDONTWRITEBYTECODE": "1"}

    def run(*extra):
        return subprocess.run(["bash", str(script), "--address", "192.0.2.10", "--models", str(models),
                               "--release", "test-release", *extra], env=env, cwd=tmp_path,
                              text=True, capture_output=True)

    return SimpleNamespace(source=source, root=root, models=models, run=run, recorded=recorded,
                           commands=commands, script=script, env=env)


def test_server_script_prepares_source_models_and_calls_existing_installer(installer):
    (installer.source / "box").chmod(0o700)
    result = installer.run()
    assert result.returncode == 0, result.stderr
    release = installer.root / "releases/test-release"
    assert (installer.root / "current").resolve() == release
    assert release.stat().st_mode & 0o777 == 0o755
    assert (release / "box").stat().st_mode & 0o777 == 0o755
    for relative in ("dist/index.html", "Makefile", "LICENSE", "COPYING", "NOTICE", "scripts/quickstart.sh", "deploy/server/install.sh",
                     "box/auditbox/reference/data/catalog.json.gz", "agent/defaults/skills/sample/SKILL.md",
                     "box/.env.example", "agent/.env.example", "web/.env.example", "web/.env.production"):
        assert (release / relative).is_file(), relative
    for relative in ("box/.env", "agent/.env", "web/.env.local", "box/.venv", "web/node_modules",
                     "box/data", "agent/data", "box/__pycache__", "box/.pytest_cache", "box/identity.pem",
                     "box/id_rsa", "agent/id_ed25519", "web/id_ecdsa", "tools/id_dsa", "box/.DS_Store"):
        assert not (release / relative).exists(), relative
    assert (installer.root / "bin/uv").read_bytes() == (installer.commands / "uv").read_bytes()
    for name in ("assistant.gguf", "review.gguf"):
        assert (installer.root / "models" / name).read_bytes() == (installer.models / name).read_bytes()
    assert (installer.root / "models-runtime/.env").read_text() == (installer.source / "deploy/server/models.env.example").read_text()
    call = json.loads(installer.recorded.read_text())
    assert call[1:] == ["--release", str(release), "--address", "192.0.2.10"]
    assert "not started or enabled" in result.stdout


def test_server_script_preserves_existing_models_uv_and_runtime_configuration(installer):
    for name in ("models", "models-runtime", "bin"):
        (installer.root / name).mkdir(parents=True, exist_ok=True)
    for path in installer.models.iterdir():
        shutil.copy2(path, installer.root / "models" / path.name)
    uv = installer.root / "bin/uv"
    uv.write_text("#!/bin/sh\necho 'existing uv'\n")
    uv.chmod(0o755)
    compose = installer.root / "models-runtime/models.compose.yaml"
    compose.write_text("existing compose")
    env = installer.root / "models-runtime/.env"
    env.write_text("existing image configuration")
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (
        uv, compose, env, installer.root / "models/assistant.gguf", installer.root / "models/review.gguf")}
    result = installer.run()
    assert result.returncode == 0, result.stderr
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in before} == before


@pytest.mark.parametrize("failure", ["current", "dist", "model", "existing-model", "existing-release", "address", "release"])
def test_server_script_rejects_invalid_or_existing_installations(installer, failure):
    extra = []
    if failure == "current":
        installer.root.mkdir()
        (installer.root / "current").symlink_to(installer.root / "missing-release")
    elif failure == "dist":
        (installer.source / "dist/index.html").unlink()
    elif failure == "model":
        (installer.models / "assistant.gguf").write_bytes(b"corrupt")
    elif failure == "existing-model":
        (installer.root / "models").mkdir(parents=True)
        (installer.root / "models/assistant.gguf").write_bytes(b"existing incompatible model")
    elif failure == "existing-release":
        (installer.root / "releases/test-release").mkdir(parents=True)
    elif failure == "address":
        extra = ["--address", "hostname-is-not-an-ip"]
    else:
        extra = ["--release", "../outside"]
    result = installer.run(*extra)
    assert result.returncode != 0
    assert not installer.recorded.exists()
    assert not (installer.root / "bin/uv").exists()
    if failure == "existing-model":
        assert (installer.root / "models/assistant.gguf").read_bytes() == b"existing incompatible model"


def test_server_script_does_not_replace_non_executable_private_uv(installer):
    uv = installer.root / "bin/uv"
    uv.parent.mkdir(parents=True)
    uv.write_text("existing but not executable")
    result = installer.run("--uv", str(installer.commands / "uv"))
    assert result.returncode != 0
    assert uv.read_text() == "existing but not executable"
    assert not installer.recorded.exists()


def test_server_script_accepts_explicit_uv_path_with_spaces(installer):
    uv = installer.source.parent / "tools with spaces/uv"
    uv.parent.mkdir()
    shutil.copy2(installer.commands / "uv", uv)
    result = installer.run("--uv", str(uv))
    assert result.returncode == 0, result.stderr
    assert (installer.root / "bin/uv").read_bytes() == uv.read_bytes()


@pytest.mark.parametrize("relative", ["README.md", "box/auditbox/linked.py"])
def test_server_script_rejects_source_symlinks_without_leaving_staged_release(installer, relative):
    outside = installer.source.parent / "outside source.txt"
    outside.write_text("synthetic private file")
    linked = installer.source / relative
    linked.unlink(missing_ok=True)
    linked.symlink_to(outside)
    result = installer.run()
    assert result.returncode != 0
    assert "Source symlinks are not supported" in result.stderr
    assert not (installer.root / "releases/test-release").exists()
    assert not list((installer.root / "releases").glob('.install-*'))
    assert not installer.recorded.exists()


@pytest.mark.parametrize("name, kind", [(".env", "symlink"), ("models.compose.yaml", "symlink"), (".env", "directory")])
def test_server_script_rejects_unsafe_existing_runtime_configuration(installer, name, kind):
    target = installer.root / "models-runtime" / name
    target.parent.mkdir(parents=True)
    outside = installer.root.parent / "outside configuration"
    if kind == "symlink":
        target.symlink_to(outside)
    else:
        target.mkdir()
    result = installer.run()
    assert result.returncode != 0
    assert "Refusing a non-file or symlink runtime configuration" in result.stderr
    assert not outside.exists()
    assert not installer.recorded.exists()


def test_server_script_cleans_failed_model_copy_and_can_retry(installer):
    mocksite = installer.source.parent / "mock python"
    mocksite.mkdir()
    (mocksite / "sitecustomize.py").write_text("""from pathlib import Path
import shutil
original_copy = shutil.copy2
def fail_model_copy(source, target, *args, **kwargs):
    if Path(source).name == 'review.gguf':
        Path(target).write_bytes(b'partial copied model')
        raise OSError('simulated full disk')
    return original_copy(source, target, *args, **kwargs)
shutil.copy2 = fail_model_copy
""")
    installer.env["PYTHONPATH"] = str(mocksite)
    result = installer.run()
    assert result.returncode != 0
    assert "simulated full disk" in result.stderr
    assert not (installer.root / "models/review.gguf").exists()
    assert not list((installer.root / "models").glob('.review.gguf.*'))
    assert not installer.recorded.exists()
    del installer.env["PYTHONPATH"]
    retry = installer.run("--release", "retry-release")
    assert retry.returncode == 0, retry.stderr
    assert (installer.root / "models/review.gguf").read_bytes() == (installer.models / "review.gguf").read_bytes()
