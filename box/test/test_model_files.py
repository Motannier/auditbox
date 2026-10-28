"""Model verification uses only local files and never starts a model runtime."""

import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "tools/check_models.py"
check_models = runpy.run_path(str(SCRIPT))["check_models"]


@pytest.fixture
def models(tmp_path):
    items = []
    for name, content in (("assistant.gguf", b"assistant"), ("review.gguf", b"review")):
        (tmp_path / name).write_bytes(content)
        items.append({"file": name, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"models": items}), encoding="utf-8")
    return tmp_path, manifest


def test_model_verifier_checks_every_file(models, capsys):
    directory, manifest = models
    check_models(directory, manifest)
    assert capsys.readouterr().out.splitlines() == ["OK assistant.gguf", "OK review.gguf"]


@pytest.mark.parametrize("content, message", [
    (None, "Missing model"),
    (b"short", "Unexpected size"),
    (b"WRONG!", "Unexpected SHA-256"),
])
def test_model_verifier_rejects_missing_or_changed_files(models, content, message):
    directory, manifest = models
    target = directory / "review.gguf"
    if content is None:
        target.unlink()
    else:
        target.write_bytes(content)
    with pytest.raises(ValueError, match=message):
        check_models(directory, manifest)


def test_model_verifier_cli_finds_manifest_from_another_directory(tmp_path):
    result = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path)], cwd=tmp_path,
                            text=True, capture_output=True)
    assert result.returncode == 1
    assert "Missing model: MiniCPM5-2B-Q4_K_M.gguf" in result.stderr
    assert "Traceback" not in result.stderr
