"""Shared long-lived objects for one running server."""

from __future__ import annotations

from dataclasses import dataclass, field

from .agent_skills import SkillLibrary
from .audit import AuditLog
from .config import Settings
from .downloads import Downloads
from .mcp_hub import McpHub
from .models import ModelRouter
from .preferences import Preferences
from .sessions import SessionStore
from .storage import Database
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
    db: Database | None = None
    mcp: McpHub | None = None  # Connectors: MCP servers and their tools.
    skills: SkillLibrary | None = None  # Agent Skills in skills/.
    preferences: Preferences | None = None
    downloads: Downloads | None = None  # .gguf downloads and Ollama pulls started from Settings > Models.

    def all_tools(self) -> dict[str, Tool]:
        """Built-in tools plus the tools of every connected MCP server."""
        if self.mcp is None:
            return self.tools
        return {**self.mcp.tools(), **self.tools}

    # Older name, kept so code and tests that say ctx.ollama still work.
    @property
    def ollama(self):
        return self.llm

    @ollama.setter
    def ollama(self, value) -> None:
        self.llm = value

    @classmethod
    def create(cls, settings: Settings) -> "AppContext":
        # The default project area every skill may use. Created on first run, so a fresh checkout has somewhere to work.
        (settings.workspace_root / "projects").mkdir(parents=True, exist_ok=True)
        db = Database(settings.db_path)
        settings.extra_roots = [path for path in db.folders() if path.is_dir()]
        return cls(
            settings=settings,
            llm=ModelRouter(settings),
            audit=AuditLog(settings.logs_dir),
            tools=discover_tools(),
            sessions=SessionStore(db),
            db=db,
            mcp=McpHub(settings),
            skills=SkillLibrary(settings.skills_dir, settings.data_dir / "skills.json"),
            preferences=Preferences(settings.data_dir / "preferences.json"),
            downloads=Downloads(settings),
        )
