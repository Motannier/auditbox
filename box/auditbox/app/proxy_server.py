"""
代理服务主入口
- 握手协议：第一行 JSON 描述连接参数，之后切换为透明字节流
- 按 protocol 字段分发到对应代理模块（当前实现 SSH）
"""

import json
import logging
import secrets
import socket
import threading

from auditbox.audit.logger import AuditLogger
from auditbox.audit.store import AuditStore, safe_audit
from auditbox.device_profiles import require_device_profile
from auditbox.env import (
    AUDITBOX_ENABLE_MARKOV_RECOMMENDATIONS,
    AUDITBOX_PORT,
    AUDITBOX_PROXY_HOST,
)
from auditbox.prediction.engine import PredictionEngine
from auditbox.prediction.server import start_predict_server
from auditbox.relay.console_proxy import ConsoleConnectionError, ConsoleProxy
from auditbox.relay.ssh_proxy import SSHConnectionError, SSHProxy
from auditbox.relay.telnet_proxy import TelnetProxy
from auditbox.relay.input_frames import INPUT_FRAMING

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

HOST = AUDITBOX_PROXY_HOST
PORT = AUDITBOX_PORT

_engine = PredictionEngine(enable_markov=AUDITBOX_ENABLE_MARKOV_RECOMMENDATIONS)
_predict_port: int = 0   # 启动后赋值


# ── 握手协议 ──────────────────────────────────────────────────────────────────
#
#  笔记本 → 盒子  {"protocol":"ssh","host":"...","port":22,"username":"...","password":"..."}
#  盒子 → 笔记本  {"status":"ok","session_id":"..."}   或   {"status":"error","message":"..."}
#  之后           原始输入，或握手协商的 source-jsonl-v1 输入帧；输出保持透明字节流

def send_json(sock: socket.socket, obj: dict) -> None:
    sock.sendall((json.dumps(obj) + "\n").encode())


def recv_json(sock: socket.socket) -> dict:
    buf = b""
    while b"\n" not in buf:
        chunk = sock.recv(1024)
        if not chunk:
            raise ConnectionError("客户端在握手阶段断开")
        buf += chunk
    return json.loads(buf.split(b"\n", 1)[0])


# ── 单连接处理 ────────────────────────────────────────────────────────────────

