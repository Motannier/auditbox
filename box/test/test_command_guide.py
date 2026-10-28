import copy
import hashlib
import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from auditbox import command_packages as engine
from auditbox.command_graph_packages import SCHEMA
from auditbox.reference.guide import command_guide, command_guide_counts, _project, _parameters, _operation
from auditbox.reference.guide_operations import READ_TASKS, CHANGE_TASKS
from auditbox.reference.guide_cli import compile_cli
from test_command_packages import packed, package_data


def guide_data(profile="example_olt", device_type="olt"):
    commands = []
    for syntax, risk in [
        ("show version", "read_only"),
        ("show memory", "read_only"),
        ("show interface port <port-id> onu <onu-id> information", "read_only"),
        ("show vlan <vlan-id>", "read_only"),
        ("show logging", "read_only"),
        ("clear logging", "ordinary_change"),
        ("reload", "dangerous_change"),
        ("show version", "ordinary_change"),  # Conflicting variants keep the higher risk.
        ("show pon { all | detail }", "pending"),
        ("show interface instance instance <id>", "read_only"),
        ("show lacp channel-group trunks Description None", "read_only"),
    ]:
        key = f"fact-{len(commands)}"
        commands.append(dict(
            id=key, family=" ".join(syntax.split()[:2]), syntax=syntax, summary=f"文档用途：{syntax}",
            risk=risk, reason="" if risk == "read_only" else "改变设备状态",
            template="" if risk == "pending" else syntax, behavior="documented",
            render_state="unsupported" if risk == "pending" else "needs_parameters" if "<" in syntax else "template",
            views=["privileged"], models=["documented-series"],
            evidence=[dict(source_id="manual", pages=[1], note="test fixture")],
        ))
    return dict(schema=SCHEMA, profile=dict(profile_id=profile, label="示例设备", device_type=device_type,
                category_label=device_type.upper(), vendor="Example", os_family="Example CLI", version="0.1.0"),
                commands=commands,
                coverage=dict(sources=[dict(id="manual", sha256="a" * 64, pages=10)], read_pages={"manual": [1]},
                              total_pages=10, runtime_validation="not_run", documentation_status="inherited_partial_review"),
                behaviors=dict(nodes=[dict(id="documented", label="文档用途", command_ids=[c["id"] for c in commands])], edges=[]))


@pytest.fixture
def packages(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "ROOT", tmp_path / "packages")
    engine._load.cache_clear()
    _project.cache_clear()
    yield
    engine._load.cache_clear()
    _project.cache_clear()


def scenarios(result):
    return [s for category in result["categories"] for s in category["scenarios"]]


def test_projection_common_operations_parameters_and_risk_do_not_change_graph(packages):
    data = guide_data()
    before = copy.deepcopy(data)
    engine.install(packed(data))
    result = command_guide("example_olt")
    assert result["fill_only"] and result["available"]
    assert {c["key"] for c in result["categories"]} == {"system", "access", "service", "fault", "maintenance"}
    items = scenarios(result)
    assert len(items) == 7
    assert next(s for s in items if s["template"] == "show version")["risk"] == "ordinary_change"
    onu = next(s for s in items if "onu" in s["template"])
    assert [p["slot"] for p in onu["parameters"]] == ["<port-id>", "<onu-id>"]
    assert all(s["views"] == ["privileged"] and s["models"] == ["documented-series"] for s in items)
    assert not any("instance instance" in s["template"] or "{ all" in s["template"] for s in items)
    assert engine.load("example_olt") == before
    assert engine.grade("clear logging", "example_olt")["action"] == "confirm"
    assert engine.grade("ls", "example_olt")["action"] == "allow"


