from collections import defaultdict

from auditbox.prediction.detector import detect
from auditbox.recommendation import (
    H3C_BEHAVIOR_GRAPH,
    H3C_BEHAVIOR_LABELS,
    H3C_COMMAND_COUNT,
    H3C_COMMAND_NODES,
    H3C_RECOMMENDATION_SYNTAX_ROOTS,
    advance_recommendation_session,
    classify_h3c_command,
    complete_h3c_command,
    create_recommendation_session,
    h3c_catalog_command_titles,
    recommend_next_commands,
    recommend_next_h3c_commands,
)
from auditbox.recommendation import h3c_factory
from auditbox.recommendation.h3c_catalog import (
    BEHAVIOR_COUNTS,
    BEHAVIOR_LABELS as CATALOG_BEHAVIOR_LABELS,
    COMMAND_CATALOG,
    COMMAND_MARKER_COUNT,
    CONTEXT_REQUIRED_MATCH_KEYS,
    DOCUMENT_COUNT,
    PAGE_COUNT,
)
from auditbox.recommendation.h3c_views import (
    COMMAND_VIEWS,
    COMMAND_VIEW_COUNT,
    SPECIALIZED_VIEW_MATCH_KEYS,
)
from auditbox.security.alert import check as alert_check


_SAMPLE_PARAMETERS = {
    "interface": "GigabitEthernet1/0/1",
    "vlan": "10",
    "target": "192.0.2.1",
    "rip_process": "1",
    "ospf_process": "1",
    "openflow_instance": "1",
    "acl_number": "3000",
    "pki_domain": "corp",
    "nqa_admin": "admin",
    "nqa_operation": "probe",
    "dhcp_pool": "branch",
    "ipv6_target": "2001:db8::1",
    "mac_address": "0011-2233-4455",
    "qos_policy": "corp",
    "qos_classifier": "voice",
    "qos_behavior": "voice",
    "radius_scheme": "corp",
    "ldap_scheme": "corp",
    "hwtacacs_scheme": "corp",
}


def test_pdf_catalog_is_complete_and_every_command_has_one_behavior() -> None:
    command_ids = [entry[0] for entry in COMMAND_CATALOG]
    behaviors = [entry[3] for entry in COMMAND_CATALOG]

    assert DOCUMENT_COUNT == 13
    assert PAGE_COUNT == 3208
    assert COMMAND_MARKER_COUNT == H3C_COMMAND_COUNT == 2815
    assert len(COMMAND_CATALOG) == len(command_ids) == len(set(command_ids))
    assert len(h3c_catalog_command_titles()) == H3C_COMMAND_COUNT
    assert set(behaviors) == set(CATALOG_BEHAVIOR_LABELS)
    assert set(CATALOG_BEHAVIOR_LABELS) < set(H3C_BEHAVIOR_LABELS)
    assert sum(BEHAVIOR_COUNTS.values()) == H3C_COMMAND_COUNT
    assert all(count > 0 for count in BEHAVIOR_COUNTS.values())
    assert COMMAND_VIEW_COUNT == len(COMMAND_VIEWS) == 2570
    assert COMMAND_VIEWS["vlan mapping"] == ("二层以太网接口视图\n二层聚合接口视图",)
    assert "vlan mapping" in SPECIALIZED_VIEW_MATCH_KEYS
    assert "vlan" not in SPECIALIZED_VIEW_MATCH_KEYS


def test_h3c_completion_uses_the_full_catalog_without_linux_fallback() -> None:
    display = complete_h3c_command("dis", limit=8)
    system = complete_h3c_command("sys", limit=8)
    vlan = complete_h3c_command("vl", limit=8)
    ospf = complete_h3c_command("osp", limit=8)
    lsr = complete_h3c_command("ls", limit=8)

    assert display
    assert all(item["command"].startswith("display ") for item in display)
    assert system[0]["command"] == "system-view"
    assert any(item["command"] == "vlan" for item in vlan)
    assert any(item["command"] == "ospf" for item in ospf)
    assert lsr and all(item["command"].startswith(("lsa", "lsdb", "lsr")) for item in lsr)
    assert all(item["command"] != "ls" for item in lsr)
    assert next(item for item in vlan if item["command"] == "vlan")["risk"] == "confirm"
    assert next(item for item in vlan if item["command"] == "vlan")["view"] == "系统视图"
    assert display[0]["risk"] == "normal"
    assert complete_h3c_command("vlan m", limit=8)[0]["view"] == "二层以太网接口视图、二层聚合接口视图"
    assert complete_h3c_command("sav", limit=8)[0]["risk"] == "confirm"
    assert complete_h3c_command("shu", limit=8)[0]["risk"] == "confirm"
    assert complete_h3c_command("systemctl", limit=8) == []


