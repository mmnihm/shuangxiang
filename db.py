"""SQLite 存储：用户台账 + 转发映射 + 运行时管理员 + 限流时间戳。

数据量小（万级用户），主线程同步读写，不引入额外依赖；开启 WAL 让频繁写不阻塞读。
路径不可写时依次退到临时目录与内存库，保证机器人不会因为存储问题整条链路报错。
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
from datetime import datetime, timezone
from typing import Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id    INTEGER PRIMARY KEY,
    username   TEXT,
    first_name TEXT,
    created_at TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    blocked    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS forward_links (
    admin_chat_id     INTEGER NOT NULL,
    admin_message_id  INTEGER NOT NULL,
    user_id           INTEGER NOT NULL,
    created_at        TEXT NOT NULL,
    PRIMARY KEY (admin_chat_id, admin_message_id)
);

CREATE INDEX IF NOT EXISTS idx_forward_user ON forward_links (user_id);

CREATE TABLE IF NOT EXISTS admins (
    user_id    INTEGER PRIMARY KEY,
    note       TEXT,
    added_by   INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bot_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str) -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------- 用户 ----------

    def upsert_user(
        self, user_id: int, username: Optional[str], first_name: Optional[str]
    ) -> bool:
        """记录/更新用户；返回是否是第一次出现。"""
        now = _now()
        with self._lock:
            existed = self._conn.execute(
                "SELECT 1 FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            self._conn.execute(
                """
                INSERT INTO users (user_id, username, first_name, created_at, last_seen, blocked)
                VALUES (?, ?, ?, ?, ?, 0)
                ON CONFLICT(user_id) DO UPDATE SET
                    username   = excluded.username,
                    first_name = excluded.first_name,
                    last_seen  = excluded.last_seen
                """,
                (user_id, username, first_name, now, now),
            )
            self._conn.commit()
        return existed is None

    def touch_user(self, user_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE users SET last_seen = ? WHERE user_id = ?", (_now(), user_id)
            )
            self._conn.commit()

    def mark_blocked(self, user_id: int, blocked: bool = True) -> bool:
        """拉黑/解封；用户不存在时返回 False。"""
        with self._lock:
            cursor = self._conn.execute(
                "UPDATE users SET blocked = ? WHERE user_id = ?",
                (1 if blocked else 0, user_id),
            )
            self._conn.commit()
        return cursor.rowcount > 0

    def is_blocked(self, user_id: int) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT blocked FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
        return bool(row and row[0])

    def active_user_ids(self) -> list[int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT user_id FROM users WHERE blocked = 0 ORDER BY user_id"
            ).fetchall()
        return [row[0] for row in rows]

    def statistics(self) -> dict[str, int]:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            blocked = self._conn.execute(
                "SELECT COUNT(*) FROM users WHERE blocked = 1"
            ).fetchone()[0]
            links = self._conn.execute("SELECT COUNT(*) FROM forward_links").fetchone()[0]
        return {"total": total, "blocked": blocked, "active": total - blocked, "links": links}

    # ---------- 转发映射：管理员侧消息 → 用户 ----------

    def save_link(self, admin_chat_id: int, admin_message_id: int, user_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO forward_links "
                "(admin_chat_id, admin_message_id, user_id, created_at) VALUES (?, ?, ?, ?)",
                (admin_chat_id, admin_message_id, user_id, _now()),
            )
            self._conn.commit()

    def get_link_user(self, admin_chat_id: int, admin_message_id: int) -> Optional[int]:
        with self._lock:
            row = self._conn.execute(
                "SELECT user_id FROM forward_links WHERE admin_chat_id = ? AND admin_message_id = ?",
                (admin_chat_id, admin_message_id),
            ).fetchone()
        return row[0] if row else None

    def prune_links(self, keep: int = 50000) -> None:
        """只保留最近 N 条映射，避免表无限增长。"""
        with self._lock:
            self._conn.execute(
                "DELETE FROM forward_links WHERE rowid NOT IN "
                "(SELECT rowid FROM forward_links ORDER BY rowid DESC LIMIT ?)",
                (keep,),
            )
            self._conn.commit()

    # ---------- 运行时管理员 ----------

    def add_admin(self, user_id: int, note: str = "", added_by: int = 0) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO admins (user_id, note, added_by, created_at) VALUES (?, ?, ?, ?)",
                (user_id, note, added_by, _now()),
            )
            self._conn.commit()
        return cursor.rowcount > 0

    def remove_admin(self, user_id: int) -> bool:
        with self._lock:
            cursor = self._conn.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
            self._conn.commit()
        return cursor.rowcount > 0

    def list_admins(self) -> list[int]:
        with self._lock:
            rows = self._conn.execute("SELECT user_id FROM admins ORDER BY created_at").fetchall()
        return [row[0] for row in rows]

    # ---------- 运行时设置 / 限流 ----------

    def get_setting(self, key: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM bot_settings WHERE key = ?", (key,)
            ).fetchone()
        return row[0] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO bot_settings (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (key, value, _now()),
            )
            self._conn.commit()

    def seconds_since_last_message(self, user_id: int) -> float:
        """返回距上次发言的秒数；用户不存在时返回 0。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT last_seen FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
        if not row:
            return 0.0
        try:
            last = datetime.fromisoformat(row[0])
        except ValueError:
            return 0.0
        return (datetime.now(timezone.utc) - last).total_seconds()


def _open_sqlite(preferred: str) -> Database:
    """按顺序尝试可用目录：原路径 → 临时目录 → 内存。"""
    candidates = [
        preferred,
        os.path.join(tempfile.gettempdir(), "shuangxiang-bot.db"),
        ":memory:",
    ]
    last: Exception | None = None
    for path in candidates:
        try:
            store = Database(path)
        except Exception as exc:  # noqa: BLE001 - 逐个兜底
            last = exc
            print(f"[db] 打开 {path} 失败：{exc}", flush=True)
            continue
        if path != preferred:
            print(f"[db] 已在用 {path}（{preferred} 不可写，重启后数据会丢）", flush=True)
        return store
    raise RuntimeError(f"没有可用的数据库路径：{last}")


_db: Database | None = None


def get_db() -> Database:
    global _db
    if _db is None:
        from config import get_settings

        _db = _open_sqlite(get_settings().db_path)
    return _db