def test_profile_isolation_and_documentation_only_availability(packages):
    engine.install(packed(guide_data()))
    other = guide_data("example_onu", "onu")
    other["commands"][0].update(template="display version", syntax="display version", family="display version")
    engine.install(packed(other))
    assert scenarios(command_guide("example_onu"))[0]["template"] == "display version"
    assert all(s["id"].startswith("guide-example_olt-") for s in scenarios(command_guide("example_olt")))
    unavailable = guide_data("unknown_onu", "onu")
    unavailable.update(commands=[], behaviors={"nodes": [], "edges": []},
                       availability={"cli": False, "reason": "当前文档只有 Web 操作，无 CLI 资料"})
    engine.install(packed(unavailable))
    result = command_guide("unknown_onu")
    assert not result["available"] and result["categories"] == []
    assert "无 CLI" in result["message"]
    with pytest.raises(ValueError):
        command_guide("linux")


def test_v1_is_supported_and_corruption_is_detected_even_with_warm_cache(packages):
    engine.install(packed(package_data()))
    assert scenarios(command_guide("test_cli"))[0]["template"] == "show alarm"
    meta = engine.metadata("test_cli")
    (engine.ROOT / "test_cli" / (meta["sha256"] + ".json")).write_text("{}")
    with pytest.raises(ValueError):
        command_guide("test_cli")


def test_projection_cache_tracks_revision(packages):
    engine.install(packed(guide_data()))
    before = command_guide("example_olt")
    count_before = command_guide_counts()["profiles"][0]
    updated = guide_data()
    updated["commands"][1]["summary"] = "新文档用途"
    updated["profile"]["version"] = "0.2.0"
    add_fact(updated, "show clock", "查看设备时间")
    raw = json.dumps(updated).encode()
    sha = hashlib.sha256(raw).hexdigest()
    folder = engine.ROOT / "example_olt"
    (folder / (sha + ".json")).write_bytes(raw)
    (folder / "current").write_text(json.dumps(dict(profile=updated["profile"], sha256=sha)))
    after = command_guide("example_olt")
    assert after["revision"] != before["revision"] and after["version"] == "0.2.0"
    assert any(s["purpose"] == "新文档用途" for s in scenarios(after))
    count_after = command_guide_counts()["profiles"][0]
    assert count_after["revision"] == after["revision"] != count_before["revision"]
    assert count_after["count"] == count_before["count"] + 1


def test_repeated_slots_are_separate_fields():
    params = _parameters("show interface <port-id> peer <port-id>")
    assert params[0]["key"] != params[1]["key"]
    assert params[0]["label"] != params[1]["label"]


def test_counts_index_matches_behaviors_not_forms_without_exposing_commands(packages):
    assert command_guide_counts()["profiles"] == []
    data = guide_data()
    add_fact(data, "show pon version", "查看 PON 版本")
    engine.install(packed(data))
    unavailable = guide_data("example_onu", "onu")
    unavailable.update(commands=[], behaviors={"nodes": [], "edges": []},
                       availability={"cli": False, "reason": "只有 Web 操作资料"})
    engine.install(packed(unavailable))
    counts = command_guide_counts()
    assert counts["schema"] == "auditbox-command-guide-counts/1"
    by_profile = {row["profile_id"]: row for row in counts["profiles"]}
    guide = command_guide("example_olt")
    assert by_profile["example_olt"] == {"profile_id": "example_olt", "count": len(scenarios(guide)),
                                         "revision": guide["revision"]}
    assert len(scenarios(guide)) < sum(len(item["variants"]) for item in scenarios(guide))
    assert by_profile["example_onu"]["count"] == 0
    assert "commands" not in json.dumps(counts) and "categories" not in json.dumps(counts)
    assert len(json.dumps(counts)) < 500
    assert engine.load("example_olt") == data


def test_counts_do_not_hide_corruption_or_break_other_brands(packages):
    engine.install(packed(guide_data()))
    engine.install(packed(guide_data("example_onu", "onu")))
    command_guide_counts()  # Warm projections must still check their integrity.
    meta = engine.metadata("example_olt")
    (engine.ROOT / "example_olt" / (meta["sha256"] + ".json")).write_text("{}")
    rows = {row["profile_id"]: row for row in command_guide_counts()["profiles"]}
    assert rows["example_olt"]["count"] is None and rows["example_olt"]["error"]
    assert rows["example_onu"]["count"] == len(scenarios(command_guide("example_onu")))


