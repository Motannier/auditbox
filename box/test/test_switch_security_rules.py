"""Reviewed switch policy and real confirmation gate; never contact a device."""

import gzip
import hashlib
import json

import pytest

from auditbox.security import alert, switch_rules
from auditbox.security.cli_syntax import matches
from auditbox.relay.command_gate import CommandGate
from auditbox.switch_commands import queries
from test_command_gate import FakeAudit


PROFILES = ("h3c_comware", "huawei_vrp", "cisco_ios", "zte_zxr10")
COUNTS = {
    "h3c_comware": (18028, 14625, 180),
    "huawei_vrp": (31736, 25716, 339),
    "cisco_ios": (2286, 1562, 288),
    "zte_zxr10": (12673, 10211, 452),
}
PORTS = {
    "h3c_comware": "GigabitEthernet1/0/1", "huawei_vrp": "GigabitEthernet0/0/1",
    "cisco_ios": "GigabitEthernet1/0/1", "zte_zxr10": "gei-1/1/1",
}


@pytest.mark.parametrize("profile", PROFILES)
def test_catalog_integrity_and_every_reviewed_risky_form_is_gated(profile):
    packed = (switch_rules.DATA_DIR / f"{profile}.json.gz").read_bytes()
    manifest = json.loads((switch_rules.DATA_DIR / "manifest.json").read_text())
    metadata = manifest["profiles"][profile]
    assert hashlib.sha256(packed).hexdigest() == metadata["sha256"]
    data = json.loads(gzip.decompress(packed))
    strings, entries = data["strings"], data["entries"]
    total, risk, pending = COUNTS[profile]
    assert len(entries) == len({r[0] for r in entries}) == total
    assert sum(r[3] > 0 for r in entries) == metadata["risk"] == risk
    assert sum(r[3] == 2 for r in entries) == metadata["pending"] == pending
    assert sum(r[3] == 0 for r in entries) == metadata["safe"] == total - risk
    for row in entries:
        assert strings[row[1]] and strings[row[2]] and strings[row[5]] and strings[row[7]]
        assert bool(strings[row[4]]) == bool(row[3])
        if row[3]:
            result = alert.check(strings[row[2]], device_profile=profile)
            assert result and result["action"] in {"confirm", "block"}, (profile, row[0], strings[row[2]])


@pytest.mark.parametrize("profile,commands", [
    ("h3c_comware", ["sysname edge", "vlan 100", "interface Vlan-interface100", "ip address 192.0.2.1 255.255.255.0", "undo shutdown", "save force", "reboot"]),
    ("huawei_vrp", ["sysname edge", "vlan 100", "interface Vlanif100", "ip route-static 0.0.0.0 0 192.0.2.1", "undo shutdown", "commit", "reset counters interface"]),
    ("cisco_ios", ["hostname edge", "vlan 100", "interface Vlan100", "switchport access vlan 100", "no shutdown", "copy running-config startup-config", "reload", "do clear counters"]),
    ("zte_zxr10", ["hostname edge", "vlan 100", "interface gei-1/1/1", "switchport access vlan 100", "no shutdown", "write", "reboot"]),
])
def test_configuration_persistence_and_runtime_changes_require_confirmation(profile, commands):
    for command in commands:
        result = alert.check(command, device_profile=profile)
        assert result and result["action"] == "confirm" and result["reason"], command


@pytest.mark.parametrize("profile,command", [
    ("h3c_comware", "acsei server"), ("huawei_vrp", "activate warranty"),
    ("cisco_ios", "abort"), ("zte_zxr10", "accept-rp monitoring"),
])
def test_pending_is_explicitly_confirmed(profile, command):
    result = alert.check(command, device_profile=profile)
    assert result["action"] == "confirm"
    assert "待核对" in result["reason"]


@pytest.mark.parametrize("profile,command", [
    ("h3c_comware", "undo accept-lifetime"), ("huawei_vrp", "undo a-msdu"),
    ("cisco_ios", "[no] aaa authentication login invalid-username-log"),
    ("zte_zxr10", "stub[no-summary]"),
])
def test_documented_risk_syntax_cannot_be_mistaken_for_unknown_due_to_heading(profile, command):
    assert alert.check(command, device_profile=profile)["action"] == "confirm"


