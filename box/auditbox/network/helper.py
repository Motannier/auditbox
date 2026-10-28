"""Privileged, narrowly scoped helper for preparing target-side IPv4 routes."""

from __future__ import annotations

import configparser
import grp
import ipaddress
import json
import os
from pathlib import Path
import re
import socketserver
import subprocess
import tempfile
import threading
import time

from auditbox.env import (
    AUDITBOX_MANAGEMENT_NETWORKS,
    AUDITBOX_TARGET_INTERFACE,
    AUDITBOX_TARGET_PREPARE_SOCKET,
)

ALLOWED_TARGET_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
CANDIDATE_HOSTS = (2, 254, 253, 3, 4, 5, 10, 20, 100, 200)
_PREPARE_LOCK = threading.Lock()
SETTINGS_FILE = Path("/run/auditbox-network/ethernet.network")
SETTINGS_LINK = Path("/etc/systemd/network/05-auditbox-target-user.network")
DEFAULT_ADDRESS = ipaddress.IPv4Interface("169.254.200.6/24")


class PreparationError(RuntimeError):
    """An expected target-network validation or configuration failure."""


def prepare_target(target_text: str) -> dict[str, str | bool]:
    configured = _read_static_address()
    if configured is not None:
        target = _validate_ipv4_host(target_text)
        if target == configured.ip:
            raise PreparationError("目标 IP 与盒子网口 IP 相同，请为两台设备设置不同地址")
        if target not in configured.network or target in {
            configured.network.network_address, configured.network.broadcast_address,
        }:
            raise PreparationError(f"目标 {target} 不在手动设置的网口网段 {configured.network} 内，请检查网络设置")
        interface = _target_interface()
        if _carrier_state(interface) != "1":
            raise PreparationError(f"目标设备网线尚未连接到 {interface}")
        if configured not in _interface_addresses(interface):
            raise PreparationError("手动网口配置尚未生效，请在设置中重新保存")
        return _result(target, configured.network, configured.ip, "manual")

    target = _validate_target(target_text)
    target_network = ipaddress.ip_network(f"{target}/24", strict=False)
    interface = _target_interface()

    if _carrier_state(interface) != "1":
        raise PreparationError(f"目标设备网线尚未连接到 {interface}")

    _run(["ip", "link", "set", interface, "up"])
    existing = _interface_addresses(interface)
    # Addresses added by this helper are disposable target-side aliases. If a
    # later server uses one of those aliases as its own address, release the
    # alias before selecting a new source address. The permanent management
    # address is protected by _validate_target above.
    for address in existing:
        if address.ip == target:
            _run(["ip", "addr", "del", str(address), "dev", interface])
    existing = [address for address in existing if address.ip != target]

    for address in existing:
        if address.ip in target_network and address.ip != target:
            if address.network.prefixlen > target_network.prefixlen:
                _run([
                    "ip", "route", "replace", str(target_network), "dev", interface,
                    "src", str(address.ip),
                ])
            return _result(target, target_network, address.ip, "existing")

    for host_number in CANDIDATE_HOSTS:
        candidate = ipaddress.IPv4Address(int(target_network.network_address) + host_number)
        if candidate == target or candidate in {target_network.network_address, target_network.broadcast_address}:
            continue
        if any(address.ip == candidate for address in existing):
            continue
        probe = _run([
            "arping", "-D", "-q", "-c", "2", "-w", "2", "-I", interface,
            str(candidate),
        ], check=False)
        if probe.returncode == 1:
            continue
        if probe.returncode != 0:
            raise PreparationError(f"无法在 {interface} 上检测地址 {candidate}")
        _run(["ip", "addr", "add", f"{candidate}/24", "dev", interface])
        return _result(target, target_network, candidate, "added")

    raise PreparationError(f"在 {target_network} 中没有找到可用的安全审计装置地址")


def _target_interface() -> str:
    interface = AUDITBOX_TARGET_INTERFACE
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,14}", interface) or interface.startswith(
        ("lo", "br-", "usb", "rndis")
    ):
        raise PreparationError("目标网口配置无效，不能修改 USB 管理接口")
    return interface


def _validate_ipv4_host(value: str) -> ipaddress.IPv4Address:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise PreparationError("请输入有效的 IPv4 地址") from exc
    if not isinstance(address, ipaddress.IPv4Address) or (
        address.is_loopback or address.is_multicast or address.is_unspecified or address.is_reserved
        or address in ipaddress.ip_network("0.0.0.0/8")
    ):
        raise PreparationError("请输入可用于设备通信的 IPv4 单播地址")
    return address


