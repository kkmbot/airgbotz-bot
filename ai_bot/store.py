"""SQLite storage: allowlist, per-chat memory, running summary, usage counters, small meta values."""
from __future__ import annotations

import os
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS allowed_users (user_id INTEGER PRIMARY KEY, added_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, id);
CREATE TABLE IF NOT EXISTS summaries (chat_id INTEGER PRIMARY KEY, summary TEXT NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS usage (
    day TEXT NOT NULL, provider TEXT NOT NULL, kind TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, provider, kind)
);
CREATE TABLE IF NOT EXISTS user_daily (
    day TEXT NOT NULL, user_id INTEGER NOT NULL, count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, user_id)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class Store:
    def __init__(self, path: str = ":memory:") -> None:
        if path != ":memory:":
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    # ---- low level -------------------------------------------------------
    def _run(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self._db.execute(sql, params)
            self._db.commit()

    def _rows(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, params).fetchall()]

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---- allowlist -------------------------------------------------------
    def allow(self, user_id: int) -> None:
        self._run("INSERT OR REPLACE INTO allowed_users(user_id, added_at) VALUES (?, ?)", (user_id, time.time()))

    def deny(self, user_id: int) -> None:
        self._run("DELETE FROM allowed_users WHERE user_id = ?", (user_id,))

    def is_allowed(self, user_id: int) -> bool:
        return bool(self._rows("SELECT 1 FROM allowed_users WHERE user_id = ?", (user_id,)))

    def allowed_ids(self) -> list[int]:
        return [r["user_id"] for r in self._rows("SELECT user_id FROM allowed_users ORDER BY user_id")]

    # ---- chat memory -----------------------------------------------------
    def add_message(self, chat_id: int, role: str, content: str) -> None:
        self._run("INSERT INTO messages(chat_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                  (chat_id, role, content, time.time()))

    def recent(self, chat_id: int, limit: int) -> list[dict]:
        rows = self._rows(
            "SELECT id, role, content FROM messages WHERE chat_id = ? ORDER BY id DESC LIMIT ?", (chat_id, limit))
        return list(reversed(rows))

    def count_messages(self, chat_id: int) -> int:
        return self._rows("SELECT COUNT(*) AS n FROM messages WHERE chat_id = ?", (chat_id,))[0]["n"]

    def oldest(self, chat_id: int, n: int) -> list[dict]:
        return self._rows(
            "SELECT id, role, content FROM messages WHERE chat_id = ? ORDER BY id ASC LIMIT ?", (chat_id, n))

    def delete_messages_upto(self, chat_id: int, max_id: int) -> None:
        self._run("DELETE FROM messages WHERE chat_id = ? AND id <= ?", (chat_id, max_id))

    def get_summary(self, chat_id: int) -> str:
        rows = self._rows("SELECT summary FROM summaries WHERE chat_id = ?", (chat_id,))
        return rows[0]["summary"] if rows else ""

    def set_summary(self, chat_id: int, summary: str) -> None:
        self._run("INSERT OR REPLACE INTO summaries(chat_id, summary, updated_at) VALUES (?, ?, ?)",
                  (chat_id, summary, time.time()))

    def reset_chat(self, chat_id: int) -> None:
        self._run("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
        self._run("DELETE FROM summaries WHERE chat_id = ?", (chat_id,))

    # ---- usage -----------------------------------------------------------
    def bump_usage(self, day: str, provider: str, kind: str) -> None:
        self._run(
            "INSERT INTO usage(day, provider, kind, count) VALUES (?, ?, ?, 1) "
            "ON CONFLICT(day, provider, kind) DO UPDATE SET count = count + 1",
            (day, provider, kind))

    def usage_for_day(self, day: str) -> list[dict]:
        return self._rows("SELECT provider, kind, count FROM usage WHERE day = ? ORDER BY provider, kind", (day,))

    def get_user_day(self, day: str, user_id: int) -> int:
        rows = self._rows("SELECT count FROM user_daily WHERE day = ? AND user_id = ?", (day, user_id))
        return rows[0]["count"] if rows else 0

    def bump_user_day(self, day: str, user_id: int) -> None:
        self._run(
            "INSERT INTO user_daily(day, user_id, count) VALUES (?, ?, 1) "
            "ON CONFLICT(day, user_id) DO UPDATE SET count = count + 1",
            (day, user_id))

    # ---- meta ------------------------------------------------------------
    def set_meta(self, key: str, value: str) -> None:
        self._run("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        rows = self._rows("SELECT value FROM meta WHERE key = ?", (key,))
        return rows[0]["value"] if rows else default