def add_fact(data, template, purpose="documented use", risk="read_only", views=None):
    row = copy.deepcopy(data["commands"][0])
    row.update(id=f"fact-{len(data['commands'])}", family=" ".join(template.split()[:2]),
               syntax=template, template=template, summary=purpose, risk=risk,
               reason="" if risk == "read_only" else "文档中注明改变状态",
               render_state="needs_parameters" if "<" in template else "template",
               views=views or ["documented view"])
    data["commands"].append(row)
    data["behaviors"]["nodes"][0]["command_ids"].append(row["id"])


def test_same_behavior_merges_forms_without_cutoff_or_losing_evidence(packages):
    data = guide_data()
    for name in ("all", "ip <ipv6>", "mac <mac>"):
        add_fact(data, "show dhcpv6 bind " + name, "查看 DHCPv6 绑定信息")
    # Different names for the same slot are not different command forms.
    for index in range(8):
        add_fact(data, f"show vlan <vlan{index}>", "查看 VLAN", views=[f"view-{index}"])
    for subject in ("qos cos-local-map", "qos cos-pon-map", "qos dscp-to-8021p", "qos eth-cos-local-map",
                    "qos tc-queue-map", "mls qos mapping cos", "mls qos mapping dscp",
                    "mls qos mapping local-priority", "gpon ont-pq-map-policy", "gpon ont-queue-map"):
        add_fact(data, "show " + subject, "查看优先级映射")
    engine.install(packed(data))
    items = scenarios(command_guide("example_olt"))
    bindings = next(item for item in items if item["behavior_key"] == "dhcpv6-bindings")
    assert len(bindings["variants"]) == 3
    vlans = next(item for item in items if item["behavior_key"] == "vlan-list")
    assert len(vlans["variants"]) == 1
    assert len(vlans["variants"][0]["views"]) == 9
    mappings = next(item for item in items if item["behavior_key"] == "qos-priority-map")
    assert len(mappings["variants"]) == 10  # No six-form cutoff.
    for variant in mappings["variants"] + vlans["variants"]:
        assert variant["evidence"] and variant["views"] and variant["models"]
        assert variant["fill_only"] and variant["command_ids"]
    assert len({s["id"] for s in items}) == len(items)
    assert len({s["title"] for s in items}) == len(items)
    assert engine.load("example_olt")["commands"] == data["commands"]


def test_different_purposes_and_opposites_are_separate_and_risk_is_per_form(packages):
    data = guide_data()
    for template, purpose, risk in [
        ("save", "保存配置", "ordinary_change"), ("commit", "提交模板", "ordinary_change"),
        ("shutdown", "关闭接口", "dangerous_change"), ("no shutdown", "启用接口", "ordinary_change"),
        ("show arp", "查看 ARP 地址表", "read_only"), ("show ip route mng", "查看管理接口路由", "read_only"),
        ("show mac", "查看 MAC 地址表", "read_only"), ("show mac statistic", "统计 MAC 数量", "read_only"),
        ("pon reset onu <onu-list>", "重启 ONU", "ordinary_change"),
        ("pon reregister onu <onu-list>", "重新注册 ONU", "ordinary_change"),
        ("show gpon dba", "查看 DBA 带宽分配", "read_only"),
        ("show pon config global", "查看 PON 配置", "read_only"),
        ("show onu-remote vlan aggregation-rule-gpon", "查看 VLAN 聚合", "read_only"),
        ("show onu-remote vlan translation-rule-gpon", "查看 VLAN 转换", "read_only"),
        ("show dhcp-option82 global", "查看 Option82 配置", "read_only"),
        ("show dhcp-option82 port <interface>", "查看端口 Option82 配置", "ordinary_change"),
    ]:
        add_fact(data, template, purpose, risk)
    engine.install(packed(data))
    items = {s["behavior_key"]: s for s in scenarios(command_guide("example_olt"))}
    for left, right in [("save", "commit"), ("shutdown", "no-shutdown"), ("arp-table", "route-table"),
                        ("mac-table", "mac-count"), ("onu-restart", "onu-reregister"), ("dba", "pon-config"),
                        ("onu-vlan-aggregation", "onu-vlan-translation")]:
        assert items[left]["id"] != items[right]["id"]
        assert items[left]["title"] != items[right]["title"]
    assert items["shutdown"]["risk"] == "dangerous_change"
    assert items["no-shutdown"]["risk"] == "ordinary_change"
    assert {v["risk"] for v in items["option82"]["variants"]} == {"read_only", "ordinary_change"}
    assert engine.grade("shutdown", "example_olt")["action"] == "confirm"
    assert engine.grade("ls", "example_olt")["action"] == "allow"


