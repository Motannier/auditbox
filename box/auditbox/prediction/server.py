"""预测推送旁路服务器（TCP 9001）。

terminal_app 检测到握手响应中的 predict_port 后，另起 TCP 连接订阅：

  → {"session_id": "abc123"}\\n
  ← {"type": "prediction", "suggestions": ["cmd1", "cmd2"]}\\n  (每条命令后推送)

连接成立后保持长连接，由 PredictionEngine 负责推送，此处只处理初始握手。
"""

import json
import logging
import socket
import threading

from auditbox.env import AUDITBOX_PREDICT_PORT, AUDITBOX_PROXY_HOST

log = logging.getLogger(__name__)

PREDICT_PORT = AUDITBOX_PREDICT_PORT
PREDICT_HOST = AUDITBOX_PROXY_HOST


def start_predict_server(engine) -> int:
    """在后台线程启动预测推送服务器，返回实际监听端口。"""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((PREDICT_HOST, PREDICT_PORT))
    srv.listen(20)
    actual_port = srv.getsockname()[1]
    log.info("预测推送服务监听 %s:%d", PREDICT_HOST, actual_port)
    threading.Thread(target=_serve, args=(srv, engine), daemon=True).start()
    return actual_port


def _serve(srv: socket.socket, engine) -> None:
    while True:
        try:
            conn, addr = srv.accept()
            threading.Thread(target=_handle, args=(conn, addr, engine), daemon=True).start()
        except Exception as e:
            log.error("预测服务 accept 异常: %s", e)


def _handle(conn: socket.socket, addr, engine) -> None:
    """注册预测订阅，并在同一旁路连接上接收命令确认控制消息。"""
    session_id = ""
    try:
        buf = b""
        while b"\n" not in buf:
            chunk = conn.recv(256)
            if not chunk:
                conn.close()
                return
            buf += chunk
        req = json.loads(buf.split(b"\n", 1)[0])
        session_id = req.get("session_id", "")
        if not session_id:
            conn.close()
            return
        if not engine.add_subscriber(session_id, conn):
            log.warning("预测订阅失败: session=%s 不存在 from %s", session_id, addr)
            conn.close()
            return

        pending = buf.split(b"\n", 1)[1]
        while True:
            while b"\n" not in pending:
                chunk = conn.recv(1024)
                if not chunk:
                    return
                pending += chunk
            line, pending = pending.split(b"\n", 1)
            if not line.strip():
                continue
            payload = json.loads(line)
            if not engine.handle_control(session_id, payload):
                log.warning("[%s] 忽略无效的预测通道控制消息", session_id)
    except Exception as e:
        log.warning("预测订阅握手异常 %s: %s", addr, e)
    finally:
        if session_id:
            engine.remove_subscriber(session_id, conn)
        try:
            conn.close()
        except OSError:
            pass
