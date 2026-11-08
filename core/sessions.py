"""In-memory conversation state. Sessions last until the server restarts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

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
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def get(self, session_id: str) -> Session:
        if not SESSION_ID.match(session_id):
            raise ValueError("invalid session id")
        if session_id not in self._sessions:
            self._sessions[session_id] = Session(id=session_id)
        return self._sessions[session_id]

    def reset(self, session_id: str) -> Session:
        self._sessions.pop(session_id, None)
        return self.get(session_id)
