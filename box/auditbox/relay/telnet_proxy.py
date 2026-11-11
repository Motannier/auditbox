"""
Telnet 代理模块
- 连接笔记本 TCP socket 与目标 Telnet 服务
- 双向透明转发，剥离 IAC 协商字节后旁路审计输入命令
- 从服务端初始 banner 检测设备类型，回调通知预测引擎
"""

import logging
import re
import select
import socket
from typing import Callable, Optional

from auditbox.audit.logger import AuditLogger
from auditbox.prediction.detector import detect as detect_device, DETECT_BYTES
from auditbox.relay.command_gate import CommandGate
from auditbox.relay.socket_health import enable_fast_disconnect_detection

log = logging.getLogger(__name__)

# Telnet 协议控制字节（RFC 854）
_IAC  = 0xFF  # Interpret As Command
_WILL = 0xFB
_WONT = 0xFC
_DO   = 0xFD
_DONT = 0xFE
_SB   = 0xFA  # Subnegotiation Begin
_SE   = 0xF0  # Subnegotiation End

_ANSI_ESCAPE_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_USERNAME_PROMPT_RE = re.compile(
    r"(?i)(?:username|user\s*name|login|用户名|账号)\s*[:：]\s*$"
)
_PASSWORD_PROMPT_RE = re.compile(
    r"(?i)(?:password|passwd|passphrase|口令|密码)\s*[:：]\s*$"
)


def _strip_iac(data: bytes) -> bytes:
    """
    从 Telnet 字节流中剥离 IAC 控制序列，仅保留用户键入的数据字节。

    IAC 序列格式（RFC 854）：
      - IAC <cmd>                      2 字节（NOP / AYT 等）
      - IAC <WILL|WONT|DO|DONT> <opt>  3 字节（选项协商）
      - IAC SB <opt> <data…> IAC SE    可变长（子选项，如 NAWS 窗口大小）

    Args:
        data (bytes): 含 IAC 控制序列的原始 Telnet 客户端字节流。

    Returns:
        bytes: 剥离全部 IAC 序列后的纯用户数据；末尾不完整的 IAC 序列
               被静默丢弃（命令提取层可容忍单次丢失）。

    Raises:
        无。

    Example:
        >>> _strip_iac(b"ls\\r" + bytes([0xFF, 0xFD, 0x01]))
        b'ls\\r'
    """
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        b = data[i]
        if b != _IAC:
            out.append(b)
            i += 1
            continue
        i += 1
        if i >= n:
            break
        cmd = data[i]
        i += 1
        if cmd in (_WILL, _WONT, _DO, _DONT):
            i += 1                      # 跳过 1 字节 option
        elif cmd == _SB:
            while i < n - 1:            # 跳到 IAC SE
                if data[i] == _IAC and data[i + 1] == _SE:
                    i += 2
                    break
                i += 1
        # 其余 2 字节命令（cmd 已消费）：继续循环
    return bytes(out)


class _TelnetAutoLogin:
    """Submit configured credentials only after a recognizable Telnet prompt."""

    def __init__(self, username: str, password: str, send: Callable[[bytes], object]):
        self._username = username
        self._password = password
        self._send = send
        self._tail = ""
        self._username_sent = False
        self._password_sent = False

    def feed(self, output: bytes) -> str | None:
        decoded = _ANSI_ESCAPE_RE.sub("", output.decode(errors="replace"))
        self._tail = (self._tail + decoded)[-512:]

        if not self._username_sent and self._username and _USERNAME_PROMPT_RE.search(self._tail):
            self._send(self._username.encode("utf-8") + b"\r\n")
            self._username_sent = True
            self._tail = ""
            return "username"

        if not self._password_sent and self._password and _PASSWORD_PROMPT_RE.search(self._tail):
            self._send(self._password.encode("utf-8") + b"\r\n")
            self._password_sent = True
            self._tail = ""
            return "password"
        return None


