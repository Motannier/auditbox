from auditbox.relay import console_proxy
from auditbox.relay.console_proxy import ConsoleProxy


class _Audit:
    session_id = "console-test"


class _FakeSerial:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_console_proxy_opens_expected_serial_settings_without_real_device(monkeypatch) -> None:
    opened: list[_FakeSerial] = []

    def create_serial(**kwargs):
        instance = _FakeSerial(**kwargs)
        opened.append(instance)
        return instance

    monkeypatch.setattr(console_proxy.serial, "Serial", create_serial)
    proxy = ConsoleProxy(_Audit())
    relayed: list[object] = []
    monkeypatch.setattr(proxy, "_relay", lambda _client, target: relayed.append(target))
    ready: list[bool] = []

    proxy.connect_and_relay(
        object(),
        {"host": "/dev/ttyUSB0", "port": 115200},
        on_ready=lambda: ready.append(True),
    )

    assert ready == [True]
    assert relayed == opened
    assert opened[0].kwargs["port"] == "/dev/ttyUSB0"
    assert opened[0].kwargs["baudrate"] == 115200
    assert opened[0].kwargs["bytesize"] == console_proxy.serial.EIGHTBITS
    assert opened[0].kwargs["parity"] == console_proxy.serial.PARITY_NONE
    assert opened[0].kwargs["stopbits"] == console_proxy.serial.STOPBITS_ONE
    assert opened[0].closed is True
