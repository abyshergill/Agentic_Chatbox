"""MCP (Model Context Protocol) servers: connect to them and hand their tools to the agent.

Configured in data/mcp.json, in the same shape as Claude Desktop's claude_desktop_config.json, so a config can be
pasted or imported as is:

    {"mcpServers": {
        "filesystem": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/Users/me/notes"]},
        "remote":     {"url": "https://example.com/mcp", "headers": {"Authorization": "Bearer ..."}}
    }}

Extra keys this app understands: "disabled": true (keep it but do not start it), "transport": "http" | "sse" for URL
servers, and "alwaysAllow": [tool names] (tools that run without asking each time).

Only the user configures servers, from Settings > Connectors. The assistant has no tool that reaches this file, and
every MCP tool call goes through the agent's normal permission and approval checks (#mcp in arch/permission.md).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Settings
from .tools import Tool, ToolContext, ToolError, ToolResult

SERVER_NAME = re.compile(r"^[A-Za-z0-9][\w.-]{0,47}$")
CONNECT_TIMEOUT = 60
CALL_TIMEOUT = 300
# Where command-line tools usually live. A desktop app started from Finder or the Dock gets a minimal PATH, so `npx`,
# `uvx` or `docker` would not be found without these.
EXTRA_BIN_DIRS = [
    "/opt/homebrew/bin", "/usr/local/bin", "~/.local/bin", "~/.cargo/bin", "~/.bun/bin", "~/.deno/bin",
    "~/.volta/bin", "/Applications/Docker.app/Contents/Resources/bin",
]


def claude_desktop_config_path() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", "~")).expanduser() / "Claude" / "claude_desktop_config.json"
    if os.uname().sysname == "Darwin":
        return Path("~/Library/Application Support/Claude/claude_desktop_config.json").expanduser()
    return Path("~/.config/Claude/claude_desktop_config.json").expanduser()


def search_path() -> str:
    dirs = [str(Path(d).expanduser()) for d in EXTRA_BIN_DIRS]
    nvm = Path("~/.nvm/versions/node").expanduser()
    if nvm.is_dir():
        # Newest Node first.
        dirs += [str(p / "bin") for p in sorted(nvm.iterdir(), reverse=True) if (p / "bin").is_dir()]
    current = os.environ.get("PATH", "").split(os.pathsep)
    return os.pathsep.join(dict.fromkeys([*current, *dirs]))


def resolve_command(command: str) -> str:
    """The full path of a server's program, looking in the usual install places as well as PATH."""
    if os.path.isabs(command):
        return command
    found = shutil.which(command, path=search_path())
    if not found:
        raise ValueError(f"'{command}' was not found on this computer. Install it, or give its full path.")
    return found


def check_server(name: str, config: Any) -> dict:
    """Validates one server entry and returns a cleaned copy."""
    if not SERVER_NAME.match(name or ""):
        raise ValueError("Server names may use letters, numbers, dots, dashes and underscores (up to 48).")
    if not isinstance(config, dict):
        raise ValueError(f"{name}: the server settings must be a JSON object.")
    clean: dict = {}
    if config.get("command"):
        clean["command"] = str(config["command"]).strip()
        args = config.get("args", [])
        if not isinstance(args, list):
            raise ValueError(f"{name}: 'args' must be a list of strings.")
        clean["args"] = [str(a) for a in args]
        env = config.get("env", {}) or {}
        if not isinstance(env, dict):
            raise ValueError(f"{name}: 'env' must be an object of names to values.")
        clean["env"] = {str(k): str(v) for k, v in env.items()}
        if config.get("cwd"):
            clean["cwd"] = str(config["cwd"])
    elif config.get("url") or config.get("serverUrl"):
        url = str(config.get("url") or config.get("serverUrl")).strip()
        if not re.match(r"^https?://", url):
            raise ValueError(f"{name}: the URL must start with https:// or http://.")
        clean["url"] = url
        transport = str(config.get("transport") or config.get("type") or "http").lower()
        clean["transport"] = "sse" if transport == "sse" else "http"
        headers = config.get("headers", {}) or {}
        if not isinstance(headers, dict):
            raise ValueError(f"{name}: 'headers' must be an object.")
        clean["headers"] = {str(k): str(v) for k, v in headers.items()}
    else:
        raise ValueError(f"{name}: give either a 'command' (a local server) or a 'url' (a remote server).")
    if config.get("disabled"):
        clean["disabled"] = True
    always = config.get("alwaysAllow") or []
    if isinstance(always, list):
        clean["alwaysAllow"] = sorted({str(t) for t in always})
    return clean