def test_purpose_is_not_replaced_by_a_brand_keyword():
    def op(template, purpose="documented use"):
        return _operation({"template": template, "summary": purpose})
    assert op("show igmp onu-config")[1] == "igmp-config"
    assert op("show vlanif brief")[1] == "vlan-list"
    assert op("show pon sn")[1] == "onu-sn"
    assert op("show edfa alarm-state")[1] == "edfa-alarm"
    assert op("show aaa configuration", "This command displays alarm configurations.") is None
    assert op("show aaa configuration", "查看 AAA 认证配置")[1] == "aaa"
    assert op("show logging configure", "To display logging information records")[1] == "log-records"
    assert op("show logging configure", "显示统计告警功能模块的配置信息")[1] == "alarm-config"
    assert op("exit", "退出登录的设备。")[1] == "logout"
    assert op("exit", "Exit the current view")[1] == "exit-view"
    assert op("show gpon unknown-thing") is None
    assert op("ont voip ftp-trigger portid ontid") is None
    assert op("version <version-num>", "version命令用来指定一个全局RIP版本")[1:] == ("rip-version", "设置 RIP 协议版本")
    assert op("version <version>", "To configure the MSTP version number")[1:] == ("mstp-version", "设置 MSTP 协议版本")


def test_operation_labels_and_keys_are_unique():
    rows = READ_TASKS + CHANGE_TASKS
    assert len({(g, key) for g, key, _, _ in rows}) == len(rows)
    assert len({title for _, _, title, _ in rows}) == len(rows)


def reference_data(data):
    data["reference"] = dict(schema="auditbox-command-reference/1", model_summary="Documented A / B series",
        manuals=[dict(source_id="manual", title="Original manual", models=["A series"], interface="CLI")],
        operations=[dict(key="pon-status", group="access", title="查看 PON 状态", command_ids=["fact-8"])], procedures=[])
    return data


def test_documented_raw_syntax_is_fillable_without_changing_gate(packages):
    data = reference_data(guide_data())
    original = copy.deepcopy(data)
    engine.install(packed(data))
    guide = command_guide("example_olt")
    entry = next(s for s in scenarios(guide) if s["behavior_key"] == "pon-status")
    assert not entry.get("documentation_only") and entry["template"] == "show pon <parameter0>"
    assert entry["parameters"][0]["validation"] == "enum"
    assert [option["value"] for option in entry["parameters"][0]["options"]] == ["all", "detail"]
    assert entry["risk"] == "pending"
    assert guide["model_summary"] == "Documented A / B series"
    assert engine.load("example_olt") == original
    assert engine.grade("show pon all", "example_olt")["action"] == "confirm"