class TelnetProxy:
    """
    Telnet 代理：在笔记本侧 TCP socket 与目标 Telnet 服务之间建立
    透明转发通道，同时旁路提取用户命令写入审计日志，并从服务端
    初始 banner 检测设备类型。

    工作流程：
      1. connect_and_relay 向目标建立 TCP 连接
      2. _relay 进入 select 双向转发循环
      3. 客户端→目标：透传全部字节；剥离 IAC 后交 _extract_commands 审计
      4. 目标→客户端：透传全部字节（含 IAC）；收集 banner 触发设备检测
    """

    def __init__(
        self,
        audit: AuditLogger,
        on_device_detected: Optional[Callable[[str], None]] = None,
        on_confirmation_ready=None,
        device_profile: str = "linux",
        input_framing: bool = False,
    ):
        """
        Args:
            audit (AuditLogger): 当前会话的审计日志记录器。
            on_device_detected (callable | None): 设备类型检测完成后的回调，
                参数为 device_type 字符串（如 "cisco_ios"）；None 则不回调。

        Raises:
            无，构造函数不做 IO 操作。

        Example:
            proxy = TelnetProxy(audit, on_device_detected=engine.update_device_type)
        """
        self._audit = audit
        self._on_device_detected = on_device_detected
        self._on_confirmation_ready = on_confirmation_ready
        self._device_profile = device_profile
        self._input_framing = input_framing

    def connect_and_relay(self, client_sock: socket.socket, params: dict) -> None:
        """
        建立到目标 Telnet 服务的 TCP 连接并进入双向转发循环。

        Args:
            client_sock (socket.socket): 已建立的笔记本侧 TCP socket。
            params (dict): 握手 JSON 解析结果，需含：
                host (str) — 目标地址；
                port (int) — 目标端口，缺省 23。

        Returns:
            None。函数返回即表示会话结束（正常断开或异常均会返回）。

        Raises:
            OSError: socket 连接或读写失败。
            ConnectionRefusedError: 目标 Telnet 端口拒绝连接。

        Example:
            proxy.connect_and_relay(conn, {"host": "192.168.1.1", "port": 23})
        """
        host = params["host"]
        port = params.get("port", 23)

        log.info("[%s] Telnet 连接 %s:%d", self._audit.session_id, host, port)
        try:
            target = socket.create_connection((host, port), timeout=10)
            target.settimeout(None)
            enable_fast_disconnect_detection(target)
        except OSError as e:
            log.error("[%s] Telnet 连接失败: %s", self._audit.session_id, e)
            raise

        try:
            self._relay(
                client_sock,
                target,
                username=str(params.get("username") or ""),
                password=str(params.get("password") or ""),
            )
        finally:
            try:
                target.close()
            except OSError:
                pass

    def _relay(
        self,
        client_sock: socket.socket,
        target_sock: socket.socket,
        username: str = "",
        password: str = "",
    ) -> None:
        """
        select 驱动的双向字节流转发循环，直到任一侧断开或出现 OSError。

        客户端→目标方向：原样透传，同时剥离 IAC 后提取命令写审计日志。
        目标→客户端方向：原样透传（含 IAC 协商），同时收集初始 banner
            用于设备类型检测；检测触发后不再收集。

        Args:
            client_sock (socket.socket): 笔记本侧连接。
            target_sock (socket.socket): 目标 Telnet 服务连接。

        Returns:
            None。

        Raises:
            无，内部 OSError 均以 break 结束循环。

        Example:
            self._relay(client_conn, target_conn)
        """
        detect_buf = b""
        detected   = False
        command_gate = CommandGate(
            audit=self._audit,
            target_send=target_sock.sendall,
            client_send=client_sock.sendall,
            device_profile=self._device_profile,
            input_framing=self._input_framing,
        )
        if self._on_confirmation_ready:
            self._on_confirmation_ready(command_gate.confirm, command_gate.cancel_confirmation)
        auto_login = _TelnetAutoLogin(username, password, target_sock.sendall)

        while True:
            try:
                readable, _, _ = select.select(
                    [client_sock, target_sock], [], [], command_gate.poll_interval
                )
            except (ValueError, OSError):
                break

            # 笔记本 → 目标
            if client_sock in readable:
                try:
                    data = client_sock.recv(4096)
                except OSError:
                    break
                if not data:
                    break
                # 剥离 IAC 协商字节后交给命令闸门；Web 终端侧不会主动发起
                # Telnet 选项协商，保留纯用户输入更利于阻断决策。
                try:
                    command_gate.feed_transport(data if self._input_framing else _strip_iac(data))
                except OSError:
                    break

            # 目标 → 笔记本
            if target_sock in readable:
                try:
                    data = target_sock.recv(4096)
                except OSError:
                    break
                if not data:
                    break
                # 剥离 IAC 后再发给 pyte：pyte 只理解 VT100，不理解 Telnet 协议字节
                output = _strip_iac(data)
                try:
                    client_sock.sendall(output)
                except OSError:
                    break
                command_gate.feed_output(output)
                if auto_login.feed(output) == "password":
                    command_gate.mark_sensitive_input_complete()

                # 收集服务端初始输出（同样剥离 IAC），首次换行后触发设备类型检测
                if not detected and self._on_device_detected:
                    detect_buf += output
                    if b"\n" in detect_buf or len(detect_buf) >= DETECT_BYTES:
                        detected = True
                        dtype = detect_device(detect_buf.decode(errors="replace"))
                        log.info(
                            "[%s] Telnet 检测到设备类型: %s",
                            self._audit.session_id, dtype,
                        )
                        self._on_device_detected(dtype)

            try:
                command_gate.poll()
            except OSError:
                break

        log.info("[%s] Telnet relay 结束", self._audit.session_id)