def test_complete_graph_contains_every_official_root_including_risky_actions() -> None:
    official_titles = {
        " ".join(entry[1].lower().split())
        for entry in COMMAND_CATALOG
    }
    official_roots = {
        " ".join(entry[2].lower().split())
        for entry in COMMAND_CATALOG
        if entry[2] and any(character.isalnum() for character in entry[2])
    }
    graph_targets = {
        edge.target
        for edges in H3C_BEHAVIOR_GRAPH.values()
        for edge in edges
    }
    graph_commands = {
        " ".join(H3C_COMMAND_NODES[target].lower().split())
        for target in graph_targets
        if "{" not in H3C_COMMAND_NODES[target]
    }

    assert len(H3C_BEHAVIOR_LABELS) >= 170
    assert set(H3C_BEHAVIOR_GRAPH) == set(H3C_BEHAVIOR_LABELS)
    assert graph_targets == set(H3C_COMMAND_NODES)
    assert len(official_roots) == 2569
    assert official_roots <= graph_commands
    assert {
        "reboot",
        "reset saved-configuration",
        "shutdown",
        "vlan mapping",
    } <= graph_commands
    assert any(not command.startswith("display ") for command in graph_commands)

    for edges in H3C_BEHAVIOR_GRAPH.values():
        assert len(edges) >= 5
        assert len({edge.target for edge in edges}) == len(edges)
        for edge in edges:
            template = H3C_COMMAND_NODES[edge.target]
            source_title = H3C_RECOMMENDATION_SYNTAX_ROOTS.get(edge.target, template)
            rendered = template.format(**_SAMPLE_PARAMETERS)

            normalized_source = " ".join(source_title.lower().split())
            assert normalized_source in official_titles | official_roots
            assert "{" not in rendered and "}" not in rendered


def test_every_catalog_row_is_linked_to_its_runtime_behavior() -> None:
    command_nodes = {
        " ".join(template.lower().split()): node
        for node, template in H3C_COMMAND_NODES.items()
        if "{" not in template
    }
    behavior_targets = {
        behavior: {edge.target for edge in edges}
        for behavior, edges in H3C_BEHAVIOR_GRAPH.items()
    }

    for _, _, match_key, behavior, _, section in COMMAND_CATALOG:
        normalized = " ".join(match_key.lower().split())
        if not normalized or not any(character.isalnum() for character in normalized):
            continue
        runtime_behavior = h3c_factory._specialize_behavior(
            h3c_factory._section_behavior(behavior, section),
            normalized,
        )
        assert command_nodes[normalized] in behavior_targets[runtime_behavior]


def test_terminal_recommendation_policy_is_separate_from_the_complete_graph() -> None:
    general_views = {"用户视图", "系统视图", "任意视图"}
    recognized = 0

    for command in {
        " ".join(entry[2].lower().split())
        for entry in COMMAND_CATALOG
        if entry[2] and any(character.isalnum() for character in entry[2])
    }:
        if classify_h3c_command(command) is None:
            continue
        recognized += 1
        suggestions = recommend_next_h3c_commands(command)

        assert len(suggestions) <= 5
        for suggestion in suggestions:
            recommended = suggestion["command"]
            alert = alert_check(recommended, device_profile="h3c_comware")
            assert not alert or alert["action"] == "confirm"
            assert suggestion["risk"] == ("confirm" if alert else "normal")
            if recommended.startswith("display "):
                continue
            assert recommended not in SPECIALIZED_VIEW_MATCH_KEYS
            assert any(
                view in general_views
                for view in COMMAND_VIEWS.get(recommended, ())
            )

    assert recognized >= 2200
    vlan_suggestions = {
        item["command"]
        for item in recommend_next_h3c_commands("display vlan")
    }
    assert not {
        "reboot",
        "reset saved-configuration",
        "shutdown",
        "vlan mapping",
    } & vlan_suggestions


