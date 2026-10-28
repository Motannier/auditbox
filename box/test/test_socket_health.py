import socket

from auditbox.relay.socket_health import KEEPALIVE_IDLE_SECONDS, enable_fast_disconnect_detection


class FakeSocket:
    def __init__(self) -> None:
        self.options: list[tuple[int, int, int]] = []

    def setsockopt(self, level: int, option: int, value: int) -> None:
        self.options.append((level, option, value))


def test_fast_disconnect_detection_enables_tcp_keepalive() -> None:
    target = FakeSocket()

    enable_fast_disconnect_detection(target)  # type: ignore[arg-type]

    assert (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1) in target.options
    assert KEEPALIVE_IDLE_SECONDS == 2
    if hasattr(socket, "TCP_KEEPIDLE"):
        assert (socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 2) in target.options
    if hasattr(socket, "TCP_USER_TIMEOUT"):
        assert any(option == socket.TCP_USER_TIMEOUT for _, option, _ in target.options)