def test_web_steps_remain_in_pack_but_are_not_extra_quick_guide_features(packages):
    data = reference_data(guide_data("example_onu", "onu"))
    data.update(commands=[], behaviors={"nodes": [], "edges": []},
                availability={"cli": False, "reason": "Web only; not a console protocol"})
    data["reference"]["operations"] = []
    procedure = dict(id="web-a", key="web-pon-state", group="access", title="查看 PON 信息",
                     summary="Web 手册所述查看页面", steps=["进入 PON 页面"],
                     evidence=[dict(source_id="manual", pages=[2], note="PON 页")],
                     models=["A"], interface="Web", risk="read_only", reason="")
    data["reference"]["procedures"] = [procedure, dict(procedure, id="web-b", models=["B"])]
    engine.install(packed(data))
    guide = command_guide("example_onu")
    entries = scenarios(guide)
    assert not guide["available"] and entries == []
    assert engine.load("example_onu")["reference"]["procedures"] == data["reference"]["procedures"]
    assert engine.load("example_onu")["commands"] == []


def test_reference_cannot_override_templates_risk_or_omit_sources(packages):
    data = reference_data(guide_data())
    for mutate in (
        lambda d: d["reference"].update(template="reload"),
        lambda d: d["reference"]["operations"][0].update(risk="read_only"),
        lambda d: d["reference"]["operations"][0].update(command_ids=["missing"]),
        lambda d: d["reference"].update(manuals=[]),
        lambda d: d["reference"]["manuals"][0].update(source_id="other"),
    ):
        modified = copy.deepcopy(data)
        mutate(modified)
        with pytest.raises(ValueError):
            engine.install(packed(modified))


def cli_row(syntax, **extra):
    return dict(syntax=syntax, template="", render_state="unsupported", family=syntax.split()[0], **extra)


def test_cli_grammar_omits_optional_query_filters_but_not_required_targets():
    result = compile_cli(cli_row("show cpu-utilization [ dynamic | slot slot-id ]"))
    assert result == {"template": "show cpu-utilization", "parameters": []}
    result = compile_cli(cli_row("display cpu frameid/slotid"))
    assert result["template"] == "display cpu <parameter0>"
    assert result["parameters"][0]["formats"] == [{"prefix": "", "separators": ["/"]}]
    result = compile_cli(cli_row("show interface port <port-id> onu <onu-id> information"))
    assert result["template"] == "show interface port <parameter0> onu <parameter1> information"
    assert [p["label"] for p in result["parameters"]] == ["端口编号", "ONU 编号"]


def test_cli_required_choices_are_explicit_not_the_first_or_a_sample():
    result = compile_cli(cli_row("loop-detect {enable | disable}"))
    assert result["template"] == "loop-detect <parameter0>"
    assert [v["value"] for v in result["parameters"][0]["options"]] == ["enable", "disable"]
    result = compile_cli(cli_row("display ont info { ont-id | all }"))
    assert result["parameters"][0]["validation"] == "integer"
    assert result["parameters"][0]["literals"] == ["all"]
    result = compile_cli(cli_row("show onu detail-info {gpon-onu_shelf/slot/olt:onu | epon-onu_shelf/slot/olt:onu}"))
    assert result["template"] == "show onu detail-info <parameter0>"
    assert len(result["parameters"][0]["formats"]) == 2
    result = compile_cli(cli_row("set vlan <1-4094>"))
    assert result["parameters"][0]["minimum"] == 1 and result["parameters"][0]["maximum"] == 4094


def test_cli_source_parameter_names_and_keyword_value_pairs():
    result = compile_cli(cli_row("set-cputhreshold rackno rackno shelfno shelfno slotno slotno"),
                         dict(kind="command_reference", parameters=["rackno", "shelfno", "slotno"]))
    assert result["template"] == "set-cputhreshold rackno <parameter0> shelfno <parameter1> slotno <parameter2>"
    result = compile_cli(cli_row("switchport down-hold-time time"), dict(kind="command_reference", parameters=["time"]))
    assert result["template"] == "switchport down-hold-time <parameter0>"
    result = compile_cli(cli_row("radius-server key password"), dict(kind="command_reference", parameters=["password"]))
    assert result["parameters"][0]["secret"]


