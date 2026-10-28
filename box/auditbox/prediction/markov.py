"""马尔可夫链统计预测：基于命令转移频率预测下一条命令。

转移表按 device_type 命名空间隔离（ssh / telnet / serial），
避免不同协议/厂商的命令互相污染。

数据持久化到 SQLite，支持跨会话积累经验。
同时支持 bigram（前1条命令）和 trigram（前2条命令联合）上下文，
预测时优先使用 trigram，无结果则 fallback 到 bigram。
"""

import sqlite3
import threading
from pathlib import Path

from auditbox.db import DB_PATH

TOP_N = 5


class MarkovPredictor:
    def __init__(self, db_path: str = DB_PATH):
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS transitions (
                device_type TEXT NOT NULL,
                ctx_key     TEXT NOT NULL,
                next_cmd    TEXT NOT NULL,
                count       INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (device_type, ctx_key, next_cmd)
            )
        """)
        self._db.commit()

    def learn(self, device_type: str, history: list, new_cmd: str) -> None:
        """记录命令转移：history[-1] → new_cmd（bigram），history[-2:] → new_cmd（trigram）。"""
        if not new_cmd.strip():
            return
        keys = self._ctx_keys(history)
        with self._lock:
            for key in keys:
                self._db.execute("""
                    INSERT INTO transitions (device_type, ctx_key, next_cmd, count)
                    VALUES (?, ?, ?, 1)
                    ON CONFLICT(device_type, ctx_key, next_cmd)
                    DO UPDATE SET count = count + 1
                """, (device_type, key, new_cmd))
            self._db.commit()

    def predict(self, device_type: str, history: list) -> list:
        """返回最多 TOP_N 条预测命令，优先 trigram，fallback bigram。"""
        if not history:
            return []
        if len(history) >= 2:
            result = self._query(device_type, f"{history[-2]} > {history[-1]}")
            if result:
                return result
        return self._query(device_type, history[-1])

    def _ctx_keys(self, history: list) -> list:
        keys = []
        if history:
            keys.append(history[-1])
        if len(history) >= 2:
            keys.append(f"{history[-2]} > {history[-1]}")
        return keys

    def _query(self, device_type: str, ctx_key: str) -> list:
        with self._lock:
            rows = self._db.execute("""
                SELECT next_cmd FROM transitions
                WHERE device_type = ? AND ctx_key = ?
                ORDER BY count DESC
                LIMIT ?
            """, (device_type, ctx_key, TOP_N)).fetchall()
        return [r[0] for r in rows]

    def close(self) -> None:
        self._db.close()
