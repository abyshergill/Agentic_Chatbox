"""SQLite storage for things that should survive a restart: past conversations and the folders the user opened.

One file, data/workspace.db. Everything stays on this machine.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    id             TEXT PRIMARY KEY,
    title          TEXT NOT NULL DEFAULT '',
    created        REAL NOT NULL,
    updated        REAL NOT NULL,
    pinned_command TEXT,
    active_command TEXT,
    messages       TEXT NOT NULL DEFAULT '[]',
    action_log     TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS chats_updated ON chats(updated DESC);

CREATE TABLE IF NOT EXISTS folders (
    path  TEXT PRIMARY KEY,
    added REAL NOT NULL
);
"""

TITLE_LENGTH = 70


def title_from(messages: list[dict]) -> str:
    """A conversation's default name: its first message, on one line."""
    first = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
    text = " ".join(str(first).split())
    return text if len(text) <= TITLE_LENGTH else text[: TITLE_LENGTH - 1].rstrip() + "…"


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)

    def _run(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _change(self, sql: str, params: tuple = ()) -> bool:
        """Runs an UPDATE or DELETE; True if a row was affected."""
        with self._lock:
            return self._conn.execute(sql, params).rowcount > 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- chats ----------------------------------------------------------------

    def save_chat(self, chat_id: str, messages: list[dict], action_log: list[str],
                  pinned_command: str | None, active_command: str | None) -> None:
        """Creates or updates a conversation. A conversation with no messages is not stored."""
        if not messages:
            return
        now = time.time()
        self._run(
            """INSERT INTO chats (id, title, created, updated, pinned_command, active_command, messages, action_log)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 title = CASE WHEN chats.title = '' THEN excluded.title ELSE chats.title END,
                 updated = excluded.updated, pinned_command = excluded.pinned_command,
                 active_command = excluded.active_command, messages = excluded.messages,
                 action_log = excluded.action_log""",
            (chat_id, title_from(messages), now, now, pinned_command, active_command,
             json.dumps(messages), json.dumps(action_log[-200:])),
        )

    def load_chat(self, chat_id: str) -> dict | None:
        rows = self._run("SELECT * FROM chats WHERE id = ?", (chat_id,))
        if not rows:
            return None
        row = rows[0]
        return {
            "id": row["id"], "title": row["title"], "created": row["created"], "updated": row["updated"],
            "pinned_command": row["pinned_command"], "active_command": row["active_command"],
            "messages": json.loads(row["messages"]), "action_log": json.loads(row["action_log"]),
        }

    def list_chats(self, limit: int = 200, query: str = "") -> list[dict]:
        sql = "SELECT id, title, created, updated, messages FROM chats"
        params: tuple = ()
        if query:
            sql += " WHERE title LIKE ? OR messages LIKE ?"
            like = f"%{query}%"
            params = (like, like)
        sql += " ORDER BY updated DESC LIMIT ?"
        rows = self._run(sql, (*params, limit))
        return [
            {"id": r["id"], "title": r["title"] or "Untitled", "created": r["created"], "updated": r["updated"],
             "messages": len(json.loads(r["messages"]))}
            for r in rows
        ]

    def rename_chat(self, chat_id: str, title: str) -> bool:
        return self._change("UPDATE chats SET title = ? WHERE id = ?", (title.strip(), chat_id))

    def delete_chat(self, chat_id: str) -> bool:
        return self._change("DELETE FROM chats WHERE id = ?", (chat_id,))

    # ---- folders the user opened ----------------------------------------------

    def folders(self) -> list[Path]:
        return [Path(r["path"]) for r in self._run("SELECT path FROM folders ORDER BY added")]

    def add_folder(self, path: Path) -> None:
        self._run("INSERT OR IGNORE INTO folders (path, added) VALUES (?, ?)", (str(path), time.time()))

    def remove_folder(self, path: Path) -> bool:
        return self._change("DELETE FROM folders WHERE path = ?", (str(path),))
