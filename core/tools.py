"""Tool model and registry. Applications plug in through core/adapters/.

To add an integration, drop a module in core/adapters/ that defines
`adapter = Adapter(app_id=..., tools=[...])` and add a matching #block to
arch/permission.md. Nothing in the agent engine changes.
"""

from __future__ import annotations

import importlib
import json
import pkgutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from .config import Settings
from .fs_guard import FsGuard
from .permissions import PermissionManager
from .process import ProcessResult
from .redact import redact

if TYPE_CHECKING:
    from .agent import AgentRun
    from .sessions import Session


class ToolError(Exception):
    """An expected failure. The message goes back to the model as the tool result."""


@dataclass
class ToolResult:
    ok: bool
    output: str
    changed_paths: list[str] = field(default_factory=list)

    @classmethod
    def from_process(cls, result: ProcessResult) -> "ToolResult":
        parts = []
        if result.timed_out:
            parts.append("TIMED OUT: the process was killed.")
        else:
            parts.append(f"exit code: {result.exit_code}")
        if result.stdout:
            parts.append(f"stdout:\n{result.stdout}")
        if result.stderr:
            parts.append(f"stderr:\n{result.stderr}")
        if result.truncated:
            parts.append("(output truncated)")
        return cls(ok=result.ok, output="\n".join(parts))


@dataclass
class ToolContext:
    settings: Settings
    permissions: PermissionManager
    skill_paths: list[str]
    session: "Session"
    run: "AgentRun | None" = None
    active_path: str | None = None
    activation: Any = None  # The active skill, agent and model (core.agent.Activation), when run by the agent.

    @property
    def guard(self) -> FsGuard:
        # Rebuilt on each access, so every use sees the current permission.md.
        self.permissions.reload()
        return FsGuard(self.settings, self.permissions, self.skill_paths, base=self.active_path)

    def output_dir(self) -> Path:
        """The folder generated files go to when no other place is given: the user's selected folder, else the first
        folder this skill may use."""
        guard = self.guard
        if self.active_path:
            path = guard.resolve(self.active_path)
            return path if path.is_dir() else path.parent
        if not guard.roots:
            raise ToolError("there is no folder to save into; ask the user to select or open one")
        return guard.roots[0]

    def resolve_workdir(self, value: str | None) -> Path:
        guard = self.guard
        path = guard.resolve(value or self.active_path)
        return path if path.is_dir() else path.parent


Handler = Callable[[ToolContext, dict], Awaitable[ToolResult]]
Check = Callable[[ToolContext, dict], None]
Preview = Callable[[ToolContext, dict], str]


@dataclass
class Tool:
    name: str
    app: str
    operation: str
    description: str
    parameters: dict[str, dict]
    handler: Handler
    required: list[str] = field(default_factory=list)
    destructive: bool = False
    precheck: Check | None = None  # Runs before approval, so doomed calls are not put to the user.
    preview: Preview | None = None
    # If set, only offered when the user names the application. Keeps the list small enough
    # for small models, which otherwise "open" a file in VS Code when they mean to read it.
    only_if_mentioned: tuple[str, ...] = ()

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {"type": "object", "properties": self.parameters, "required": self.required},
            },
        }

    def clean_arguments(self, raw: Any) -> dict:
        """Validates model-supplied arguments against this tool's declared parameters."""
        if not isinstance(raw, dict):
            raise ToolError("arguments must be a JSON object")
        cleaned: dict = {}
        for key, spec in self.parameters.items():
            if key in raw and raw[key] is not None:
                cleaned[key] = _coerce(key, raw[key], spec.get("type", "string"))
        missing = [key for key in self.required if key not in cleaned]
        if missing:
            raise ToolError(f"missing required argument(s): {', '.join(missing)}")
        return cleaned

    def target(self, arguments: dict) -> str:
        for key in ("command", "path", "source", "url", "paths", "message", "pattern", "query", "attachment_id"):
            if key in arguments:
                return str(arguments[key])
        return json.dumps(arguments)[:200] if arguments else "(current project)"


def _coerce(key: str, value: Any, kind: str) -> Any:
    if kind == "string":
        if isinstance(value, (str, int, float)):
            return str(value)
    elif kind == "integer":
        try:
            return int(value)
        except (TypeError, ValueError):
            pass
    elif kind == "boolean":
        if isinstance(value, bool):
            return value
        if str(value).lower() in {"true", "false"}:
            return str(value).lower() == "true"
    elif kind == "array":
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, str):
            if value.strip().startswith("["):
                # Small models often send a list as JSON text: '["a.png", "b.png"]'.
                try:
                    parsed = json.loads(value)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, list):
                    return [str(item) for item in parsed]
            return [value]
    raise ToolError(f"argument '{key}' must be of type {kind}")


@dataclass
class Adapter:
    app_id: str
    tools: list[Tool]


def discover_tools() -> dict[str, Tool]:
    from . import adapters

    tools: dict[str, Tool] = {}
    for info in sorted(pkgutil.iter_modules(adapters.__path__), key=lambda item: item.name):
        adapter = getattr(importlib.import_module(f"{adapters.__name__}.{info.name}"), "adapter", None)
        if isinstance(adapter, Adapter):
            for tool in adapter.tools:
                tools[tool.name] = tool
    return tools


def clip_output(text: str, limit: int) -> str:
    text = redact(text)
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n... [{len(text) - limit} characters omitted] ...\n{text[-half:]}"