@pytest.mark.parametrize("profile", PROFILES)
def test_unknown_commands_and_unmatched_query_arguments_pass(profile):
    for command in ("ls", "ls -la", "unknown-command", "sh ver", "dis ver"):
        assert alert.check(command, device_profile=profile) is None, command
    # The Cisco manual explicitly includes this abbreviated risk command.
    if profile == "cisco_ios":
        assert alert.check("shut", device_profile=profile)["action"] == "confirm"
    else:
        assert alert.check("shut", device_profile=profile) is None
    version = "display version" if profile in PROFILES[:2] else "show version"
    for suffix in (" junk", " clear"):
        assert alert.check(version + suffix, device_profile=profile) is None
    other_brand = "show version" if profile in PROFILES[:2] else "display version"
    assert alert.check(other_brand, device_profile=profile) is None


@pytest.mark.parametrize("profile", PROFILES)
def test_known_risk_families_active_tests_and_compound_syntax_still_require_confirmation(profile):
    for command in ("vlan", "ping 192.0.2.1", "debug all", "traceroute 192.0.2.1"):
        assert alert.check(command, device_profile=profile)["action"] == "confirm"
    version = "display version" if profile in PROFILES[:2] else "show version"
    for suffix in (" ; shutdown", "\nshutdown", " | redirect flash:/x", " > flash:/x", " | tee flash:/x", " && reboot", "\x00", " $(reboot)", " | include ok | delete file"):
        assert alert.check(version + suffix, device_profile=profile)["action"] == "confirm", suffix
    assert alert.check("ls; shutdown", device_profile=profile)["action"] == "confirm"
    assert alert.check("ls > flash:/x", device_profile=profile)["action"] == "confirm"


@pytest.mark.parametrize("profile", PROFILES)
def test_unknown_commands_reach_terminal_without_confirmation_and_remain_audited(profile):
    audit, target, client = FakeAudit(), bytearray(), bytearray()
    gate = CommandGate(audit, target.extend, client.extend, device_profile=profile)
    commands = ["ls", "ls -la", "unknown-command"]
    for command in commands:
        gate.feed((command + "\r").encode())
    assert bytes(target) == b"ls\rls -la\runknown-command\r"
    assert audit.commands == commands
    assert not audit.confirmations and not audit.blocked
    assert not client


@pytest.mark.parametrize("profile", PROFILES)
def test_fully_verified_queries_navigation_and_read_only_filters_pass(profile):
    forms = [q.command.format(interface=PORTS[profile], vlan="100") for q in queries(profile)]
    if profile == "h3c_comware":
        forms += ["display version", "display vlan", "display vlan 100", "display vlan brief",
                  "display interface brief", "display interface GigabitEthernet1/0/1", "display ip routing-table"]
    assert forms
    for command in forms:
        assert alert.check(command, device_profile=profile) is None, command
        assert alert.check(command + " | include up", device_profile=profile) is None, command
    for command in ("?", "help", "quit", "exit", "end", "return"):
        assert alert.check(command, device_profile=profile) is None
    enter = "system-view" if profile in PROFILES[:2] else "configure terminal"
    if profile in ("huawei_vrp", "zte_zxr10"):
        # Reviewed pending/risk forms take precedence over generic navigation.
        assert alert.check(enter, device_profile=profile)["action"] == "confirm"
    else:
        assert alert.check(enter, device_profile=profile) is None
    if profile == "cisco_ios":
        assert alert.check("do show version", device_profile=profile) is None
        assert alert.check("do show version | include NX-OS", device_profile=profile) is None


