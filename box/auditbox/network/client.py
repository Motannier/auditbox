"""Unprivileged client for the target-network helper."""

from __future__ import annotations

import ipaddress
import json
import socket
import subprocess

from auditbox.env import (
    AUDITBOX_MANAGEMENT_NETWORKS,
    AUDITBOX_TARGET_INTERFACE,
    AUDITBOX_TARGET_PREPARE_SOCKET,
)


_PRIVATE_TARGET_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


class NetworkPreparationError(RuntimeError):
    """Raised when the appliance cannot prepare a route to a target."""


def prepare_target_network(host: str) -> dict | None:
    """Ask the privileged helper to prepare the target NIC for a literal IPv4 target."""
    socket_path = AUDITBOX_TARGET_PREPARE_SOCKET.strip()
    if not socket_path:
        return None

    try:
        target = ipaddress.ip_address(host)
    except ValueError:
        # Hostnames use the appliance's existing DNS and routes.
        return None
    if not isinstance(target, ipaddress.IPv4Address):
        raise NetworkPreparationError("目标网络自动配置目前仅支持 IPv4")

    return _request({"target": str(target)}, fallback_target=target)


def get_ethernet_settings() -> dict:
    return _request({"action": "get_ethernet"})


def set_ethernet_settings(mode: str, ip_address: str, netmask: str) -> dict:
    return _request({
        "action": "set_ethernet", "mode": mode, "ip_address": ip_address, "netmask": netmask,
    }, timeout=40)


def _request(
    payload: dict, *, timeout: int = 8, fallback_target: ipaddress.IPv4Address | None = None,
) -> dict:
    socket_path = AUDITBOX_TARGET_PREPARE_SOCKET.strip()
    if not socket_path:
        raise NetworkPreparationError("网口设置服务尚未配置，请先更新盒子")
    request = json.dumps(payload).encode("utf-8") + b"\n"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(socket_path)
            client.sendall(request)
            response_bytes = _read_line(client, 4096)
    except (OSError, TimeoutError) as exc:
        # Only connection preparation may reuse a route. A settings request must
        # never appear successful when the helper did not acknowledge it.
        if fallback_target is not None:
            existing = _existing_target_route(fallback_target)
            if existing is not None:
                return existing
            raise NetworkPreparationError(
                "目标网络助手不可用，且目标网卡没有可复用的同网段路由；"
                "请检查 auditbox-network-helper.service"
            ) from exc
        raise NetworkPreparationError(
            "网口设置服务不可用或响应超时，无法确认当前配置；"
            "请检查 auditbox-network-helper.service 并重新读取设置"
        ) from exc

    try:
        response = json.loads(response_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NetworkPreparationError("目标网络助手返回了无效响应") from exc

    if not isinstance(response, dict):
        raise NetworkPreparationError("目标网络助手返回了无效响应")
    if not response.get("ok"):
        raise NetworkPreparationError(str(response.get("error") or "目标网络配置失败"))
    return response


def _existing_target_route(target: ipaddress.IPv4Address) -> dict | None:
    """Reuse only a kernel route already bound to the configured target NIC."""
    target_network = ipaddress.ip_network(f"{target}/24", strict=False)
    management_networks = tuple(
        ipaddress.ip_network(value, strict=False) for value in AUDITBOX_MANAGEMENT_NETWORKS
    )
    if (
        not any(target in network for network in _PRIVATE_TARGET_NETWORKS)
        or any(target in network for network in management_networks)
        or target in {target_network.network_address, target_network.broadcast_address}
    ):
        return None
    try:
        completed = subprocess.run(
            ["ip", "-j", "route", "get", str(target)],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        payload = json.loads(completed.stdout) if completed.returncode == 0 else []
        route = payload[0] if isinstance(payload, list) and payload else {}
        source = ipaddress.ip_address(str(route.get("prefsrc") or route.get("src") or ""))
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, json.JSONDecodeError):
        return None
    if (
        route.get("dev") != AUDITBOX_TARGET_INTERFACE
        or not isinstance(source, ipaddress.IPv4Address)
        or source not in target_network
        or source == target
    ):
        return None
    return {
        "ok": True,
        "target": str(target),
        "network": str(target_network),
        "source": str(source),
        "action": "existing-route",
        "degraded": True,
    }


def _read_line(client: socket.socket, limit: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < limit:
        chunk = client.recv(min(1024, limit - len(chunks)))
        if not chunk:
            break
        chunks.extend(chunk)
        if b"\n" in chunk:
            return bytes(chunks).split(b"\n", 1)[0]
    if not chunks:
        raise NetworkPreparationError("目标网络助手关闭连接且没有返回响应")
    if len(chunks) >= limit:
        raise NetworkPreparationError("目标网络助手响应过大")
    return bytes(chunks)
