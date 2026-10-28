from auditbox.recommendation import (
    LINUX_BEHAVIOR_GRAPH,
    LINUX_BEHAVIOR_LABELS,
    LINUX_COMMAND_NODES,
    MAX_RECOMMENDATIONS,
    complete_linux_command,
    recommend_next_linux_commands,
)
from auditbox.recommendation import linux_factory
from auditbox.security.alert import check as alert_check


_SAMPLE_PARAMETERS = {
    "container": "web",
    "device": "/dev/sdb1",
    "domain": "vm1",
    "engine": "docker",
    "interface": "eth0",
    "package": "nginx",
    "path": "/tmp/example",
    "pid": "1234",
    "port": "443",
    "service": "nginx",
    "target": "192.0.2.1",
    "url": "https://example.com",
    "user": "alice",
}


def test_complete_linux_graph_contains_the_full_command_catalog() -> None:
    graph_targets = {
        edge.target
        for edges in LINUX_BEHAVIOR_GRAPH.values()
        for edge in edges
    }
    graph_commands = {
        " ".join(LINUX_COMMAND_NODES[target].split())
        for target in graph_targets
        if "{" not in LINUX_COMMAND_NODES[target]
    }
    catalog_commands = {
        " ".join(command.split())
        for command in linux_factory._COMPLETION_CATALOG
    }

    assert len(LINUX_BEHAVIOR_LABELS) >= 90
    assert len(LINUX_COMMAND_NODES) >= 390
    assert set(LINUX_BEHAVIOR_GRAPH) == set(LINUX_BEHAVIOR_LABELS)
    assert graph_targets == set(LINUX_COMMAND_NODES)
    assert len(catalog_commands) >= 310
    assert catalog_commands <= graph_commands
    assert {
        "kubectl delete",
        "reboot",
        "rm -rf",
        "shutdown -h now",
        "systemctl restart",
    } <= graph_commands

    for edges in LINUX_BEHAVIOR_GRAPH.values():
        assert len(edges) >= 5
        assert len({edge.target for edge in edges}) == len(edges)
        for edge in edges:
            rendered = LINUX_COMMAND_NODES[edge.target].format(**_SAMPLE_PARAMETERS)
            assert "{" not in rendered and "}" not in rendered


def test_every_linux_catalog_command_is_linked_to_its_classified_behavior() -> None:
    command_nodes = {
        " ".join(template.split()): node
        for node, template in LINUX_COMMAND_NODES.items()
        if "{" not in template
    }
    behavior_targets = {
        behavior: {edge.target for edge in edges}
        for behavior, edges in LINUX_BEHAVIOR_GRAPH.items()
    }

    for raw_command in linux_factory._COMPLETION_CATALOG:
        command = " ".join(raw_command.split())
        context = linux_factory._classify(command)
        assert context is not None
        assert command_nodes[command] in behavior_targets[context.action]


def test_linux_recommendation_policy_filters_the_complete_graph() -> None:
    dangerous_graph_commands = {
        "kubectl delete",
        "reboot",
        "rm -rf",
        "shutdown -h now",
        "systemctl restart",
    }

    for command in linux_factory._COMPLETION_CATALOG:
        suggestions = recommend_next_linux_commands(command)

        assert suggestions
        assert len(suggestions) <= MAX_RECOMMENDATIONS
        assert not dangerous_graph_commands & {
            suggestion["command"] for suggestion in suggestions
        }
        for suggestion in suggestions:
            assert suggestion["source"] == "factory"
            assert suggestion["risk"] == "normal"
            assert suggestion["behavior"] in LINUX_BEHAVIOR_LABELS
            assert alert_check(
                suggestion["command"],
                device_profile="linux",
            ) is None

    assert complete_linux_command("reb")[0]["risk"] == "confirm"
    kubectl_delete = next(
        item
        for item in complete_linux_command("kubectl d")
        if item["command"] == "kubectl delete "
    )
    assert kubectl_delete["risk"] == "confirm"


def test_representative_linux_domains_follow_their_behavior_meshes() -> None:
    expectations = {
        "cp source destination": ("file.copy", "stat -- destination"),
        "mkfs.ext4 /dev/sdb1": ("storage.filesystem", "findmnt "),
        "reboot": ("system.power", "uptime -s"),
        "systemctl restart nginx": ("service.change", "systemctl status nginx"),
        "dig example.com": ("network.dns", "getent ahosts example.com"),
        "curl https://example.com": ("network.http", "curl --fail"),
        "useradd alice": ("users.change", "getent passwd alice"),
        "apt install nginx": ("package.change", "apt-cache policy nginx"),
        "docker restart web": ("container.change", "docker ps -a"),
        "kubectl delete pod web": ("kubernetes.change", "kubectl get pods"),
        "virsh start vm1": ("virtualization.change", "virsh list --all"),
        "nginx -t": ("web.server", "nginx -T"),
        "mysqladmin ping": ("database.mysql", "mysqladmin processlist"),
    }

    for command, (behavior, first_command) in expectations.items():
        suggestions = recommend_next_linux_commands(command)
        assert suggestions
        assert all(item["behavior"] == behavior for item in suggestions)
        assert suggestions[0]["command"].startswith(first_command)


def test_package_recommendations_stay_with_the_selected_distribution_family() -> None:
    debian = {
        item["command"]
        for item in recommend_next_linux_commands("apt install nginx")
    }
    rpm = {
        item["command"]
        for item in recommend_next_linux_commands("dnf install nginx")
    }

    assert {"apt-cache policy nginx", "dpkg-query -s nginx"} <= debian
    assert all(not command.startswith(("dnf ", "rpm ")) for command in debian)
    assert {"dnf info nginx", "rpm -qi nginx"} <= rpm
    assert all(
        not command.startswith(("apt", "dpkg"))
        for command in rpm
    )


def test_cd_recommends_directory_inspection_first() -> None:
    suggestions = recommend_next_linux_commands("cd /var/log")

    assert len(suggestions) == MAX_RECOMMENDATIONS
    assert suggestions[0]["command"] == "ls -lah"
    assert [item["priority"] for item in suggestions] == sorted(
        [item["priority"] for item in suggestions], reverse=True
    )


def test_service_status_carries_service_parameter() -> None:
    suggestions = recommend_next_linux_commands("sudo systemctl status nginx")

    assert suggestions[0]["command"] == "journalctl -u nginx -n 100 --no-pager"
    assert all("{service}" not in item["command"] for item in suggestions)
    assert all(item["risk"] == "normal" for item in suggestions)


def test_ping_carries_target_to_route_and_resolver_checks() -> None:
    suggestions = recommend_next_linux_commands("ping -c 4 192.168.88.1")

    assert suggestions[0]["command"] == "ip route get 192.168.88.1"
    assert suggestions[1]["command"] == "getent ahosts 192.168.88.1"


def test_unknown_or_invalid_command_has_no_factory_recommendation() -> None:
    assert recommend_next_linux_commands("LS") == []
    assert recommend_next_linux_commands("hotstnamec\t-h") == []


def test_completion_is_factory_provided_and_case_sensitive() -> None:
    suggestions = complete_linux_command("l", limit=20)
    commands = [item["command"] for item in suggestions]

    assert "ls" in commands
    assert "ls -lah" in commands
    assert "LS" not in commands
    assert all(item["source"] == "catalog" for item in suggestions)
    assert complete_linux_command("L") == []


def test_completion_honors_limit() -> None:
    suggestions = complete_linux_command("s", limit=3)

    assert len(suggestions) == 3