def test_optional_clear_monitor_and_file_export_branches_are_not_blanket_allowed():
    assert alert.check("show cos-queue gei-1/1/1", device_profile="zte_zxr10") is None
    result = alert.check("show cos-queue gei-1/1/1 clear", device_profile="zte_zxr10")
    assert result["action"] == "confirm" and "统计" in result["reason"]
    assert alert.check("show cos-queue gei-1/1/1 unknown", device_profile="zte_zxr10")
    for kind in ("process", "thread"):
        assert alert.check(f"monitor {kind}", device_profile="h3c_comware")["action"] == "confirm"
        assert alert.check(f"monitor {kind} dumbtty", device_profile="h3c_comware") is None
        assert alert.check(f"monitor {kind} dumbtty iteration 5 slot 1", device_profile="h3c_comware") is None
        assert alert.check(f"monitor {kind} dumbtty kill", device_profile="h3c_comware")
    for kind in ("dsa", "ecdsa", "rsa"):
        for encoding in ("openssh", "ssh2"):
            command = f"public-key local export {kind} {encoding}"
            assert alert.check(command, device_profile="h3c_comware") is None
            result = alert.check(command + " flash:/key.pub", device_profile="h3c_comware")
            assert result["action"] == "confirm" and "文件" in result["reason"]
    assert alert.check("public-key local export rsa ssh1", device_profile="h3c_comware") is None
    assert alert.check("public-key local export dsa ssh1", device_profile="h3c_comware")
    for profile in PROFILES[:2]:
        assert alert.check("display diagnostic-information", device_profile=profile)["action"] == "confirm"
        assert alert.check("display diagnostic-information diag.txt", device_profile=profile)["action"] == "confirm"
    result = alert.check("format bulkascii", device_profile="huawei_vrp")
    assert result["action"] == "confirm" and "不是存储格式化" in result["reason"]
    result = alert.check("show ip protocol routing", device_profile="zte_zxr10")
    assert result["action"] == "confirm" and "待核对" in result["reason"]
    assert "歧义" in result["reason"]
    result = alert.check("show ip protocol routing vrf sample", device_profile="zte_zxr10")
    assert result["action"] == "confirm" and "待核对" in result["reason"]
    result = alert.check("system-view", device_profile="huawei_vrp")
    assert result["action"] == "confirm" and "待核对" in result["reason"]


@pytest.mark.parametrize("profile", PROFILES)
def test_pending_takes_priority_over_curated_read_only_template(profile, monkeypatch):
    command = "display version" if profile in PROFILES[:2] else "show version"
    rule = switch_rules.Rule("pending", command, command, 2, "暂按有风险", "test", "test")
    monkeypatch.setattr(switch_rules, "_catalog", lambda _: {command: (rule,)})
    result = alert.check(command, device_profile=profile)
    assert result["action"] == "confirm" and "待核对" in result["reason"]


def test_pending_parent_is_not_hidden_by_more_specific_safe_family(monkeypatch):
    pending = switch_rules.Rule("pending", "show ip protocol routing", "show ip protocol routing [ vrf <vrf-name> ]", 2, "暂按有风险", "test", "test")
    safe = switch_rules.Rule("safe", "show ip protocol routing vrf", "show ip protocol routing vrf <vrf-name>", 0, "", "test", "test")
    monkeypatch.setattr(switch_rules, "_catalog", lambda _: {pending.title: (pending,), safe.title: (safe,)})
    result = alert.check("show ip protocol routing vrf sample", device_profile="zte_zxr10")
    assert result["action"] == "confirm" and "待核对" in result["reason"]


@pytest.mark.parametrize("broken", ["missing", "corrupt", "schema", "status"])
def test_missing_or_invalid_rule_data_falls_back_to_confirmation(tmp_path, monkeypatch, broken):
    if broken != "missing":
        manifest = json.loads((switch_rules.DATA_DIR / "manifest.json").read_text())
        packed = (switch_rules.DATA_DIR / "h3c_comware.json.gz").read_bytes()
        if broken == "corrupt":
            packed = b"corrupt compressed data"
        else:
            data = json.loads(gzip.decompress(packed))
            if broken == "schema":
                data["schema"] = 999
            else:
                data["entries"][0][3] = 999
            packed = gzip.compress(json.dumps(data).encode())
            manifest["profiles"]["h3c_comware"]["sha256"] = hashlib.sha256(packed).hexdigest()
        (tmp_path / "manifest.json").write_text(json.dumps(manifest))
        (tmp_path / "h3c_comware.json.gz").write_bytes(packed)
    monkeypatch.setattr(switch_rules, "DATA_DIR", tmp_path)
    switch_rules._catalog.cache_clear()
    try:
        result = alert.check("display version", device_profile="h3c_comware")
        assert result["action"] == "confirm" and "规则库未就绪" in result["reason"]
        assert alert.check("format flash:", device_profile="h3c_comware")["action"] == "block"
    finally:
        switch_rules._catalog.cache_clear()


