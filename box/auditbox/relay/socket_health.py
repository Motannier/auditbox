"""Cross-platform TCP keepalive settings for target-side sessions."""

from __future__ import annotations

import logging
import socket

log = logging.getLogger(__name__)

KEEPALIVE_IDLE_SECONDS = 2
KEEPALIVE_INTERVAL_SECONDS = 2
KEEPALIVE_PROBE_COUNT = 2
TCP_USER_TIMEOUT_MS = 6_000


def enable_fast_disconnect_detection(sock: socket.socket) -> None:
    """Detect a dead target link in seconds instead of the OS default minutes.

    Each option is best-effort because Linux, macOS and Windows expose
    different socket controls. Unsupported options are skipped so the relay
    remains portable.
    """
    _set_option(sock, socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

    if hasattr(socket, "TCP_KEEPIDLE"):
        _set_option(sock, socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, KEEPALIVE_IDLE_SECONDS)
    elif hasattr(socket, "TCP_KEEPALIVE"):
        _set_option(sock, socket.IPPROTO_TCP, socket.TCP_KEEPALIVE, KEEPALIVE_IDLE_SECONDS)

    if hasattr(socket, "TCP_KEEPINTVL"):
        _set_option(sock, socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, KEEPALIVE_INTERVAL_SECONDS)
    if hasattr(socket, "TCP_KEEPCNT"):
        _set_option(sock, socket.IPPROTO_TCP, socket.TCP_KEEPCNT, KEEPALIVE_PROBE_COUNT)
    if hasattr(socket, "TCP_USER_TIMEOUT"):
        _set_option(sock, socket.IPPROTO_TCP, socket.TCP_USER_TIMEOUT, TCP_USER_TIMEOUT_MS)

    # Windows configures the keepalive idle and interval values through ioctl.
    if hasattr(socket, "SIO_KEEPALIVE_VALS") and hasattr(sock, "ioctl"):
        try:
            sock.ioctl(
                socket.SIO_KEEPALIVE_VALS,
                (1, KEEPALIVE_IDLE_SECONDS * 1000, KEEPALIVE_INTERVAL_SECONDS * 1000),
            )
        except OSError as exc:
            log.debug("无法设置 Windows TCP keepalive 参数：%s", exc)


def _set_option(sock: socket.socket, level: int, option: int, value: int) -> None:
    try:
        sock.setsockopt(level, option, value)
    except OSError as exc:
        log.debug("无法设置 TCP keepalive 选项 %s：%s", option, exc)
