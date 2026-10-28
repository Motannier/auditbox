from auditbox.security.alert import ACTION_BLOCK, ACTION_CONFIRM, check


def assert_action(command: str, action: str, *, device_profile: str = "linux") -> None:
    alert = check(command, device_profile=device_profile)
    assert alert is not None, command
    assert alert["action"] == action, command


def test_operationally_risky_commands_require_confirmation() -> None:
    for command in (
        "sudo reboot",
        "sudo shutdown -h now",
        "sudo systemctl restart nginx",
        "sudo systemctl stop nginx",
        "sudo kubectl delete deployment web",
        "sudo kill -9 123",
        "sudo rm -rf /tmp/demo",
        "iptables -F",
        "nft flush ruleset",
    ):
        assert_action(command, ACTION_CONFIRM)


def test_h3c_risky_commands_use_comware_specific_rules() -> None:
    for command in (
        "reboot",
        "save force",
        "shutdown",
        "reset counters interface GigabitEthernet 1/0/1",
        "undo vlan 100",
    ):
        assert_action(command, ACTION_CONFIRM, device_profile="h3c_comware")

    shutdown_alert = check("shutdown", device_profile="h3c_comware")
    assert shutdown_alert is not None
    assert shutdown_alert["reason"] == "关闭当前接口或协议功能"
    assert check("save", device_profile="linux") is None


def test_irreversible_commands_are_permanently_blocked() -> None:
    for command in (
        "rm -rf /",
        "rm -r -f /",
        "bash -c 'rm -r -f /'",
        "rm --recursive --force /tmp/../*",
        "dd if=/dev/zero of=/dev/nvme0n1",
        "dd if=/dev/zero of=/dev/mmcblk0",
        "mkfs.ext4 /dev/sda1",
        "wipefs --all /dev/vda",
        "cat /dev/null > /etc/shadow",
        "erase startup-config",
        "execute factoryreset",
    ):
        assert_action(command, ACTION_BLOCK)

    for command in (
        "reset saved-configuration",
        "format flash:",
        "delete /unreserved flash:/startup.cfg",
    ):
        assert_action(command, ACTION_BLOCK, device_profile="h3c_comware")


def test_sudo_and_read_only_commands_are_allowed() -> None:
    for command in (
        "sudo ls -la",
        "sudo journalctl -u nginx",
        "df -h",
        "display version",
    ):
        assert check(command) is None

    for command in ("display version", "display vlan brief", "system-view"):
        assert check(command, device_profile="h3c_comware") is None
