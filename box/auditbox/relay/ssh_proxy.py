"""
SSH 代理模块
- 连接笔记本 TCP socket 与目标 SSH 服务
- 双向透明转发，同时旁路审计输入命令
"""

import select
import logging
import socket
import paramiko

from auditbox.audit.logger import AuditLogger
from auditbox.prediction.detector import detect as detect_device, DETECT_BYTES
from auditbox.relay.command_gate import CommandGate
from auditbox.relay.socket_health import enable_fast_disconnect_detection

log = logging.getLogger(__name__)


class SSHConnectionError(Exception):
    """A safe, structured SSH setup error that can be returned to the browser."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class SSHProxy:
    def __init__(
        self,
        audit: AuditLogger,
        on_device_detected=None,
        on_confirmation_ready=None,
        device_profile: str = "linux",
        input_framing: bool = False,
    ):
        self._audit = audit
        self._on_device_detected = on_device_detected   # callable(device_type: str) | None
        self._on_confirmation_ready = on_confirmation_ready
        self._device_profile = device_profile
        self._input_framing = input_framing

    def connect_and_relay(
        self,
        client_sock: socket.socket,
        params: dict,
        on_ready=None,
    ) -> None:
        host     = params["host"]
        port     = params.get("port", 22)
        username = params["username"]
        password = params.get("password")
        key_path = params.get("key_path")
        cols     = params.get("cols", 220)
        rows     = params.get("rows", 50)

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            connect_kwargs = dict(
                hostname=host,
                port=port,
                username=username,
                timeout=10,
                banner_timeout=10,
                auth_timeout=10,
                look_for_keys=False,
                allow_agent=False,
            )
            if key_path:
                connect_kwargs["key_filename"] = key_path
            else:
                connect_kwargs["password"] = password

            log.info("[%s] SSH 连接 %s@%s:%d", self._audit.session_id, username, host, port)
            ssh.connect(**connect_kwargs)

            transport = ssh.get_transport()
            if transport:
                enable_fast_disconnect_detection(transport.sock)
                transport.set_keepalive(2)

            channel = ssh.invoke_shell(term="xterm", width=cols, height=rows)
            channel.setblocking(False)
            log.info("[%s] SSH shell 已建立", self._audit.session_id)

            # The proxy handshake must not report success until both SSH
            # authentication and shell creation have completed.
            if on_ready:
                on_ready()

            self._relay(client_sock, channel)

        except paramiko.AuthenticationException as e:
            log.warning("[%s] SSH 认证失败: %s", self._audit.session_id, e)
            raise SSHConnectionError(
                "auth_failed",
                "SSH 用户名或密码错误，请重新输入",
            ) from e
        except (socket.timeout, TimeoutError) as e:
            log.warning("[%s] SSH 连接超时: %s", self._audit.session_id, e)
            raise SSHConnectionError(
                "connection_timeout",
                "SSH 连接超时，请检查目标地址、端口和网络",
            ) from e
        except paramiko.BadHostKeyException as e:
            log.warning("[%s] SSH 主机密钥校验失败: %s", self._audit.session_id, e)
            raise SSHConnectionError(
                "host_key_failed",
                "SSH 主机密钥校验失败",
            ) from e
        except paramiko.SSHException as e:
            log.warning("[%s] SSH 协议握手失败: %s", self._audit.session_id, e)
            raise SSHConnectionError(
                "ssh_handshake_failed",
                "SSH 协议握手失败，请检查目标 SSH 服务",
            ) from e
        except OSError as e:
            log.warning("[%s] SSH 网络连接失败: %s", self._audit.session_id, e)
            raise SSHConnectionError(
                "network_error",
                "无法连接 SSH 目标，请检查目标地址、端口和网络",
            ) from e
        except Exception as e:
            log.error("[%s] SSH 连接异常: %s", self._audit.session_id, e)
            raise
        finally:
            try:
                ssh.close()
            except Exception:
                pass

    def _relay(self, client_sock: socket.socket, channel: paramiko.Channel) -> None:
        """双向转发，输入方向旁路提取命令，输出方向旁路检测设备类型。"""
        detect_buf = b""
        detected   = False
        command_gate = CommandGate(
            audit=self._audit,
            target_send=channel.sendall,
            client_send=client_sock.sendall,
            device_profile=self._device_profile,
            input_framing=self._input_framing,
        )
        if self._on_confirmation_ready:
            self._on_confirmation_ready(command_gate.confirm, command_gate.cancel_confirmation)

        while True:
            transport = channel.get_transport()
            if channel.closed or not transport or not transport.is_active():
                log.info("[%s] SSH 目标连接已失效", self._audit.session_id)
                break
            try:
                readable, _, _ = select.select([client_sock, channel], [], [], command_gate.poll_interval)
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
                try:
                    command_gate.feed_transport(data)
                except OSError:
                    break

            # 目标 → 笔记本
            if channel in readable:
                if channel.recv_ready():
                    data = channel.recv(4096)
                    if not data:
                        break
                    try:
                        client_sock.sendall(data)
                    except OSError:
                        break
                    command_gate.feed_output(data)

                    # 设备类型检测：收集初始输出，首次出现换行且够长后触发
                    if not detected and self._on_device_detected:
                        detect_buf += data
                        # 收到首个换行立即检测（远端 banner 总在用户输入前到达）
                        if b"\n" in detect_buf or len(detect_buf) >= DETECT_BYTES:
                            detected = True
                            dtype = detect_device(detect_buf.decode(errors="replace"))
                            log.info("[%s] 检测到设备类型: %s", self._audit.session_id, dtype)
                            self._on_device_detected(dtype)

            try:
                command_gate.poll()
            except OSError:
                break

            if channel.exit_status_ready():
                log.info("[%s] SSH 远端关闭连接", self._audit.session_id)
                break

        log.info("[%s] relay 结束", self._audit.session_id)
