"""Shared long-lived objects for one running server."""

from __future__ import annotations

from dataclasses import dataclass, field

from .audit import AuditLog
from .config import Settings
from .models import ModelRouter
from .sessions import SessionStore
from .tools import Tool, discover_tools


@dataclass
class AppContext:
    settings: Settings
    llm: ModelRouter  # Ollama, .gguf files and API models, routed by model id.
    audit: AuditLog
    tools: dict[str, Tool]
    sessions: SessionStore = field(default_factory=SessionStore)
    approvals: dict = field(default_factory=dict)
    runs: dict = field(default_factory=dict)

    # Older name, kept so code and tests that say ctx.ollama still work.
    @property
    def ollama(self):
        return self.llm

    @ollama.setter
    def ollama(self, value) -> None:
        self.llm = value

    @classmethod
    def create(cls, settings: Settings) -> "AppContext":
        return cls(
            settings=settings,
            llm=ModelRouter(settings),
            audit=AuditLog(settings.logs_dir),
            tools=discover_tools(),
        )