class McpConfigStore:
    """data/mcp.json (mode 0600: it can hold API tokens in env or headers)."""

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict[str, dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        servers = data.get("mcpServers", {}) if isinstance(data, dict) else {}
        return servers if isinstance(servers, dict) else {}

    def save(self, servers: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=self.path.parent, prefix=".mcp-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"mcpServers": servers}, handle, indent=2)
            os.chmod(temp, 0o600)
            os.replace(temp, self.path)
        except BaseException:
            if os.path.exists(temp):
                os.unlink(temp)
            raise


# ---- tools from MCP servers -------------------------------------------------------


@dataclass
class McpTool(Tool):
    """A tool offered by an MCP server. Arguments follow the server's own JSON schema."""

    server: str = ""
    remote_name: str = ""
    input_schema: dict = field(default_factory=dict)
    trusted: bool = False  # In the server's alwaysAllow list: runs without asking.
    read_only: bool = False

    def schema(self) -> dict:
        parameters = {k: v for k, v in (self.input_schema or {}).items() if k not in {"$schema", "$id"}}
        parameters.setdefault("type", "object")
        parameters.setdefault("properties", {})
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": parameters}}

    def clean_arguments(self, raw: Any) -> dict:
        if raw is None or raw == "":
            raw = {}
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ToolError("arguments must be a JSON object") from exc
        if not isinstance(raw, dict):
            raise ToolError("arguments must be a JSON object")
        missing = [key for key in self.input_schema.get("required", []) or [] if key not in raw]
        if missing:
            raise ToolError(f"missing required argument(s): {', '.join(missing)}")
        return dict(raw)

    def target(self, arguments: dict) -> str:
        text = json.dumps(arguments, ensure_ascii=False, default=str) if arguments else ""
        return f"{self.server} › {self.remote_name}" + (f" {text[:160]}" if text else "")


def tool_name(server: str, remote: str, taken: set[str]) -> str:
    """mcp_<server>_<tool>, limited to what every model API accepts: [A-Za-z0-9_-], at most 64 characters."""
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", f"mcp_{server}_{remote}")[:64]
    name, n = base, 2
    while name in taken:
        suffix = f"_{n}"
        name, n = base[: 64 - len(suffix)] + suffix, n + 1
    return name


def result_text(result: Any) -> tuple[bool, str]:
    """Flattens an MCP CallToolResult into text the model can read."""
    parts: list[str] = []
    for item in getattr(result, "content", None) or []:
        kind = getattr(item, "type", "")
        if kind == "text":
            parts.append(item.text)
        elif kind in {"image", "audio"}:
            size = len(getattr(item, "data", "") or "") * 3 // 4
            parts.append(f"[{kind}: {getattr(item, 'mime_type', '') or getattr(item, 'mimeType', '')}, about {size} bytes]")
        elif kind == "resource":
            resource = item.resource
            text = getattr(resource, "text", None)
            parts.append(f"[resource {resource.uri}]\n{text}" if text is not None else f"[binary resource {resource.uri}]")
        elif kind == "resource_link":
            parts.append(f"[link: {getattr(item, 'name', '')} {item.uri}]")
        else:
            parts.append(str(item))
    structured = getattr(result, "structured_content", None)
    if structured is None:
        structured = getattr(result, "structuredContent", None)
    if structured is not None and not parts:
        parts.append(json.dumps(structured, ensure_ascii=False, indent=2, default=str))
    is_error = bool(getattr(result, "is_error", False) or getattr(result, "isError", False))
    return not is_error, "\n".join(parts) or "(the tool returned nothing)"


def describe_error(exc: BaseException) -> str:
    """The useful message inside an exception group, as anyio wraps transport failures."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    text = str(exc).strip() or type(exc).__name__
    if isinstance(exc, FileNotFoundError):
        text = f"program not found: {exc.filename or text}"
    return text[:600]


# ---- one server ----------------------------------------------------------------


class McpConnection:
    """Holds one server's session open in a background task (MCP sessions must be entered and left in one task)."""

    def __init__(self, name: str, config: dict, log_path: Path):
        self.name = name
        self.config = config
        self.log_path = log_path
        self.status = "stopped"  # stopped | starting | connected | error
        self.error = ""
        self.tools: list[Any] = []
        self.instructions = ""
        self.server_info = ""
        self.session: Any = None
        self._task: asyncio.Task | None = None
        self._stop: asyncio.Event | None = None
        self._ready: asyncio.Event | None = None

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self.status, self.error = "starting", ""
        self._stop, self._ready = asyncio.Event(), asyncio.Event()
        self._task = asyncio.create_task(self._run(), name=f"mcp:{self.name}")
        try:
            await asyncio.wait_for(self._ready.wait(), CONNECT_TIMEOUT)
        except TimeoutError:
            self.error = f"did not finish starting within {CONNECT_TIMEOUT} seconds"
            await self.stop(status="error")

    async def _transport(self, stack: AsyncExitStack):
        config = self.config
        if "command" in config:
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client

            command = resolve_command(config["command"])
            env = {"PATH": os.pathsep.join(dict.fromkeys([str(Path(command).parent), *search_path().split(os.pathsep)])),
                   **config.get("env", {})}
            params = StdioServerParameters(command=command, args=config.get("args", []), env=env,
                                           cwd=config.get("cwd") or None)
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            errlog = stack.enter_context(self.log_path.open("a", encoding="utf-8"))
            errlog.write(f"\n--- starting {command} {' '.join(config.get('args', []))}\n")
            errlog.flush()
            streams = await stack.enter_async_context(stdio_client(params, errlog=errlog))
        elif config.get("transport") == "sse":
            from mcp.client.sse import sse_client

            streams = await stack.enter_async_context(sse_client(config["url"], headers=config.get("headers") or None))
        else:
            from mcp.client.streamable_http import streamable_http_client
            from mcp.shared._httpx_utils import create_mcp_http_client

            http = await stack.enter_async_context(create_mcp_http_client(headers=config.get("headers") or None))
            streams = await stack.enter_async_context(streamable_http_client(config["url"], http_client=http))
        return streams[0], streams[1]

    async def _run(self) -> None:
        from mcp import ClientSession

        try:
            async with AsyncExitStack() as stack:
                read, write = await self._transport(stack)
                session = await stack.enter_async_context(ClientSession(read, write))
                init = await session.initialize()
                info = getattr(init, "server_info", None) or getattr(init, "serverInfo", None)
                self.server_info = f"{info.name} {info.version}".strip() if info else ""
                self.instructions = (getattr(init, "instructions", None) or "").strip()
                self.tools = await self._list_tools(session)
                self.session = session
                self.status = "connected"
                self._ready.set()
                await self._stop.wait()
        except asyncio.CancelledError:
            pass
        except BaseException as exc:  # noqa: BLE001 - a broken server must not take the app down.
            self.error = describe_error(exc)
            self.status = "error"
        finally:
            self.session = None
            if self.status != "error":
                self.status = "stopped"
            if self._ready is not None:
                self._ready.set()

    @staticmethod
    async def _list_tools(session) -> list[Any]:
        tools: list[Any] = []
        cursor = None
        for _ in range(20):  # Pages; a server with more than that is listing something else.
            if cursor:
                from mcp import types

                page = await session.list_tools(params=types.PaginatedRequestParams(cursor=cursor))
            else:
                page = await session.list_tools()
            tools += list(page.tools)
            cursor = getattr(page, "next_cursor", None) or getattr(page, "nextCursor", None)
            if not cursor:
                break
        return tools

    async def refresh_tools(self) -> None:
        if self.session is not None:
            self.tools = await self._list_tools(self.session)

    async def call(self, name: str, arguments: dict) -> tuple[bool, str]:
        if self.session is None:
            raise ToolError(f"the MCP server '{self.name}' is not connected; reconnect it in Settings > Connectors")
        try:
            result = await asyncio.wait_for(self.session.call_tool(name, arguments), CALL_TIMEOUT)
        except TimeoutError as exc:
            raise ToolError(f"{self.name} did not answer within {CALL_TIMEOUT} seconds") from exc
        except Exception as exc:  # noqa: BLE001 - protocol errors go back to the model as the result.
            raise ToolError(f"{self.name} reported an error: {describe_error(exc)}") from exc
        return result_text(result)

    async def stop(self, status: str = "stopped") -> None:
        if self._stop is not None:
            self._stop.set()
        task = self._task
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), 8)
            except (TimeoutError, asyncio.CancelledError):
                task.cancel()
            except BaseException:  # noqa: BLE001
                pass
        self._task = None
        self.session = None
        self.status = status

    def log_tail(self, chars: int = 4000) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")[-chars:]
        except OSError:
            return ""


