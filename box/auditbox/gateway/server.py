"""Uvicorn entrypoint for the AuditBox gateway."""

from __future__ import annotations

import errno
import logging
import socket
import time

import uvicorn

from auditbox.env import (
    AUDITBOX_GATEWAY_HOSTS,
    AUDITBOX_GATEWAY_LOG_LEVEL,
    AUDITBOX_GATEWAY_PORT,
)

GATEWAY_BIND_RETRY_INTERVAL_SECONDS = 0.25
_LOGGER = logging.getLogger("uvicorn.error")


def bind_gateway_sockets(hosts: list[str], port: int) -> list[socket.socket]:
    """Bind one gateway process to every management-side USB address."""
    sockets: list[socket.socket] = []
    try:
        for host in dict.fromkeys(hosts):
            family = socket.AF_INET6 if ":" in host else socket.AF_INET
            address: tuple[str, int] | tuple[str, int, int, int]
            address = (host, port, 0, 0) if family == socket.AF_INET6 else (host, port)
            listener = socket.socket(family, socket.SOCK_STREAM)
            # Track the socket before bind so a failed partial attempt cannot leak it.
            sockets.append(listener)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if family == socket.AF_INET6:
                listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            listener.bind(address)
            listener.listen(2048)
            listener.set_inheritable(True)
    except BaseException:
        for listener in sockets:
            listener.close()
        raise
    return sockets


def bind_gateway_sockets_when_ready(
    hosts: list[str],
    port: int,
    retry_interval: float = GATEWAY_BIND_RETRY_INTERVAL_SECONDS,
) -> list[socket.socket]:
    """Wait for static USB addresses instead of crash-looping during early boot."""
    attempts = 0
    while True:
        try:
            listeners = bind_gateway_sockets(hosts, port)
        except OSError as exc:
            if not _is_address_not_available(exc):
                raise
            attempts += 1
            if attempts == 1 or attempts % 40 == 0:
                _LOGGER.warning(
                    "Gateway management address is not ready; retrying bind in %.2fs",
                    retry_interval,
                )
            time.sleep(retry_interval)
            continue
        if attempts:
            _LOGGER.info("Gateway management addresses became ready after %d retries", attempts)
        return listeners


def _is_address_not_available(error: OSError) -> bool:
    retryable = {errno.EADDRNOTAVAIL}
    windows_error = getattr(errno, "WSAEADDRNOTAVAIL", None)
    if windows_error is not None:
        retryable.add(windows_error)
    return error.errno in retryable or getattr(error, "winerror", None) in retryable


def main() -> None:
    config = uvicorn.Config(
        "auditbox.gateway.app:app",
        host=AUDITBOX_GATEWAY_HOSTS[0],
        port=AUDITBOX_GATEWAY_PORT,
        log_level=AUDITBOX_GATEWAY_LOG_LEVEL,
    )
    server = uvicorn.Server(config)
    listeners = bind_gateway_sockets_when_ready(
        AUDITBOX_GATEWAY_HOSTS,
        AUDITBOX_GATEWAY_PORT,
    )
    try:
        server.run(sockets=listeners)
    finally:
        for listener in listeners:
            listener.close()


if __name__ == "__main__":
    main()
