"""Append-only audit trail of every tool request, stored as JSON lines."""

from __future__ import annotations

import json
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from .redact import redact


class AuditLog:
    def __init__(self, logs_dir: Path):
        logs_dir.mkdir(parents=True, exist_ok=True)
        self.path = logs_dir / "audit.jsonl"
        self._lock = Lock()

    def record(
        self,
        *,
        session_id: str,
        agent: str,
        skill: str,
        model: str,
        operation: str,
        target: str,
        permission: str,
        approval: str,
        status: str,
        result: str,
    ) -> dict:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "session_id": session_id,
            "agent": agent,
            "skill": skill,
            "model": model,
            "operation": operation,
            "target": redact(target)[:500],
            "permission": permission,
            "approval": approval,
            "status": status,
            "result": redact(result)[:1000],
        }
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
        return entry

    def history(self, session_id: str | None = None, limit: int = 200) -> list[dict]:
        if not self.path.exists():
            return []
        matches: deque[dict] = deque(maxlen=limit)
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if session_id is None or entry.get("session_id") == session_id:
                    matches.append(entry)
        return list(matches)