def test_every_unambiguous_catalog_title_is_runtime_recognizable() -> None:
    behaviors_by_key: defaultdict[str, set[str]] = defaultdict(set)
    for _, _, match_key, behavior, _, _ in COMMAND_CATALOG:
        behaviors_by_key[match_key].add(behavior)

    missing = [
        match_key
        for match_key, behaviors in behaviors_by_key.items()
        if len(behaviors) == 1
        and match_key not in CONTEXT_REQUIRED_MATCH_KEYS
        and classify_h3c_command(match_key) is None
    ]
    assert missing == []


def test_parameters_are_carried_into_verification_commands() -> None:
    interface = recommend_next_h3c_commands("interface GigabitEthernet1/0/1")
    vlan = recommend_next_h3c_commands("vlan 10")
    target = recommend_next_h3c_commands("ping 192.0.2.1")
    ipv6_target = recommend_next_h3c_commands("ping ipv6 2001:db8::1")
    rip = recommend_next_h3c_commands("rip 7")
    ospf = recommend_next_h3c_commands("ospf 7")
    ospfv3 = recommend_next_h3c_commands("ospfv3 9")
    acl = recommend_next_h3c_commands("acl 3000")
    openflow = recommend_next_h3c_commands("openflow instance 2")
    mac = recommend_next_h3c_commands("mac-address 0011-2233-4455")
    dhcp = recommend_next_h3c_commands("dhcp server ip-pool branch")
    qos_policy = recommend_next_h3c_commands("qos policy corp")
    qos_classifier = recommend_next_h3c_commands("traffic classifier voice operator and")
    qos_behavior = recommend_next_h3c_commands("traffic behavior voice")
    radius = recommend_next_h3c_commands("radius scheme corp")
    ldap = recommend_next_h3c_commands("ldap scheme corp")
    hwtacacs = recommend_next_h3c_commands("hwtacacs scheme corp")
    nqa = recommend_next_h3c_commands("nqa entry admin probe")

    assert interface[0]["command"] == "display interface GigabitEthernet 1/0/1"
    assert interface[0]["behavior"] == "switching.interface"
    assert vlan[0]["command"] == "display vlan 10"
    assert vlan[0]["behavior"] == "switching.vlan"
    assert target[0]["command"] == "display ip routing-table 192.0.2.1"
    assert [item["command"] for item in ipv6_target[:3]] == [
        "display ipv6 routing-table 2001:db8::1",
        "display ipv6 routing-table",
        "display ipv6 routing-table summary",
    ]
    assert ipv6_target[-1]["command"] == "tracert ipv6"
    assert all(
        not item["command"].startswith(("display arp", "display ip "))
        for item in ipv6_target
    )
    assert rip[0]["command"] == "display rip 7 neighbor"
    assert ospf[0]["command"] == "display ospf 7 peer"
    assert ospfv3[0]["command"] == "display ospfv3 9 peer"
    assert acl[0]["command"] == "display acl 3000"
    assert openflow[0]["command"] == "display openflow instance 2 controller"
    assert mac[0]["command"] == "display mac-address 0011-2233-4455"
    assert dhcp[0]["command"] == "display dhcp server pool branch"
    assert qos_policy[0]["command"] == "display qos policy user-defined corp"
    assert qos_classifier[0]["command"] == "display traffic classifier user-defined voice"
    assert qos_behavior[0]["command"] == "display traffic behavior user-defined voice"
    assert radius[0]["command"] == "display radius scheme corp"
    assert ldap[0]["command"] == "display ldap scheme corp"
    assert hwtacacs[0]["command"] == "display hwtacacs scheme corp"
    assert [item["command"] for item in nqa[:2]] == [
        "display nqa result admin probe",
        "display nqa history admin probe",
    ]


def test_active_view_disambiguates_short_and_reused_commands() -> None:
    cases = (
        (
            "authentication-mode scheme",
            ("system-view", "user-interface vty 0 4"),
            "system.access",
        ),
        (
            "silent-interface GigabitEthernet1/0/1",
            ("system-view", "rip 1"),
            "routing.rip",
        ),
        (
            "if-match acl 3000",
            ("system-view", "route-policy IMPORT permit node 10"),
            "routing.base",
        ),
        (
            "control-vlan 4093",
            ("system-view", "erps ring 1"),
            "reliability.ring",
        ),
        (
            "protected-vlan reference-instance 1",
            ("system-view", "rrpp domain 1"),
            "reliability.ring",
        ),
        (
            "ipv6",
            ("system-view", "ldap scheme corp"),
            "security.authentication.ldap",
        ),
        (
            "version tls1.2",
            ("system-view", "ssl server-policy web"),
            "security.crypto.ssl",
        ),
        (
            "enable",
            ("system-view", "igmp-snooping"),
            "multicast.igmp",
        ),
    )

    for command, history, behavior in cases:
        context = classify_h3c_command(command, history=history)
        assert context is not None
        assert context.action == behavior

    assert classify_h3c_command(
        "description uplink",
        history=("system-view", "interface GigabitEthernet1/0/1", "quit"),
    ) is None