def test_compiled_change_arguments_keep_confirmation_and_unknown_policy(packages):
    data = reference_data(guide_data())
    add_fact(data, "board power-on frameid/slotid", "恢复指定机框槽位供电", "dangerous_change")
    fact = data["commands"][-1]
    fact.update(render_state="unsupported", template="", family="board power-on")
    data["reference"]["cli_syntax"] = [dict(command_id=fact["id"], kind="command_reference", parameters=[])]
    engine.install(packed(data))
    assert engine.grade("board power-on 0/1", "example_olt")["action"] == "confirm"
    assert engine.grade("ls", "example_olt")["action"] == "allow"
    assert engine.grade("board power-on not-a-position", "example_olt")["action"] == "confirm"
    assert engine.load("example_olt")["commands"] == data["commands"]


def test_cli_repeated_angle_parameters_keep_separate_slots():
    result = compile_cli(cli_row("set threshold <value> <value>"))
    assert result["template"] == "set threshold <parameter0> <parameter1>"
    assert [p["label"] for p in result["parameters"]] == ["value（第 1 项）", "value（第 2 项）"]


@pytest.mark.parametrize("syntax", ["show vlan 100", "show version;reload", "show interface port <port-id>\nshutdown",
    "show interface instance instance <id>", "show version Command Line Reference", "show x {a <id> | b <name>}",
    "show bad [unterminated", "show vlan 192.168.1.1"])
def test_cli_cannot_compile_examples_prose_malformed_grammar_or_injection(syntax):
    assert compile_cli(cli_row(syntax)) is None


def test_source_declarations_cannot_supply_commands_regex_or_risk(packages):
    data = reference_data(guide_data())
    data["reference"]["cli_syntax"] = [dict(command_id="fact-8", kind="command_reference", parameters=[])]
    engine.install(packed(data))
    assert scenarios(command_guide("example_olt"))[-1]["template"]
    for value in [dict(command_id="missing", kind="command_reference", parameters=[]),
                  dict(command_id="fact-8", kind="command_reference", parameters=["x;reload"]),
                  dict(command_id="fact-8", kind="command_reference", parameters=[], template="reload"),
                  dict(command_id="fact-8", kind="command_reference", parameters=[], risk="read_only")]:
        bad = copy.deepcopy(data)
        bad["reference"]["cli_syntax"] = [value]
        with pytest.raises(ValueError): engine.install(packed(bad))


def test_merged_brand_counts_deduplicate_behaviors_not_execution_profiles(packages):
    for profile, device in (("huawei_olt", "olt"), ("huawei_onu", "onu")):
        engine.install(packed(reference_data(guide_data(profile, device))))
    counts = command_guide_counts()
    brand = next(b for b in counts["brands"] if b["id"] == "pon-huawei")
    assert brand["profile_ids"] == ["huawei_olt", "huawei_onu"]
    assert brand["count"] == len(scenarios(command_guide("huawei_olt")))
    assert brand["model_summary"] == "Documented A / B series"


def test_gateway_requires_login_and_reports_unavailable_data(packages, monkeypatch):
    from auditbox.gateway import app as gateway
    request = Request({"type": "http", "headers": []})
    with pytest.raises(HTTPException) as error:
        gateway.device_command_guide(request, "example_olt")
    assert error.value.status_code == 401
    with pytest.raises(HTTPException) as error:
        gateway.device_command_guide_counts(request)
    assert error.value.status_code == 401
    monkeypatch.setattr(gateway, "require_authenticated", lambda request: object())
    engine.install(packed(guide_data()))
    assert gateway.device_command_guide(request, "example_olt")["profile_id"] == "example_olt"
    assert gateway.device_command_guide_counts(request) == command_guide_counts()
    with pytest.raises(HTTPException) as error:
        gateway.device_command_guide(request, "missing")
    assert error.value.status_code == 503
    monkeypatch.setattr(engine, "profiles", lambda: (_ for _ in ()).throw(OSError("test unavailable")))
    with pytest.raises(HTTPException) as error:
        gateway.device_command_guide_counts(request)
    assert error.value.status_code == 503