def handle_client(conn: socket.socket, addr: tuple) -> None:
    session_id = secrets.token_hex(6)
    log.info("[%s] 新连接来自 %s:%d", session_id, *addr)

    audit = None
    handshake_finished = False
    input_framing = False

    def finish_handshake(payload: dict) -> None:
        nonlocal handshake_finished
        if input_framing and payload.get("status") == "ok":
            payload["input_framing"] = INPUT_FRAMING
        send_json(conn, payload)
        handshake_finished = True

    try:
        params   = recv_json(conn)
        input_framing = params.get("input_framing") == INPUT_FRAMING
        protocol = params.get("protocol", "").lower()
        device_type = str(params.get("device_type") or "").strip().lower()
        device_profile = str(params.get("device_profile") or "").strip().lower()
        try:
            profile = require_device_profile(device_type, device_profile)
        except ValueError as exc:
            finish_handshake({
                "status": "error",
                "code": "invalid_device_type" if not device_type else "invalid_device_profile",
                "message": str(exc),
            })
            return
        target = (
            f"{params.get('host', '?')} @ {params.get('port', '?')} baud"
            if protocol == "console"
            else f"{params.get('host', '?')}:{params.get('port', '?')}"
        )
        operator = str(params.get("operator") or "未登录").strip()[:255] or "未登录"

        target_key = json.dumps(
            [protocol, str(params.get("host") or ""), int(params.get("port") or 0)],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        _engine.register_session(
            session_id,
            protocol=protocol,
            target_key=target_key,
            device_type=device_type,
            device_profile=profile.key,
        )

        audit = AuditLogger(
            session_id=session_id,
            target=target,
            protocol=protocol,
            account=operator,
            login_id=str(params.get("login_id") or ""),
            device_id=str(params.get("device_id") or ""),
            device_name=str(params.get("device_name") or ""),
            device_profile=profile.key,
            command_hook=lambda cmd, metadata: _engine.on_command(
                session_id,
                cmd,
                metadata=metadata,
            ),
            security_hook=lambda cmd, alert, blocked: _engine.on_security_alert(
                session_id, cmd, alert, blocked
            ),
            command_result_hook=lambda cmd, success: _engine.on_command_result(
                session_id,
                cmd,
                success=success,
            ),
        )
        _engine.register_audit_handler(session_id, audit.log_operation)

        on_detected = lambda dtype: _engine.update_device_type(session_id, dtype)
        on_confirmation_ready = lambda confirm, cancel: _engine.register_confirmation_handler(
            session_id,
            confirm,
            cancel,
        )

        if protocol == "ssh":
            SSHProxy(
                audit,
                on_device_detected=on_detected,
                on_confirmation_ready=on_confirmation_ready,
                device_profile=profile.key,
                input_framing=input_framing,
            ).connect_and_relay(
                conn,
                params,
                on_ready=lambda: finish_handshake({
                    "status":       "ok",
                    "session_id":   session_id,
                    "predict_port": _predict_port,
                }),
            )

        elif protocol == "telnet":
            finish_handshake({
                "status":       "ok",
                "session_id":   session_id,
                "predict_port": _predict_port,
            })
            TelnetProxy(
                audit,
                on_device_detected=on_detected,
                on_confirmation_ready=on_confirmation_ready,
                device_profile=profile.key,
                input_framing=input_framing,
            ).connect_and_relay(conn, params)

        elif protocol == "console":
            ConsoleProxy(
                audit,
                on_device_detected=on_detected,
                on_confirmation_ready=on_confirmation_ready,
                device_profile=profile.key,
                input_framing=input_framing,
            ).connect_and_relay(
                conn,
                params,
                on_ready=lambda: finish_handshake({
                    "status":       "ok",
                    "session_id":   session_id,
                    "predict_port": _predict_port,
                }),
            )

        else:
            finish_handshake({"status": "error", "message": f"不支持的协议: {protocol}"})
            log.warning("[%s] 不支持的协议: %s", session_id, protocol)

    except (ConnectionError, json.JSONDecodeError) as e:
        log.warning("[%s] 握手失败: %s", session_id, e)
    except SSHConnectionError as e:
        log.warning("[%s] SSH 建连失败: %s", session_id, e)
        if not handshake_finished:
            try:
                finish_handshake({
                    "status": "error",
                    "code": e.code,
                    "message": e.message,
                })
            except Exception:
                pass
    except ConsoleConnectionError as e:
        log.warning("[%s] Console 建连失败: %s", session_id, e)
        if not handshake_finished:
            try:
                finish_handshake({
                    "status": "error",
                    "code": e.code,
                    "message": e.message,
                })
            except Exception:
                pass
    except Exception as e:
        log.error("[%s] 会话异常: %s", session_id, e)
        if not handshake_finished:
            try:
                finish_handshake({
                    "status": "error",
                    "code": "connection_failed",
                    "message": "目标连接失败，请检查地址、端口和网络",
                })
            except Exception:
                pass
    finally:
        _engine.unregister_session(session_id)
        if audit:
            try:
                audit.close()
            except Exception:
                pass
        conn.close()
        log.info("[%s] 连接已关闭", session_id)


# ── 主服务循环 ────────────────────────────────────────────────────────────────

def main() -> None:
    global _predict_port
    safe_audit(lambda: AuditStore().recover_interrupted())
    _predict_port = start_predict_server(_engine)

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen(5)
    log.info("代理服务监听 %s:%d", HOST, PORT)

    try:
        while True:
            conn, addr = server.accept()
            threading.Thread(
                target=handle_client, args=(conn, addr), daemon=True
            ).start()
    except KeyboardInterrupt:
        log.info("代理服务停止")
    finally:
        server.close()


if __name__ == "__main__":
    main()
