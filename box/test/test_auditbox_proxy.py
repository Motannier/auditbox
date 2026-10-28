import json
import socket

import paramiko
import pytest

from auditbox.app import proxy_server
from auditbox.env import AUDITBOX_PROXY_HOST
from auditbox.prediction import server as prediction_server
from auditbox.relay import ssh_proxy
from auditbox.relay.ssh_proxy import SSHConnectionError, SSHProxy


def test_internal_relay_listeners_use_the_configured_proxy_host() -> None:
    assert proxy_server.HOST == AUDITBOX_PROXY_HOST
    assert prediction_server.PREDICT_HOST == AUDITBOX_PROXY_HOST


class _Audit:
    session_id = "test-session"


class _Channel:
    def __init__(self) -> None:
        self.blocking = None

    def setblocking(self, value: bool) -> None:
        self.blocking = value


class _Transport:
    sock = None

    def set_keepalive(self, _seconds: int) -> None:
        return None


class _SuccessfulSSHClient:
    def __init__(self) -> None:
        self.channel = _Channel()
        self.connected = False

    def set_missing_host_key_policy(self, _policy) -> None:
        return None

    def connect(self, **_kwargs) -> None:
        self.connected = True

    def get_transport(self):
        return None

    def invoke_shell(self, **_kwargs):
        assert self.connected
        return self.channel

    def close(self) -> None:
        return None


class _AuthenticationFailedSSHClient(_SuccessfulSSHClient):
    def connect(self, **_kwargs) -> None:
        raise paramiko.AuthenticationException("Authentication failed")


def _ssh_params() -> dict:
    return {
        "device_type": "switch",
        "device_profile": "h3c_comware",
        "host": "192.0.2.10",
        "port": 22,
        "username": "admin",
        "password": "wrong-password",
    }


def test_ssh_proxy_reports_ready_after_authentication_and_shell(monkeypatch) -> None:
    client = _SuccessfulSSHClient()
    monkeypatch.setattr(ssh_proxy.paramiko, "SSHClient", lambda: client)

    events: list[str] = []
    proxy = SSHProxy(_Audit())
    monkeypatch.setattr(proxy, "_relay", lambda _sock, _channel: events.append("relay"))

    proxy.connect_and_relay(object(), _ssh_params(), on_ready=lambda: events.append("ready"))

    assert events == ["ready", "relay"]
    assert client.channel.blocking is False


def test_ssh_proxy_returns_structured_authentication_error(monkeypatch) -> None:
    monkeypatch.setattr(
        ssh_proxy.paramiko,
        "SSHClient",
        _AuthenticationFailedSSHClient,
    )
    ready = False

    def mark_ready() -> None:
        nonlocal ready
        ready = True

    with pytest.raises(SSHConnectionError) as captured:
        SSHProxy(_Audit()).connect_and_relay(object(), _ssh_params(), on_ready=mark_ready)

    assert ready is False
    assert captured.value.code == "auth_failed"
    assert captured.value.message == "SSH 用户名或密码错误，请重新输入"


class _Engine:
    def __init__(self) -> None:
        self.registrations = []

    def register_session(self, *_args, **_kwargs) -> None:
        self.registrations.append((_args, _kwargs))

    def unregister_session(self, *_args, **_kwargs) -> None:
        return None

    def on_command(self, *_args, **_kwargs) -> None:
        return None

    def on_security_alert(self, *_args, **_kwargs) -> None:
        return None

    def update_device_type(self, *_args, **_kwargs) -> None:
        return None

    def register_confirmation_handler(self, *_args, **_kwargs) -> None:
        return None

    def register_audit_handler(self, *_args, **_kwargs) -> None:
        return None


class _AuditLogger:
    def __init__(self, session_id: str, **_kwargs) -> None:
        self.session_id = session_id

    def close(self) -> None:
        return None

    def log_operation(self, *_args, **_kwargs) -> None:
        return None


