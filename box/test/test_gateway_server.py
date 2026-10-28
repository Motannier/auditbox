from __future__ import annotations

import errno
import socket

import pytest

from auditbox.gateway import server


class FakeSocket:
    def __init__(self, family: int, kind: int) -> None:
        self.family = family
        self.kind = kind
        self.address: tuple[object, ...] | None = None
        self.closed = False

    def setsockopt(self, *_args: object) -> None:
        pass

    def bind(self, address: tuple[object, ...]) -> None:
        self.address = address

    def listen(self, _backlog: int) -> None:
        pass

    def set_inheritable(self, _value: bool) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def test_gateway_binds_each_unique_management_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listeners: list[FakeSocket] = []

    def fake_socket(family: int, kind: int) -> FakeSocket:
        listener = FakeSocket(family, kind)
        listeners.append(listener)
        return listener

    monkeypatch.setattr(server.socket, "socket", fake_socket)

    result = server.bind_gateway_sockets(
        ["192.168.78.1", "192.168.77.1", "192.168.78.1"],
        80,
    )

    assert result == listeners
    assert [listener.address for listener in listeners] == [
        ("192.168.78.1", 80),
        ("192.168.77.1", 80),
    ]
    assert all(listener.family == socket.AF_INET for listener in listeners)


def test_failed_partial_bind_closes_every_created_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listeners: list[FakeSocket] = []

    class FailingSocket(FakeSocket):
        def bind(self, address: tuple[object, ...]) -> None:
            super().bind(address)
            if address[0] == "192.168.77.1":
                raise OSError(errno.EADDRNOTAVAIL, "address not ready")

    def fake_socket(family: int, kind: int) -> FakeSocket:
        listener = FailingSocket(family, kind)
        listeners.append(listener)
        return listener

    monkeypatch.setattr(server.socket, "socket", fake_socket)

    with pytest.raises(OSError) as error:
        server.bind_gateway_sockets(["192.168.78.1", "192.168.77.1"], 80)

    assert error.value.errno == errno.EADDRNOTAVAIL
    assert len(listeners) == 2
    assert all(listener.closed for listener in listeners)


def test_gateway_waits_for_management_addresses_without_crash_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listener = FakeSocket(socket.AF_INET, socket.SOCK_STREAM)
    attempts = 0
    sleeps: list[float] = []

    def fake_bind(_hosts: list[str], _port: int) -> list[FakeSocket]:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise OSError(errno.EADDRNOTAVAIL, "address not ready")
        return [listener]

    monkeypatch.setattr(server, "bind_gateway_sockets", fake_bind)
    monkeypatch.setattr(server.time, "sleep", sleeps.append)

    result = server.bind_gateway_sockets_when_ready(
        ["192.168.78.1", "192.168.77.1"],
        80,
        retry_interval=0.1,
    )

    assert result == [listener]
    assert attempts == 3
    assert sleeps == [0.1, 0.1]


def test_gateway_does_not_retry_non_transient_bind_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []

    def fake_bind(_hosts: list[str], _port: int) -> list[FakeSocket]:
        raise OSError(errno.EADDRINUSE, "port already in use")

    monkeypatch.setattr(server, "bind_gateway_sockets", fake_bind)
    monkeypatch.setattr(server.time, "sleep", sleeps.append)

    with pytest.raises(OSError) as error:
        server.bind_gateway_sockets_when_ready(["192.168.78.1"], 80)

    assert error.value.errno == errno.EADDRINUSE
    assert sleeps == []