# ---- all servers ------------------------------------------------------------------


class McpHub:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = McpConfigStore(settings.data_dir / "mcp.json")
        self.connections: dict[str, McpConnection] = {}
        self._lock = asyncio.Lock()

    def _log_path(self, name: str) -> Path:
        return self.settings.logs_dir / f"mcp-{re.sub(r'[^A-Za-z0-9_.-]', '_', name)}.log"

    # ---- lifecycle

    async def start_all(self) -> None:
        servers = self.store.load()
        await asyncio.gather(*(self.connect(name) for name, cfg in servers.items() if not cfg.get("disabled")),
                             return_exceptions=True)

    async def stop_all(self) -> None:
        await asyncio.gather(*(c.stop() for c in self.connections.values()), return_exceptions=True)

    async def connect(self, name: str) -> McpConnection:
        async with self._lock:
            config = self.store.load().get(name)
            if config is None:
                raise KeyError(name)
            old = self.connections.pop(name, None)
        if old is not None:
            await old.stop()
        try:
            clean = check_server(name, config)
        except ValueError as exc:
            connection = McpConnection(name, config, self._log_path(name))
            connection.status, connection.error = "error", str(exc)
            self.connections[name] = connection
            return connection
        connection = McpConnection(name, clean, self._log_path(name))
        self.connections[name] = connection
        try:
            await connection.start()
        except ValueError as exc:  # e.g. the program is not installed.
            connection.status, connection.error = "error", str(exc)
        return connection

    async def disconnect(self, name: str) -> None:
        connection = self.connections.pop(name, None)
        if connection is not None:
            await connection.stop()

    # ---- configuration (Settings > Connectors)

    async def save_server(self, name: str, config: dict, original: str | None = None) -> None:
        clean = check_server(name, config)
        servers = self.store.load()
        if original and original != name:
            if name in servers:
                raise ValueError(f"A server named '{name}' already exists.")
            servers.pop(original, None)
            await self.disconnect(original)
        previous = servers.get(name, {})
        if "alwaysAllow" not in config and previous.get("alwaysAllow"):
            clean["alwaysAllow"] = previous["alwaysAllow"]
        servers[name] = clean
        self.store.save(servers)
        if clean.get("disabled"):
            await self.disconnect(name)
        else:
            await self.connect(name)

    async def import_servers(self, servers: dict, replace: bool = False) -> list[str]:
        """Adds every server from a Claude Desktop-style {"mcpServers": {...}} object."""
        if "mcpServers" in servers:
            servers = servers["mcpServers"]
        if not isinstance(servers, dict) or not servers:
            raise ValueError('Paste an object like {"mcpServers": {"name": {"command": "...", "args": []}}}.')
        cleaned = {name: check_server(name, cfg) for name, cfg in servers.items()}
        current = self.store.load()
        added = []
        for name, cfg in cleaned.items():
            if name in current and not replace:
                continue
            current[name] = cfg
            added.append(name)
        self.store.save(current)
        await asyncio.gather(*(self.connect(n) for n in added if not current[n].get("disabled")), return_exceptions=True)
        return added

    async def remove_server(self, name: str) -> bool:
        servers = self.store.load()
        if name not in servers:
            return False
        servers.pop(name)
        self.store.save(servers)
        await self.disconnect(name)
        return True

    async def set_enabled(self, name: str, enabled: bool) -> None:
        servers = self.store.load()
        if name not in servers:
            raise KeyError(name)
        if enabled:
            servers[name].pop("disabled", None)
        else:
            servers[name]["disabled"] = True
        self.store.save(servers)
        if enabled:
            await self.connect(name)
        else:
            await self.disconnect(name)

    def set_always_allow(self, name: str, tool: str, allowed: bool) -> None:
        servers = self.store.load()
        if name not in servers:
            raise KeyError(name)
        current = set(servers[name].get("alwaysAllow") or [])
        current = current | {tool} if allowed else current - {tool}
        servers[name]["alwaysAllow"] = sorted(current)
        self.store.save(servers)

    # ---- what the UI and the agent see

    def status(self) -> list[dict]:
        servers = self.store.load()
        rows = []
        for name, config in servers.items():
            connection = self.connections.get(name)
            always = set(config.get("alwaysAllow") or [])
            rows.append({
                "name": name,
                "kind": "local" if config.get("command") else "remote",
                "command": " ".join([config.get("command", ""), *config.get("args", [])]).strip(),
                "url": config.get("url", ""),
                "transport": config.get("transport", "stdio" if config.get("command") else "http"),
                "env_keys": sorted((config.get("env") or {}).keys()),
                "header_keys": sorted((config.get("headers") or {}).keys()),
                "disabled": bool(config.get("disabled")),
                "status": "disabled" if config.get("disabled") else (connection.status if connection else "stopped"),
                "error": connection.error if connection else "",
                "server_info": connection.server_info if connection else "",
                "tools": [
                    {"name": t.name, "description": (t.description or "")[:300],
                     "read_only": bool(getattr(getattr(t, "annotations", None), "read_only_hint", False)),
                     "always_allow": t.name in always}
                    for t in (connection.tools if connection else [])
                ],
            })
        return rows

    def raw_config(self, name: str) -> dict | None:
        return self.store.load().get(name)

    def tools(self) -> dict[str, McpTool]:
        """Tools of every connected server, as agent tools. Rebuilt on each call, so config changes apply at once."""
        servers = self.store.load()
        found: dict[str, McpTool] = {}
        for name, connection in list(self.connections.items()):
            if connection.status != "connected" or name not in servers:
                continue
            always = set(servers[name].get("alwaysAllow") or [])
            for remote in connection.tools:
                annotations = getattr(remote, "annotations", None)
                read_only = bool(getattr(annotations, "read_only_hint", False))
                destructive = bool(getattr(annotations, "destructive_hint", False)) and not read_only
                local = tool_name(name, remote.name, set(found))
                schema = getattr(remote, "input_schema", None) or getattr(remote, "inputSchema", None) or {}
                found[local] = McpTool(
                    name=local, app="mcp", operation="read" if read_only else "call",
                    description=f"[{name}] {(remote.description or remote.name).strip()}"[:1024],
                    parameters={}, handler=_make_handler(self, name, remote.name),
                    destructive=destructive, server=name, remote_name=remote.name,
                    input_schema=dict(schema) if isinstance(schema, dict) else {},
                    trusted=remote.name in always, read_only=read_only,
                )
        return found

    def instructions(self, servers: set[str]) -> str:
        notes = []
        for name in sorted(servers):
            connection = self.connections.get(name)
            if connection and connection.instructions:
                notes.append(f"### {name}\n{connection.instructions[:1500]}")
        return "\n\n".join(notes)


def _make_handler(hub: McpHub, server: str, remote: str):
    async def handler(_context: ToolContext, arguments: dict) -> ToolResult:
        connection = hub.connections.get(server)
        if connection is None:
            raise ToolError(f"the MCP server '{server}' is not running")
        ok, text = await connection.call(remote, arguments)
        return ToolResult(ok, text)

    return handler