class _FailedSSHProxy:
    def __init__(self, *_args, **_kwargs) -> None:
        return None

    def connect_and_relay(self, _conn, _params, on_ready=None) -> None:
        assert on_ready is not None
        raise SSHConnectionError("auth_failed", "SSH 用户名或密码错误，请重新输入")


@pytest.mark.parametrize("protocol,proxy_name", [("ssh", "SSHProxy"), ("telnet", "TelnetProxy"), ("console", "ConsoleProxy")])
@pytest.mark.parametrize("framed", [False, True])
def test_input_source_framing_is_negotiated_for_each_transport(monkeypatch, protocol, proxy_name, framed):
    from auditbox.relay.input_frames import INPUT_FRAMING
    options = []
    class ReadyProxy:
        def __init__(self, _audit, **kwargs):
            options.append(kwargs)
        def connect_and_relay(self, _conn, _params, on_ready=None):
            if on_ready:
                on_ready()
    monkeypatch.setattr(proxy_server, "_engine", _Engine())
    monkeypatch.setattr(proxy_server, "AuditLogger", _AuditLogger)
    monkeypatch.setattr(proxy_server, proxy_name, ReadyProxy)
    server, client = socket.socketpair()
    try:
        payload = {**_ssh_params(), "protocol": protocol}
        if framed:
            payload["input_framing"] = INPUT_FRAMING
        client.sendall((json.dumps(payload) + "\n").encode())
        proxy_server.handle_client(server, ("127.0.0.1", 12345))
        with client.makefile("rb") as stream:
            response = json.loads(stream.readline())
        assert response["status"] == "ok"
        assert response.get("input_framing") == (INPUT_FRAMING if framed else None)
        assert options[0]["input_framing"] is framed
    finally:
        server.close()
        client.close()


def test_proxy_handshake_does_not_report_success_for_bad_password(monkeypatch) -> None:
    engine = _Engine()
    monkeypatch.setattr(proxy_server, "_engine", engine)
    monkeypatch.setattr(proxy_server, "AuditLogger", _AuditLogger)
    monkeypatch.setattr(proxy_server, "SSHProxy", _FailedSSHProxy)
    server_sock, client_sock = socket.socketpair()
    try:
        client_sock.sendall((json.dumps({"protocol": "ssh", **_ssh_params()}) + "\n").encode())
        proxy_server.handle_client(server_sock, ("127.0.0.1", 12345))
        response = json.loads(client_sock.makefile("rb").readline())
    finally:
        client_sock.close()

    assert response == {
        "status": "error",
        "code": "auth_failed",
        "message": "SSH 用户名或密码错误，请重新输入",
    }
    assert engine.registrations[0][1]["device_type"] == "switch"
    assert engine.registrations[0][1]["device_profile"] == "h3c_comware"


def test_proxy_handshake_rejects_missing_device_type(monkeypatch) -> None:
    engine = _Engine()
    monkeypatch.setattr(proxy_server, "_engine", engine)
    server_sock, client_sock = socket.socketpair()
    try:
        params = {key: value for key, value in _ssh_params().items() if key != "device_type"}
        client_sock.sendall((json.dumps({"protocol": "ssh", **params}) + "\n").encode())
        proxy_server.handle_client(server_sock, ("127.0.0.1", 12345))
        response = json.loads(client_sock.makefile("rb").readline())
    finally:
        client_sock.close()

    assert response["code"] == "invalid_device_type"
    assert engine.registrations == []


def test_proxy_handshake_rejects_missing_device_profile(monkeypatch) -> None:
    engine = _Engine()
    monkeypatch.setattr(proxy_server, "_engine", engine)
    server_sock, client_sock = socket.socketpair()
    try:
        params = {key: value for key, value in _ssh_params().items() if key != "device_profile"}
        client_sock.sendall((json.dumps({"protocol": "ssh", **params}) + "\n").encode())
        proxy_server.handle_client(server_sock, ("127.0.0.1", 12345))
        response = json.loads(client_sock.makefile("rb").readline())
    finally:
        client_sock.close()

    assert response["code"] == "invalid_device_profile"
    assert engine.registrations == []