def _static_address(address_text: str, netmask: str) -> ipaddress.IPv4Interface:
    address = _validate_ipv4_host(address_text)
    try:
        # Dotted decimal masks only; reject ipaddress's implicit hostmask syntax.
        mask = ipaddress.IPv4Address(netmask)
        configured = ipaddress.IPv4Interface(f"{address}/{netmask}")
        if configured.netmask != mask or not 8 <= configured.network.prefixlen <= 30:
            raise ValueError("mask")
    except ValueError as exc:
        raise PreparationError("请输入连续的子网掩码，支持 /8 到 /30，例如 255.255.255.0") from exc
    if configured.ip in {configured.network.network_address, configured.network.broadcast_address}:
        raise PreparationError("盒子 IP 不能使用该网段的网络地址或广播地址")
    if any(configured.network.overlaps(ipaddress.ip_network(value, strict=False)) for value in AUDITBOX_MANAGEMENT_NETWORKS):
        raise PreparationError("有线网口网段不能与 USB 管理网段重叠")
    return configured


def _read_static_address() -> ipaddress.IPv4Interface | None:
    try:
        content = SETTINGS_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PreparationError("无法读取已保存的网口配置") from exc
    if not content.strip():
        return None
    try:
        config = configparser.ConfigParser(interpolation=None)
        config.read_string(content)
        if config["Match"]["Name"] != _target_interface():
            raise ValueError("interface mismatch")
        configured = ipaddress.IPv4Interface(config["Network"]["Address"])
        return _static_address(str(configured.ip), str(configured.netmask))
    except (KeyError, ValueError, configparser.Error) as exc:
        raise PreparationError("已保存的网口配置无效，请在设置中重新保存") from exc


def ethernet_settings() -> dict:
    interface = _target_interface()
    configured = _read_static_address()
    existing = _interface_addresses(interface)
    primary = configured or (existing[0] if existing else None)
    return {
        "ok": True,
        "interface": interface,
        "mode": "static" if configured else "auto",
        "ip_address": str(primary.ip) if primary else "",
        "netmask": str(primary.netmask) if primary else "255.255.255.0",
        "addresses": [str(address) for address in existing],
        "link_up": _carrier_state(interface) == "1",
        "management_networks": AUDITBOX_MANAGEMENT_NETWORKS,
        "reset_on_reboot": True,
        "default_address": str(DEFAULT_ADDRESS),
    }


def _write_network_file(content: bytes) -> None:
    # /run is cleared on reboot, so networkd falls back to the factory config.
    # The fixed networkd symlink loads only this target NIC's temporary file.
    descriptor, name = tempfile.mkstemp(prefix=".ethernet-", dir=SETTINGS_FILE.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o644)
        os.replace(name, SETTINGS_FILE)
    finally:
        Path(name).unlink(missing_ok=True)


def _reload_target_interface(interface: str) -> None:
    _run(["networkctl", "reload"])
    _run(["networkctl", "reconfigure", interface])


def configure_ethernet(payload: dict) -> dict:
    interface = _target_interface()
    mode = payload.get("mode")
    if not isinstance(mode, str) or mode not in {"auto", "static"}:
        raise PreparationError("请选择自动适配或手动固定 IP")
    configured = _static_address(
        str(payload.get("ip_address", "")), str(payload.get("netmask", "")),
    ) if mode == "static" else None
    if not SETTINGS_LINK.is_symlink() or SETTINGS_LINK.resolve() != SETTINGS_FILE.resolve():
        raise PreparationError("网口设置组件尚未安装，请先更新盒子")

    existing = _interface_addresses(interface)
    link_up = _carrier_state(interface) == "1"
    conflict_checked = False
    if configured and link_up and not any(address.ip == configured.ip for address in existing):
        probe = _run([
            "arping", "-D", "-q", "-c", "2", "-w", "2", "-I", interface, str(configured.ip),
        ], check=False)
        if probe.returncode == 1:
            raise PreparationError(f"IP {configured.ip} 已被网线另一端的设备占用，请更换地址")
        if probe.returncode != 0:
            raise PreparationError("无法检查网口 IP 冲突，配置未修改")
        conflict_checked = True

    content = (
        f"# Managed by AuditBox network settings\n[Match]\nName={interface}\n\n"
        "[Link]\nRequiredForOnline=no\n\n[Network]\n"
        f"Address={configured}\nDHCP=no\nLinkLocalAddressing=no\n"
        "IPv6AcceptRA=no\nConfigureWithoutCarrier=yes\nIPForward=no\n"
    ).encode("utf-8") if configured else b""
    try:
        previous = SETTINGS_FILE.read_bytes() if SETTINGS_FILE.exists() else b""
        _write_network_file(content)
        try:
            _reload_target_interface(interface)
            if configured or link_up:
                expected = configured or DEFAULT_ADDRESS
                deadline = time.monotonic() + 5
                while expected not in _interface_addresses(interface):
                    if time.monotonic() >= deadline:
                        raise PreparationError("网口未应用所设置的 IP 地址")
                    time.sleep(0.2)
            result = ethernet_settings()
        except (PreparationError, OSError) as exc:
            _write_network_file(previous)
            try:
                _reload_target_interface(interface)
            except PreparationError as rollback_error:
                raise PreparationError(f"配置应用失败，已恢复配置文件但重新加载失败：{rollback_error}") from exc
            raise PreparationError(f"配置应用失败，已恢复原配置：{exc}") from exc
    except OSError as exc:
        raise PreparationError(f"无法保存网口配置：{exc}") from exc
    result["conflict_checked"] = conflict_checked
    return result