def test_incremental_view_stack_uses_post_command_state_and_keeps_parent_parameters() -> None:
    state = create_recommendation_session("h3c_comware")

    for command in (
        "system-view",
        "ospf 7",
        "area 0",
        "network 10.0.0.0 0.0.0.255",
    ):
        advance_recommendation_session(state, command)

    network = recommend_next_commands(
        "network 10.0.0.0 0.0.0.255",
        device_profile="h3c_comware",
        limit=5,
        session_state=state,
    )
    assert "display ospf 7 peer" in {item["command"] for item in network}

    advance_recommendation_session(state, "quit")
    after_quit = recommend_next_commands(
        "quit",
        device_profile="h3c_comware",
        limit=5,
        session_state=state,
    )
    assert after_quit[0]["command"] == "display ospf 7 peer"
    assert all(item["behavior"] == "routing.ospf" for item in after_quit)

    advance_recommendation_session(state, "return")
    assert recommend_next_commands(
        "return",
        device_profile="h3c_comware",
        limit=5,
        session_state=state,
    ) == []


def test_explicit_query_is_not_reclassified_or_parameterized_by_active_view() -> None:
    state = create_recommendation_session("h3c_comware")
    for command in ("system-view", "ospf 7", "display interface brief"):
        advance_recommendation_session(state, command)

    assert state.current_intent is not None
    assert state.current_intent.behavior == "switching.interface"
    assert "ospf_process" not in state.current_intent.parameters
    suggestions = recommend_next_commands(
        "display interface brief",
        device_profile="h3c_comware",
        limit=5,
        session_state=state,
    )
    assert suggestions
    assert all(item["behavior"] == "switching.interface" for item in suggestions)
    assert all("ospf 7" not in item["command"] for item in suggestions)


def test_view_local_ambiguity_and_configuration_neighbors_follow_current_view() -> None:
    state = create_recommendation_session("h3c_comware")
    for command in (
        "system-view",
        "interface GigabitEthernet1/0/1",
        "port access vlan 100",
    ):
        advance_recommendation_session(state, command)

    suggestions = recommend_next_commands(
        "port access vlan 100",
        device_profile="h3c_comware",
        limit=5,
        session_state=state,
    )
    commands = {item["command"] for item in suggestions}
    assert {"port hybrid vlan", "port trunk permit vlan"} <= commands
    assert "control-packet dot1p" not in commands

    context = classify_h3c_command(
        "description uplink",
        state=state.platform_state,
    )
    assert context is not None and context.action == "switching.interface"

    qos_state = create_recommendation_session("h3c_comware")
    for command in (
        "system-view",
        "qos policy corp",
        "classifier voice behavior voice",
    ):
        advance_recommendation_session(qos_state, command)
    assert qos_state.current_intent is not None
    assert qos_state.current_intent.behavior == "policy.qos"
    assert recommend_next_commands(
        "classifier voice behavior voice",
        device_profile="h3c_comware",
        limit=5,
        session_state=qos_state,
    )


def test_runtime_gap_strict_fallback_and_address_family_policy() -> None:
    patch_completion = complete_h3c_command("display patch")
    assert patch_completion[0]["command"] == "display patch-information"
    assert classify_h3c_command("display patch-information").action == "system.health"
    assert classify_h3c_command("disdsdididisaaaaaiay version") is None

    ipv4_commands = {
        item["command"]
        for item in recommend_next_h3c_commands("display ip routing-table")
    }
    assert ipv4_commands
    assert all("ipv6" not in command for command in ipv4_commands)


def test_blocked_command_does_not_mutate_the_h3c_view_stack() -> None:
    state = create_recommendation_session("h3c_comware")
    advance_recommendation_session(state, "system-view")
    advance_recommendation_session(
        state,
        "interface GigabitEthernet1/0/1",
        status="blocked",
    )

    assert [frame.entry_command for frame in state.platform_state.view_stack] == [
        "system-view",
    ]


