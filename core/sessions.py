"""Conversation state. Live sessions are kept in memory; their messages are saved to SQLite after every turn, so
past conversations can be reopened from History after a restart."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .storage import Database
from .uploads import Attachment

SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


@dataclass
class Session:
    id: str
    messages: list[dict] = field(default_factory=list)
    pinned_command: str | None = None  # Set by an explicit /command; cleared by /auto.
    active_command: str | None = None  # What the last run used; drives the file tree.
    attachments: dict[str, Attachment] = field(default_factory=dict)
    action_log: list[str] = field(default_factory=list)  # Backend-confirmed outcomes only.


class SessionStore:
    def __init__(self, db: Database | None = None) -> None:
        self._sessions: dict[str, Session] = {}
        self.db = db

    def get(self, session_id: str) -> Session:
        if not SESSION_ID.match(session_id):
            raise ValueError("invalid session id")
        if session_id not in self._sessions:
            session = Session(id=session_id)
            saved = self.db.load_chat(session_id) if self.db is not None else None
            if saved is not None:
                session.messages = saved["messages"]
                session.action_log = saved["action_log"]
                session.pinned_command = saved["pinned_command"]
                session.active_command = saved["active_command"]
            self._sessions[session_id] = session
        return self._sessions[session_id]

    def save(self, session: Session) -> None:
        if self.db is not None:
            self.db.save_chat(session.id, session.messages, session.action_log,
                              session.pinned_command, session.active_command)

    def reset(self, session_id: str) -> Session:
        """Clears a conversation, in memory and on disk."""
        self._sessions.pop(session_id, None)
        if self.db is not None:
            self.db.delete_chat(session_id)
        return self.get(session_id)

    def forget(self, session_id: str) -> None:
        """Drops a conversation from memory and disk (History > Delete)."""
        self._sessions.pop(session_id, None)
        if self.db is not None:
            self.db.delete_chat(session_id)