def handle_request(request: dict) -> dict:
    action = request.get("action", "prepare")
    if action == "get_ethernet":
        return ethernet_settings()
    if action == "set_ethernet":
        return configure_ethernet(request)
    if action == "prepare":
        return prepare_target(str(request.get("target", "")))
    raise PreparationError("不支持的网络助手操作")


def _validate_target(target_text: str) -> ipaddress.IPv4Address:
    try:
        target = ipaddress.ip_address(target_text)
    except ValueError as exc:
        raise PreparationError("目标必须是有效的 IPv4 地址") from exc
    if not isinstance(target, ipaddress.IPv4Address):
        raise PreparationError("目标网络自动配置目前仅支持 IPv4")
    if not any(target in network for network in ALLOWED_TARGET_NETWORKS):
        raise PreparationError("目标网络自动配置仅支持私有 IPv4 地址")

    management_networks = tuple(
        ipaddress.ip_network(value, strict=False)
        for value in AUDITBOX_MANAGEMENT_NETWORKS
    )
    if any(target in network for network in management_networks):
        raise PreparationError(
            f"目标 {target} 与 USB 管理网段冲突"
        )

    target_network = ipaddress.ip_network(f"{target}/24", strict=False)
    if target in {target_network.network_address, target_network.broadcast_address}:
        raise PreparationError(f"目标 {target} 不是可用的 /24 主机地址")
    return target


def _carrier_state(interface: str) -> str:
    path = Path("/sys/class/net") / interface / "carrier"
    try:
        return path.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise PreparationError(f"网卡 {interface} 不可用") from exc


def _interface_addresses(interface: str) -> list[ipaddress.IPv4Interface]:
    completed = _run(["ip", "-j", "-4", "addr", "show", "dev", interface])
    try:
        payload = json.loads(completed.stdout)
        return [
            ipaddress.ip_interface(f"{entry['local']}/{entry['prefixlen']}")
            for item in payload
            for entry in item.get("addr_info", [])
            if entry.get("family") == "inet"
        ]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PreparationError(f"无法检查 {interface} 上的 IPv4 地址") from exc


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=6,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise PreparationError(f"执行 {' '.join(command[:3])} 超时") from exc
    except OSError as exc:
        raise PreparationError(f"无法执行 {command[0]}：{exc}") from exc
    if check and completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "未知错误"
        raise PreparationError(f"执行 {' '.join(command[:3])} 失败：{detail}")
    return completed


def _result(
    target: ipaddress.IPv4Address,
    network: ipaddress.IPv4Network,
    source: ipaddress.IPv4Address,
    action: str,
) -> dict[str, str | bool]:
    return {
        "ok": True,
        "target": str(target),
        "network": str(network),
        "source": str(source),
        "action": action,
    }


class _RequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            raw = self.rfile.readline(4097)
            if not raw or len(raw) > 4096:
                raise PreparationError("网络助手请求无效")
            request = json.loads(raw.decode("utf-8"))
            if not isinstance(request, dict):
                raise PreparationError("网络助手请求无效")
            with _PREPARE_LOCK:
                response = handle_request(request)
        except (PreparationError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            response = {"ok": False, "error": str(exc)}
        self.wfile.write(json.dumps(response).encode("utf-8") + b"\n")


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def main() -> None:
    socket_path = Path(AUDITBOX_TARGET_PREPARE_SOCKET)
    if not socket_path.is_absolute():
        raise SystemExit("AUDITBOX_TARGET_PREPARE_SOCKET 必须是绝对路径")
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.unlink(missing_ok=True)
    with _Server(str(socket_path), _RequestHandler) as server:
        os.chmod(socket_path, 0o660)
        os.chown(socket_path, 0, grp.getgrnam("auditbox").gr_gid)
        print(f"AuditBox network helper listening on {socket_path}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
