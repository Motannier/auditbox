import gzip
import hashlib
import io
import json
import tarfile
from types import SimpleNamespace

import pytest

from auditbox import capability_packages as packages
from auditbox.security import switch_rules
from auditbox.recommendation import switch_catalog


def bundle(extra=None, bad_hash=False):
    strings = ["show version", "", "IOS", "user", "manual"]
    data = {
        "schema": 1,
        "profile": "cisco_ios",
        "strings": strings,
        "entries": [["version", 0, 0, 0, 1, 2, 3, 4]],
        "brand": "Cisco",
        "policy": "test fixture",
        "command_names": 1,
        "variants": 1,
        "risk": 0,
        "safe": 1,
    }
    packed = gzip.compress(json.dumps(data).encode())
    files = {"rules.json.gz": packed, "reference.json.gz": packed}
    manifest = {
        "schema": packages.SCHEMA,
        "engine": "auditbox-native/v1",
        "profile_id": "cisco_ios",
        "version": "1.0.0",
        "files": {
            name: {
                "variants": 1,
                "sha256": "bad" if bad_hash else hashlib.sha256(content).hexdigest(),
            }
            for name, content in files.items()
        },
    }
    files["manifest.json"] = json.dumps(manifest).encode()
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        if extra:
            archive.addfile(extra)
    return output.getvalue()


@pytest.fixture
def empty_runtime(tmp_path, monkeypatch):
    root = tmp_path / "runtime"
    for name in (
        "recommendation/dispatcher.py",
        "security/alert.py",
        "recommendation/switch_factory.py",
        "recommendation/switch_catalog.py",
        "security/switch_rules.py",
        "security/switch_history_review.py",
    ):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    monkeypatch.setattr(packages, "ROOT", root)
    monkeypatch.setattr(packages, "PACKAGE_DIR", tmp_path / "packages")
    monkeypatch.setattr(switch_rules, "DATA_DIR", root / "security/data")
    switch_rules._catalog.cache_clear()
    switch_catalog.catalog_for.cache_clear()
    yield
    switch_rules._catalog.cache_clear()
    switch_catalog.catalog_for.cache_clear()


def test_missing_data_install_activates_native_consumers(empty_runtime):
    before = next(
        p for p in packages.inventory()["profiles"] if p["profile_id"] == "cisco_ios"
    )
    assert not before["installed"] and before["engine_ready"]
    assert switch_rules._catalog("cisco_ios") is None
    result = packages.install(bundle())
    assert result["installed"] and not result["already_installed"]
    assert (
        switch_rules._catalog("cisco_ios") is not None
    )  # Cached failure must not survive install.
    assert len(switch_catalog.catalog_for("cisco_ios").variants) == 1
    assert next(
        p for p in packages.inventory()["profiles"] if p["profile_id"] == "cisco_ios"
    )["installed"]
    assert packages.install(bundle())["already_installed"]


@pytest.mark.parametrize(
    "name", ["../escape", "/tmp/escape", "module.py", "manifest.json"]
)
def test_reject_paths_scripts_duplicates(empty_runtime, name):
    with pytest.raises(ValueError):
        packages.install(bundle(tarfile.TarInfo(name)))
    assert not packages.PACKAGE_DIR.exists()


def test_reject_links_hash_mismatch_and_missing_adapter(empty_runtime):
    link = tarfile.TarInfo("module.py")
    link.type, link.linkname = tarfile.SYMTYPE, "/tmp/target"
    with pytest.raises(ValueError):
        packages.install(bundle(link))
    with pytest.raises(ValueError):
        packages.install(bundle(bad_hash=True))
    (packages.ROOT / "security/alert.py").unlink()
    with pytest.raises(ValueError, match="适配器"):
        packages.install(bundle())


def test_gateway_inventory_auth_identity_and_active_sessions(
    empty_runtime, tmp_path, monkeypatch
):
    from fastapi.testclient import TestClient
    from auditbox.gateway import app as gateway
    from auditbox.audit.store import AuditStore

    client = TestClient(gateway.app)
    assert client.get("/api/device-capabilities").status_code == 401
    assert (
        client.post(
            "/api/device-capabilities/install?expected_box_id=wrong", content=bundle()
        ).status_code
        == 401
    )
    store = AuditStore(tmp_path / "audit.db")
    monkeypatch.setattr(gateway, "_audit_store", lambda: store)
    monkeypatch.setattr(
        gateway,
        "require_super_admin",
        lambda request: SimpleNamespace(username="admin", login_id="test-login"),
    )
    response = client.get("/api/device-capabilities")
    assert response.json()["box_id"] == store.info()["box_id"]
    assert (
        client.post(
            "/api/device-capabilities/install?expected_box_id=wrong", content=bundle()
        ).status_code
        == 409
    )
    path = "/api/device-capabilities/install?expected_box_id=" + store.info()["box_id"]
    monkeypatch.setattr(gateway, "_active_terminal_sessions", {"terminal": object()})
    assert client.post(path, content=bundle()).status_code == 409
    monkeypatch.setattr(gateway, "_active_terminal_sessions", {})
    assert client.post(path, content=bundle()).status_code == 200
    assert store.records("behaviors")["items"][0]["action"] == "capability_installed"
    assert not client.post(path, content=b"broken").is_success