def test_coarse_domains_are_refined_into_relevant_operational_meshes() -> None:
    expectations = {
        "display dhcp server statistics": ("ip.dhcp.server", "display dhcp server"),
        "display dhcp relay statistics": ("ip.dhcp.relay", "display dhcp relay"),
        "display dhcp snooping binding": ("ip.dhcp.snooping", "display dhcp snooping"),
        "display igmp-snooping": ("multicast.igmp", "display igmp-snooping"),
        "display vrrp": ("reliability.vrrp", "display"),
        "display snmp-agent statistics": ("observability.snmp", "display snmp-agent"),
        "display nqa result": ("observability.nqa", "display nqa"),
        "display radius statistics": ("security.authentication.aaa", "display radius"),
        "display ssh server status": ("security.crypto.ssh", "display ssh"),
    }

    for command, (behavior, expected_prefix) in expectations.items():
        context = classify_h3c_command(command)
        suggestions = recommend_next_h3c_commands(command)
        assert context is not None and context.action == behavior
        assert suggestions
        assert all(item["behavior"] == behavior for item in suggestions)
        assert suggestions[0]["command"].startswith(expected_prefix)


def test_representative_commands_follow_the_expected_behavior_mesh() -> None:
    expectations = {
        "display irf": "virtualization.irf",
        "display ospf peer": "routing.ospf",
        "display qos policy interface": "policy.qos",
        "display ipsec sa": "security.crypto.ipsec",
        "display bfd session": "reliability.bfd",
        "display grpc": "observability.telemetry",
        "display openflow flow-table": "sdn.openflow",
    }

    for command, behavior in expectations.items():
        context = classify_h3c_command(command)
        assert context is not None
        assert context.action == behavior
        suggestions = recommend_next_h3c_commands(command)
        assert suggestions
        assert all(item["behavior"] == behavior for item in suggestions)
        assert all(item["behavior_label"] == H3C_BEHAVIOR_LABELS[behavior] for item in suggestions)
        assert any(item["command"].startswith("display ") for item in suggestions)
        assert any(not item["command"].startswith("display ") for item in suggestions)
        for item in suggestions:
            alert = alert_check(item["command"], device_profile="h3c_comware")
            assert not alert or alert["action"] == "confirm"
            assert item["risk"] == ("confirm" if alert else "normal")
        assert len(suggestions) <= 5


def test_catalog_mesh_links_display_checks_to_configuration_roots() -> None:
    expectations = {
        "display alias": "alias",
        "display interface brief": "interface",
        "display vlan": "vlan",
        "display ospf peer": "ospf",
        "display grpc": "grpc enable",
        "display openflow flow-table": "openflow instance",
    }

    for command, expected in expectations.items():
        suggestions = recommend_next_h3c_commands(command)
        candidate = next(item for item in suggestions if item["command"] == expected)
        assert candidate["risk"] == "confirm"
        assert candidate["reason"] == "同一行为域的 Comware 官方命令"

    highest_priority_only = recommend_next_h3c_commands("display vlan", limit=1)
    assert highest_priority_only[0]["command"] == "display vlan brief"
    vlan_suggestions = recommend_next_h3c_commands("display vlan", limit=5)
    assert all(item["command"] != "vlan mapping" for item in vlan_suggestions)
    assert next(item for item in vlan_suggestions if item["command"] == "vlan")["view"] == "系统视图"


def test_h3c_and_other_vendor_namespaces_never_receive_linux_factory_commands() -> None:
    h3c = recommend_next_commands(
        "ping 192.0.2.1",
        device_type="ssh_h3c",
        limit=5,
    )

    assert h3c[0]["command"] == "display ip routing-table 192.0.2.1"
    assert all(not item["command"].startswith("ip route get") for item in h3c)
    assert recommend_next_commands(
        "ping 192.0.2.1",
        device_type="ssh_cisco_ios",
        limit=5,
    ) == []


def test_unknown_command_has_no_h3c_factory_recommendation() -> None:
    assert recommend_next_h3c_commands("definitely-not-a-comware-command") == []
    assert recommend_next_h3c_commands("enable") == []


def test_h3c_banner_detection_routes_to_the_h3c_namespace() -> None:
    banner = "H3C Comware Software, Version 7.1.070, Release 3506P10"

    assert detect(banner) == "h3c"