@pytest.mark.parametrize("profile", PROFILES)
def test_one_time_confirmation_cancel_and_isolation_for_each_brand(profile):
    audit, other_audit, target = FakeAudit(), FakeAudit(), bytearray()
    gate = CommandGate(audit, target.extend, bytearray().extend, device_profile=profile)
    other = CommandGate(other_audit, bytearray().extend, bytearray().extend, device_profile=profile)
    pending = {"h3c_comware": "acsei server", "huawei_vrp": "activate warranty",
               "cisco_ios": "abort", "zte_zxr10": "accept-rp monitoring"}[profile]
    gate.feed((pending + "\r").encode())
    before = bytes(target)
    assert b"\r" not in before and not audit.commands
    token = audit.confirmations[-1][2]
    gate.feed(b"shutdown\r")
    assert bytes(target) == before
    assert not gate.confirm("wrong") and not other.confirm(token)
    assert gate.confirm(token)
    assert bytes(target) == before + b"\r"
    assert audit.commands == [pending]
    assert not gate.confirm(token)
    gate.feed(b"shutdown\r")
    next_token = audit.confirmations[-1][2]
    assert next_token != token and not gate.confirm(token)
    assert gate.cancel_confirmation(next_token) and not gate.confirm(next_token)
    assert audit.commands == [pending]
    assert audit.confirmation_results == [(pending, True), ("shutdown", False)]


@pytest.mark.parametrize("command", ["format flash:", "reset saved-configuration", "delete /unreserved flash:/x", "display version; format flash:", "save; delete /unreserved flash:/x"])
def test_existing_hard_blocks_cannot_be_downgraded_to_confirmation(command):
    assert alert.check(command, device_profile="h3c_comware")["action"] == "block"
    for profile in PROFILES:
        assert alert.check("write erase", device_profile=profile)["action"] == "block"


def test_switch_policy_never_runs_for_linux(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Linux must not enter the switch rule engine")
    monkeypatch.setattr(alert, "confirmation_reason", forbidden)
    for command in ("ls -la", "df -h", "rm demo.txt", "chmod 644 demo.txt", "save", "display version"):
        assert alert.check(command, device_profile="linux") is None
    assert alert.check("sudo reboot", device_profile="linux")["action"] == "confirm"
    assert alert.check("bash -c 'rm -rf /'", device_profile="linux")["action"] == "block"


def test_manual_matcher_requires_complete_typed_bounded_grammar():
    syntax = "display vlan [ vlan-id ] [ verbose | brief ]"
    for command in ("display vlan", "display vlan 100", "display vlan 100 brief", "display vlan verbose"):
        assert matches(syntax, command)
    for command in ("display vlan 0", "display vlan 4095", "display vlan vlan-id", "display vlan clear", "display vlan 100 brief clear", "display vlan 100 200"):
        assert not matches(syntax, command)
    syntax = "display interface interface-type interface-number"
    assert matches(syntax, "display interface GigabitEthernet1/0/1")
    assert matches(syntax, "display interface GigabitEthernet 1/0/1")
    assert not matches(syntax, "display interface file output.txt")
    assert matches("show route <ip-address>", "show route 192.0.2.1")
    assert not matches("show route <ip-address>", "show route garbage")
    assert matches("display vlan [ vlan-id1 [ to vlan-id2 ] ]", "display vlan 100 to 200")
    assert not matches("display vlan [ vlan-id1 [ to vlan-id2 ] ]", "display vlan 100 to 4095")
    assert matches("show test { alpha | beta }", "show test alpha")
    assert not matches("show test { alpha | beta }", "show test gamma")
    for malformed in ("show test [", "show test []", "show test [| clear]", "show test {a|}", "show test (stuff)", "show test ...", "show test " + "[" * 20 + "x" + "]" * 20):
        assert not matches(malformed, "show test")
    assert not matches("show test " + "x " * 4096, "show test")
