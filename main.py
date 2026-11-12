"""Agentic Chatbox: FastAPI entry point. Run with `python main.py` (browser) or `python desktop.py` (desktop app)."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode, urlparse

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core.agent import AgentRun, ChatOptions
from core.app_context import AppContext
from core.arch_loader import load_registry
from core.config import PROJECT_ROOT, Settings
from core.fs_guard import FsGuard, PathDenied, atomic_write, is_secret_name, read_text_limited
from core import dataviz
from core.documents import DOCUMENT_TYPES, extract_text
from core.models import PRESETS, mask
from core.ollama_client import OllamaError
from core.openai_client import OpenAICompatibleClient
from core.permissions import PermissionManager
from core.process import isolation_mode
from core.sessions import Session
from core.uploads import (
    CHUNK, Attachment, UploadRejected, new_attachment_id, store_path,
    upload_rules, validate_content, validate_name,
)

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self' data:; worker-src 'self' blob:; connect-src 'self'; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
)


class ChatRequest(BaseModel):
    session_id: str
    message: str = Field(min_length=1, max_length=100_000)
    active_path: str | None = None
    selected_file: str | None = None
    selection: str | None = Field(default=None, max_length=50_000)
    model: str | None = None
    auto_approve: str | None = Field(default=None, max_length=500)
    context_files: list[str] = Field(default_factory=list, max_length=6)
    plan_only: bool = False
    skills: list[str] = Field(default_factory=list, max_length=10)
    mcp_servers: list[str] | None = Field(default=None, max_length=100)


class McpServerRequest(BaseModel):
    name: str = Field(min_length=1, max_length=48)
    original: str | None = Field(default=None, max_length=48)
    config: dict


class McpImportRequest(BaseModel):
    config: dict | None = None
    from_claude_desktop: bool = False
    replace: bool = False


class McpToggleRequest(BaseModel):
    enabled: bool


class McpAllowRequest(BaseModel):
    tool: str = Field(min_length=1, max_length=200)
    allowed: bool = True


class SkillRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    original: str | None = Field(default=None, max_length=64)
    description: str = Field(min_length=1, max_length=1024)
    body: str = Field(default="", max_length=200_000)


class SkillImportRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    replace: bool = False


class PreferencesRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=60)
    custom_instructions: str | None = Field(default=None, max_length=8000)


class DownloadRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2000)


class OllamaModelRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class SessionRequest(BaseModel):
    session_id: str


class ApprovalRequest(BaseModel):
    session_id: str
    approval_id: str


class ProjectRequest(BaseModel):
    session_id: str
    name: str = Field(min_length=1, max_length=60)


class MkdirRequest(BaseModel):
    session_id: str
    path: str = Field(min_length=1, max_length=300)


class PathRequest(BaseModel):
    session_id: str
    path: str = Field(min_length=1, max_length=500)


class RenameRequest(BaseModel):
    session_id: str
    path: str = Field(min_length=1, max_length=500)
    new_path: str = Field(min_length=1, max_length=500)


class ProviderRequest(BaseModel):
    id: str | None = Field(default=None, max_length=30)
    name: str = Field(min_length=1, max_length=40)
    base_url: str = Field(min_length=1, max_length=300)
    api_key: str | None = Field(default=None, max_length=500)
    models: list[str] = Field(default_factory=list, max_length=100)
    native_tools: bool = True


class DiscoverRequest(BaseModel):
    id: str | None = None
    base_url: str = Field(min_length=1, max_length=300)
    api_key: str | None = Field(default=None, max_length=500)


class GgufRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1000)


class OpenRequest(BaseModel):
    session_id: str
    path: str = Field(min_length=1, max_length=1000)
    reveal: bool = False  # Show it in Finder instead of opening it.
    base: str | None = Field(default=None, max_length=1000)  # The active project, for paths relative to it.


class FilesWindowRequest(BaseModel):
    path: str | None = Field(default=None, max_length=1000)  # A file to open in it.


class CopyRequest(BaseModel):
    session_id: str
    path: str = Field(min_length=1, max_length=500)
    dest_dir: str = Field(min_length=1, max_length=500)


BAD_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def check_name(name: str) -> None:
    """Rejects names the explorer must not create: separators, reserved characters, '.', '..', or edge spaces."""
    if not name or name in {".", ".."} or len(name) > 255 or name != name.strip() or BAD_NAME.search(name):
        raise HTTPException(400, f"'{name}' is not a valid name. Avoid / \\ : * ? \" < > | and leading or trailing spaces.")


def copy_name(folder: Path, name: str) -> Path:
    """'report.csv' -> 'report copy.csv', then 'report copy 2.csv' ..., like VS Code's duplicate and paste."""
    if not (folder / name).exists():
        return folder / name
    stem, ext = (name, "") if name.startswith(".") or "." not in name else (name.rsplit(".", 1)[0], "." + name.rsplit(".", 1)[1])
    for index in range(1, 1000):
        candidate = folder / f"{stem} copy{'' if index == 1 else f' {index}'}{ext}"
        if not candidate.exists():
            return candidate
    raise HTTPException(409, "Too many copies with this name.")


class FolderRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    create: bool = False


class RenameChatRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class WriteRequest(BaseModel):
    session_id: str
    path: str
    content: str
    expected_mtime: float | None = None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    ctx = AppContext.create(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Connectors start in the background, so a slow server never delays the window.
        startup = asyncio.create_task(ctx.mcp.start_all()) if ctx.mcp is not None else None
        yield
        if startup is not None and not startup.done():
            startup.cancel()
        if ctx.mcp is not None:
            await ctx.mcp.stop_all()
        if hasattr(ctx.llm, "shutdown"):
            ctx.llm.shutdown()  # Stops the llama.cpp server for .gguf models.

    app = FastAPI(title="Agentic Chatbox", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.ctx = ctx

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        # Without this, any website open in your browser could call this API,
        # including approving tool actions. Checking Host also blocks DNS rebinding.
        host = request.headers.get("host", "")
        if urlparse(f"//{host}").hostname not in LOOPBACK:
            return JSONResponse({"detail": "Requests must address this machine directly."}, status_code=403)
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != host:
            return JSONResponse({"detail": "Cross-origin requests are not allowed."}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "Cross-site requests are not allowed."}, status_code=403)
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    # ---- helpers ------------------------------------------------------------

    def permissions() -> PermissionManager:
        return PermissionManager(settings.arch_dir / "permission.md")

    def session_for(session_id: str) -> Session:
        try:
            return ctx.sessions.get(session_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    def guard_for(session: Session) -> FsGuard:
        """The file view follows the active skill, falling back to the default skill.

        It also shows every folder the user opened and arch/, so agents and skills can be edited here.
        """
        registry = load_registry(settings.arch_dir)
        command = session.pinned_command or session.active_command
        skill = registry.skill_for(command) if command else None
        if skill is None or not skill.valid:
            skill = registry.default_skill
        paths = skill.allowed_paths if skill is not None and skill.valid else []
        return FsGuard(settings, permissions(), paths, include_config=True)

    def require_fs(operation: str) -> None:
        decision = permissions().check("filesystem", operation)
        if not decision.allowed:
            raise HTTPException(403, decision.reason)

    def audit_ui(session: Session, operation: str, target: str, status: str, result: str) -> None:
        ctx.audit.record(
            session_id=session.id, agent="user", skill="-", model="-", operation=operation, target=target,
            permission="allowed" if status == "success" else "denied", approval="user_action",
            status=status, result=result,
        )

    # ---- status and configuration -------------------------------------------

    @app.get("/api/health")
    async def health():
        registry = load_registry(settings.arch_dir)
        perms = permissions()
        try:
            models = await ctx.llm.list_models()
            ollama = {"connected": True, "models": models, "error": None}
        except OllamaError as exc:
            ollama = {"connected": False, "models": [], "error": str(exc)}
        catalog = []
        if hasattr(ctx.llm, "catalog"):
            catalog = [{"id": e.id, "label": e.label, "kind": e.kind, "remote": e.remote} for e in await ctx.llm.catalog()]
            ollama["ollama_error"] = ctx.llm.ollama_error
        connectors = ctx.mcp.status() if ctx.mcp is not None else []
        return {
            "status": "ok",
            "name": registry.system.name,
            "ollama": ollama,
            "catalog": catalog,
            "isolation": isolation_mode(),
            "warnings": registry.warnings + perms.warnings,
            "connectors": [{"name": c["name"], "status": c["status"], "tools": len(c["tools"])} for c in connectors],
            "skills": len(ctx.skills.active()) if ctx.skills is not None else 0,
        }

    # ---- models: Ollama, .gguf files, API providers ---------------------------------
    # These change which model the app talks to and hold API keys, so only the user's interface reaches them:
    # the assistant has no tool for them, and keys are never sent back to the page.

    def model_settings() -> dict:
        data = ctx.llm.store.load()
        return {
            "providers": [{**{k: v for k, v in p.items() if k != "api_key"}, "api_key": mask(p.get("api_key", "")),
                           "remote": not is_local(p["base_url"])} for p in data["providers"]],
            "gguf": [{"path": str(f), "name": f.name, "exists": f.is_file(),
                      "size_mb": round(f.stat().st_size / 1e6) if f.is_file() else None,
                      "from_folder": f.parent == ctx.llm.models_dir.resolve()} for f in ctx.llm.gguf_files()],
            "models_dir": str(ctx.llm.models_dir),
            "runtime": ctx.llm.gguf.status(),
            "ollama_error": ctx.llm.ollama_error,
            "ollama_url": settings.ollama_url,
            "lmstudio": {"running": getattr(ctx.llm, "lmstudio_running", False), "url": settings.lmstudio_url},
            "presets": PRESETS,
            "downloads": ctx.downloads.jobs() if ctx.downloads is not None else [],
        }

    def is_local(url: str) -> bool:
        from core.models import is_local_url
        return is_local_url(url)

    @app.get("/api/models")
    async def get_models():
        await ctx.llm.catalog()  # Refreshes the Ollama status.
        return model_settings()

    @app.post("/api/models/providers")
    async def save_provider(body: ProviderRequest):
        try:
            provider = ctx.llm.store.upsert_provider(body.name, body.base_url, body.api_key, body.models,
                                                     body.native_tools, body.id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        ctx.audit.record(session_id="-", agent="user", skill="-", model="-", operation="ui_model_provider",
                         target=provider["id"], permission="allowed", approval="user_action", status="success",
                         result=f"saved {provider['base_url']} with {len(provider['models'])} model(s)")
        return model_settings()

    @app.delete("/api/models/providers/{provider_id}")
    async def delete_provider(provider_id: str):
        if not ctx.llm.store.remove_provider(provider_id):
            raise HTTPException(404, "No such provider.")
        return model_settings()

    @app.post("/api/models/providers/discover")
    async def discover_models(body: DiscoverRequest):
        """Asks an API which models it offers, so the user can pick instead of typing names."""
        from core.models import check_base_url
        try:
            base_url = check_base_url(body.base_url)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        key = body.api_key or ""
        if not key and body.id:
            saved = ctx.llm.store.provider(body.id)
            key = saved.get("api_key", "") if saved else ""
        try:
            models = await OpenAICompatibleClient(base_url, key, label="The API").list_models()
        except OllamaError as exc:
            raise HTTPException(502, str(exc)) from exc
        return {"models": models[:500]}

    @app.post("/api/models/gguf")
    async def add_gguf(body: GgufRequest):
        try:
            ctx.llm.store.add_gguf(body.path)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(400, f"Could not read that file: {exc.strerror}") from exc
        return model_settings()

    @app.post("/api/models/gguf/remove")
    async def remove_gguf(body: GgufRequest):
        if not ctx.llm.store.remove_gguf(body.path):
            raise HTTPException(404, "That file is not in the list. Files in the models folder are listed automatically.")
        return model_settings()

    @app.post("/api/models/gguf/stop")
    async def stop_gguf():
        ctx.llm.gguf.stop()
        return model_settings()

    @app.get("/api/models/ollama")
    async def ollama_models():
        """Installed Ollama models with their sizes, for Settings > Models."""
        import httpx
        try:
            async with httpx.AsyncClient(timeout=5) as http:
                response = await http.get(f"{settings.ollama_url}/api/tags")
            models = response.json().get("models", [])
        except (httpx.HTTPError, ValueError):
            return {"running": False, "models": []}
        return {"running": True, "models": [
            {"name": m.get("name"), "size_gb": round((m.get("size") or 0) / 1e9, 1),
             "family": (m.get("details") or {}).get("family", ""),
             "parameters": (m.get("details") or {}).get("parameter_size", ""),
             "quantization": (m.get("details") or {}).get("quantization_level", "")} for m in models]}

    @app.post("/api/models/ollama/pull")
    async def ollama_pull(body: OllamaModelRequest):
        try:
            job = ctx.downloads.pull_ollama(body.name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        ctx.audit.record(session_id="-", agent="user", skill="-", model="-", operation="ui_ollama_pull",
                         target=body.name, permission="allowed", approval="user_action", status="success", result="started")
        return job.public()

    @app.post("/api/models/ollama/delete")
    async def ollama_delete(body: OllamaModelRequest):
        import httpx
        try:
            async with httpx.AsyncClient(timeout=30) as http:
                response = await http.request("DELETE", f"{settings.ollama_url}/api/delete", json={"model": body.name})
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"Ollama is not reachable: {exc}") from exc
        if response.status_code != 200:
            raise HTTPException(400, f"Ollama could not delete {body.name}: {response.text[:200]}")
        ctx.audit.record(session_id="-", agent="user", skill="-", model="-", operation="ui_ollama_delete",
                         target=body.name, permission="allowed", approval="user_action", status="success", result="deleted")
        return {"deleted": True}

    @app.post("/api/models/download")
    async def download_model(body: DownloadRequest):
        """Downloads a .gguf file from a link (e.g. Hugging Face) into models/."""
        try:
            job = ctx.downloads.download_gguf(body.url)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        ctx.audit.record(session_id="-", agent="user", skill="-", model="-", operation="ui_gguf_download",
                         target=job.source, permission="allowed", approval="user_action", status="success", result="started")
        return job.public()

    @app.get("/api/models/downloads")
    async def list_downloads():
        return {"jobs": ctx.downloads.jobs()}

    @app.post("/api/models/downloads/{job_id}/cancel")
    async def cancel_download(job_id: str):
        return {"cancelled": ctx.downloads.cancel(job_id)}

    @app.post("/api/models/downloads/clear")
    async def clear_downloads():
        ctx.downloads.clear_finished()
        return {"jobs": ctx.downloads.jobs()}

    # ---- connectors: MCP servers ----------------------------------------------------
    # Only the user's interface reaches these. Secrets in env and headers are never sent back to the page.

    def audit_settings(operation: str, target: str, result: str) -> None:
        ctx.audit.record(session_id="-", agent="user", skill="-", model="-", operation=operation, target=target,
                         permission="allowed", approval="user_action", status="success", result=result)

    @app.get("/api/mcp")
    async def mcp_status():
        from core.mcp_hub import claude_desktop_config_path
        return {"servers": ctx.mcp.status(), "claude_desktop_config": claude_desktop_config_path().is_file()}

    @app.get("/api/mcp/servers/{name}")
    async def mcp_server(name: str):
        config = ctx.mcp.raw_config(name)
        if config is None:
            raise HTTPException(404, "No such server.")
        masked = json.loads(json.dumps(config))
        for key in ("env", "headers"):
            if isinstance(masked.get(key), dict):
                masked[key] = {k: mask(str(v)) for k, v in masked[key].items()}
        connection = ctx.mcp.connections.get(name)
        return {"name": name, "config": masked, "log": connection.log_tail() if connection else ""}

    @app.post("/api/mcp/servers")
    async def save_mcp_server(body: McpServerRequest):
        config = dict(body.config)
        previous = ctx.mcp.raw_config(body.original or body.name) or {}
        # Masked values coming back from the form mean "keep what is saved".
        for key in ("env", "headers"):
            if isinstance(config.get(key), dict):
                kept = previous.get(key) or {}
                config[key] = {k: (kept.get(k, "") if str(v).startswith("••••••") else v) for k, v in config[key].items()}
        try:
            await ctx.mcp.save_server(body.name.strip(), config, body.original)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        audit_settings("ui_mcp_save", body.name, "saved")
        return {"servers": ctx.mcp.status()}

    @app.post("/api/mcp/import")
    async def import_mcp(body: McpImportRequest):
        from core.mcp_hub import claude_desktop_config_path
        source = body.config
        if body.from_claude_desktop:
            try:
                source = json.loads(claude_desktop_config_path().read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise HTTPException(404, f"Could not read Claude Desktop's config: {exc}") from exc
        if not source:
            raise HTTPException(400, "Nothing to import.")
        try:
            added = await ctx.mcp.import_servers(source, body.replace)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        audit_settings("ui_mcp_import", ", ".join(added) or "-", f"imported {len(added)}")
        return {"added": added, "servers": ctx.mcp.status()}

    @app.post("/api/mcp/servers/{name}/toggle")
    async def toggle_mcp(name: str, body: McpToggleRequest):
        try:
            await ctx.mcp.set_enabled(name, body.enabled)
        except KeyError as exc:
            raise HTTPException(404, "No such server.") from exc
        audit_settings("ui_mcp_toggle", name, "enabled" if body.enabled else "disabled")
        return {"servers": ctx.mcp.status()}

    @app.post("/api/mcp/servers/{name}/restart")
    async def restart_mcp(name: str):
        try:
            await ctx.mcp.connect(name)
        except KeyError as exc:
            raise HTTPException(404, "No such server.") from exc
        return {"servers": ctx.mcp.status()}

    @app.post("/api/mcp/servers/{name}/always-allow")
    async def always_allow_mcp(name: str, body: McpAllowRequest):
        try:
            ctx.mcp.set_always_allow(name, body.tool, body.allowed)
        except KeyError as exc:
            raise HTTPException(404, "No such server.") from exc
        audit_settings("ui_mcp_always_allow", f"{name}/{body.tool}", "always allow" if body.allowed else "ask each time")
        return {"servers": ctx.mcp.status()}

    @app.delete("/api/mcp/servers/{name}")
    async def delete_mcp(name: str):
        if not await ctx.mcp.remove_server(name):
            raise HTTPException(404, "No such server.")
        audit_settings("ui_mcp_remove", name, "removed")
        return {"servers": ctx.mcp.status()}

    # ---- Agent Skills -----------------------------------------------------------------

    @app.get("/api/agent-skills")
    async def list_agent_skills():
        return {"skills": [s.summary() for s in ctx.skills.list()], "folder": str(settings.skills_dir)}

    @app.get("/api/agent-skills/{name}")
    async def get_agent_skill(name: str):
        skill = ctx.skills.get(name)
        if skill is None:
            raise HTTPException(404, "No such skill.")
        return {**skill.summary(), "body": skill.body()}

    @app.post("/api/agent-skills")
    async def save_agent_skill(body: SkillRequest):
        try:
            skill = ctx.skills.save(body.name, body.description, body.body, body.original)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        audit_settings("ui_skill_save", skill.name, "saved")
        return {**skill.summary(), "body": skill.body()}

    @app.post("/api/agent-skills/{name}/toggle")
    async def toggle_agent_skill(name: str, body: McpToggleRequest):
        try:
            ctx.skills.set_enabled(name, body.enabled)
        except KeyError as exc:
            raise HTTPException(404, "No such skill.") from exc
        return {"skills": [s.summary() for s in ctx.skills.list()]}

    @app.delete("/api/agent-skills/{name}")
    async def delete_agent_skill(name: str):
        try:
            ctx.skills.delete(name)
        except KeyError as exc:
            raise HTTPException(404, "No such skill.") from exc
        audit_settings("ui_skill_delete", name, "moved to Trash")
        return {"skills": [s.summary() for s in ctx.skills.list()]}

    @app.post("/api/agent-skills/import")
    async def import_agent_skill(file: UploadFile = File(...), replace: bool = Form(False)):
        data = await file.read(30_000_001)
        try:
            names = ctx.skills.import_zip(data, replace)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        audit_settings("ui_skill_import", ", ".join(names), "imported from zip")
        return {"added": names, "skills": [s.summary() for s in ctx.skills.list()]}

    @app.post("/api/agent-skills/import-folder")
    async def import_agent_skill_folder(body: SkillImportRequest):
        try:
            names = ctx.skills.import_folder(body.path, body.replace)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        audit_settings("ui_skill_import", ", ".join(names), f"imported from {body.path}")
        return {"added": names, "skills": [s.summary() for s in ctx.skills.list()]}

    # ---- personal preferences ---------------------------------------------------------

    @app.get("/api/preferences")
    async def get_preferences():
        return ctx.preferences.load()

    @app.post("/api/preferences")
    async def save_preferences(body: PreferencesRequest):
        try:
            return ctx.preferences.save(body.model_dump())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/agents")
    async def agents():
        registry = load_registry(settings.arch_dir)
        return [
            {
                "id": agent.id, "name": agent.name, "description": agent.description,
                "capabilities": agent.capabilities, "applications": agent.applications,
                "skills": [skill.id for skill in agent.skills],
            }
            for agent in registry.agents.values()
        ]

    @app.get("/api/skills")
    async def skills():
        registry = load_registry(settings.arch_dir)
        return [
            {
                "id": skill.id, "agent": skill.agent_id, "command": skill.command, "title": skill.title,
                "description": skill.description, "allowed_paths": skill.allowed_paths, "model": skill.model,
                "valid": skill.valid, "errors": skill.errors,
            }
            for agent in registry.agents.values() for skill in agent.skills
        ]

    @app.get("/api/commands")
    async def commands():
        registry = load_registry(settings.arch_dir)
        return [
            {"command": command, "title": skill.title, "description": skill.description,
             "agent": skill.agent_id, "valid": skill.valid}
            for command, skill in sorted(registry.commands.items())
        ]

    @app.get("/api/permissions")
    async def list_permissions():
        perms = permissions()
        integrated = {tool.app for tool in ctx.all_tools().values()}
        return [
            {
                "id": item.id, "name": item.name, "executable": item.executable, "enabled": item.enabled,
                "operations": item.operations, "approval_required": item.approval_required,
                "approval_exempt": item.approval_exempt, "chat_command": item.id in integrated,
            }
            for item in perms.apps.values()
        ]

    # ---- chat ---------------------------------------------------------------

    @app.post("/api/chat")
    async def chat(body: ChatRequest):
        session = session_for(body.session_id)
        current = ctx.runs.get(session.id)
        if current is not None and current.task is not None and not current.task.done():
            raise HTTPException(409, "A task is already running in this conversation.")

        auto_approve = None
        if body.auto_approve:
            # "Allow all actions in this project": must be a real folder inside the workspace that is not a top-level
            # root of the whole workspace, so the checkbox can never mean "everything".
            try:
                auto_guard = FsGuard(settings, permissions(), ["./workspace"])
                folder = auto_guard.resolve(body.auto_approve)
            except PathDenied as exc:
                raise HTTPException(403, f"Cannot allow actions there: {exc}") from exc
            if not folder.is_dir() or folder == settings.workspace_root.resolve():
                raise HTTPException(400, "Choose a project folder to allow actions in.")
            auto_approve = auto_guard.display(folder)
            audit_ui(session, "ui_auto_approve", auto_approve, "success", "allow all actions in project for this message")

        run = AgentRun(
            ctx=ctx, session=session,
            options=ChatOptions(
                message=body.message, active_path=body.active_path, selected_file=body.selected_file,
                selection=body.selection, model=body.model, auto_approve=auto_approve,
                context_files=[f[:500] for f in body.context_files], plan_only=body.plan_only,
                skills=[name[:64] for name in body.skills],
                mcp_servers=[name[:48] for name in body.mcp_servers] if body.mcp_servers is not None else None,
            ),
        )
        run.task = asyncio.create_task(run.run())
        ctx.runs[session.id] = run

        async def stream():
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(run.queue.get(), 15)
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    if event is None:
                        break
                    yield f"data: {json.dumps(event)}\n\n"
            finally:
                # The browser went away: do not leave a task running unattended.
                if not run.task.done():
                    run.task.cancel()

        return StreamingResponse(
            stream(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/chat/stop")
    async def stop(body: SessionRequest):
        run = ctx.runs.get(body.session_id)
        if run is None or run.task is None or run.task.done():
            return {"stopped": False}
        run.task.cancel()
        return {"stopped": True}

    @app.get("/api/session")
    async def get_session(session_id: str = Query(...)):
        session = session_for(session_id)
        return {
            "messages": session.messages,
            "pinned_command": session.pinned_command,
            "active_command": session.active_command,
            "attachments": [
                {"id": a.id, "name": a.name, "size": a.size, "saved_to": a.saved_to}
                for a in session.attachments.values()
            ],
        }

    # ---- chat history (SQLite) -------------------------------------------------

    @app.get("/api/chats")
    async def list_chats(q: str = Query("", max_length=200), limit: int = Query(200, ge=1, le=1000)):
        return {"chats": ctx.db.list_chats(limit, q.strip()) if ctx.db else []}

    @app.get("/api/chats/{chat_id}")
    async def get_chat(chat_id: str):
        session_for(chat_id)  # Validates the id.
        chat = ctx.db.load_chat(chat_id) if ctx.db else None
        if chat is None:
            raise HTTPException(404, "No such conversation.")
        return chat

    @app.post("/api/chats/{chat_id}/rename")
    async def rename_chat(chat_id: str, body: RenameChatRequest):
        session_for(chat_id)
        title = " ".join(body.title.split())
        if not title or not ctx.db or not ctx.db.rename_chat(chat_id, title):
            raise HTTPException(404, "No such conversation.")
        return {"id": chat_id, "title": title}

    @app.delete("/api/chats/{chat_id}")
    async def delete_chat(chat_id: str):
        session = session_for(chat_id)
        run = ctx.runs.get(chat_id)
        if run is not None and run.task is not None and not run.task.done():
            raise HTTPException(409, "This conversation is still running. Stop it first.")
        for attachment in session.attachments.values():
            attachment.path.unlink(missing_ok=True)
        ctx.sessions.forget(chat_id)
        return {"deleted": True}

    # ---- folders opened in the explorer ------------------------------------------

    def folder_list() -> dict:
        return {"folders": [{"path": p.as_posix(), "name": p.name, "exists": p.is_dir()} for p in settings.extra_roots]}

    @app.get("/api/folders")
    async def list_folders():
        return folder_list()

    @app.post("/api/folders")
    async def open_folder(body: FolderRequest):
        """Adds a folder on this computer to the explorer; the assistant may then read and save files there."""
        require_fs("list")
        text = body.path.strip()
        if "\x00" in text:
            raise HTTPException(400, "Invalid path.")
        folder = Path(text).expanduser()
        if not folder.is_absolute():
            raise HTTPException(400, "Give the full path of the folder, for example /Users/you/Documents/reports.")
        folder = folder.resolve()
        if body.create and not folder.exists():
            try:
                folder.mkdir(parents=True)
            except OSError as exc:
                raise HTTPException(400, f"Could not create that folder: {exc.strerror}") from exc
        if not folder.is_dir():
            raise HTTPException(404, "That folder does not exist.")
        if folder == Path(folder.anchor) or folder == Path.home().resolve():
            raise HTTPException(400, "That folder is too broad. Choose a more specific folder.")
        for protected in settings.protected_paths:
            mark = protected.resolve()
            if folder == mark or folder.is_relative_to(mark):
                raise HTTPException(400, "That folder is part of the application and cannot be opened.")
        if any(is_secret_name(part) for part in folder.parts[1:]):
            raise HTTPException(400, "That folder looks like it holds secrets and cannot be opened.")
        if folder not in settings.extra_roots:
            if ctx.db is not None:
                ctx.db.add_folder(folder)
            settings.extra_roots.append(folder)
        ctx.audit.record(session_id="-", agent="user", skill="-", model="-", operation="ui_open_folder",
                         target=str(folder), permission="allowed", approval="user_action", status="success",
                         result="added to explorer")
        return {**folder_list(), "path": folder.as_posix()}

    @app.post("/api/folders/remove")
    async def close_folder(body: FolderRequest):
        """Removes a folder from the explorer. Nothing on disk is touched."""
        folder = Path(body.path.strip()).expanduser().resolve()
        if folder not in settings.extra_roots:
            raise HTTPException(404, "That folder is not open.")
        if ctx.db is not None:
            ctx.db.remove_folder(folder)
        settings.extra_roots.remove(folder)
        return folder_list()

    @app.post("/api/session/reset")
    async def reset_session(body: SessionRequest):
        session_for(body.session_id)
        run = ctx.runs.get(body.session_id)
        if run is not None and run.task is not None and not run.task.done():
            run.task.cancel()
        ctx.sessions.reset(body.session_id)
        return {"reset": True}

    def resolve_approval(body: ApprovalRequest, approved: bool) -> dict:
        pending = ctx.approvals.get(body.approval_id)
        if pending is None or pending.session_id != body.session_id:
            raise HTTPException(404, "This approval request is no longer pending.")
        if not pending.future.done():
            pending.future.set_result(approved)
        return {"approved": approved}

    @app.post("/api/tools/approve")
    async def approve(body: ApprovalRequest):
        return resolve_approval(body, True)

    @app.post("/api/tools/reject")
    async def reject(body: ApprovalRequest):
        return resolve_approval(body, False)

    @app.get("/api/execution/history")
    async def history(session_id: str | None = None, limit: int = Query(200, ge=1, le=1000)):
        return ctx.audit.history(session_id, limit)

    # ---- files --------------------------------------------------------------

    @app.get("/api/files/tree")
    async def tree(session_id: str = Query(...), path: str | None = None):
        session = session_for(session_id)
        require_fs("list")
        guard = guard_for(session)
        try:
            directories = [guard.resolve(path)] if path else guard.roots
        except PathDenied as exc:
            raise HTTPException(403, str(exc)) from exc

        if not path:
            roots = [p for p in directories if p.is_dir()]
            # Your own folders first, then the workspace, then arch/ (agents and skills) last.
            order = {"folder": 0, "workspace": 1, "config": 2}
            roots.sort(key=lambda p: order[guard.root_kind(p)])
            entries = []
            for root in roots:
                kind = guard.root_kind(root)
                name = guard.display(root) if kind == "workspace" else root.name
                if kind == "config":
                    name = "arch · agents & skills"
                entries.append({"name": name, "path": guard.display(root), "type": "dir", "root": kind,
                                "title": str(root)})
            return {"roots": guard.root_labels(), "entries": entries}
        if not directories[0].is_dir():
            raise HTTPException(404, "Not a directory.")
        entries = []
        for child in sorted(directories[0].iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if child.name.startswith(".tmp-write-") or not guard.allows(child):
                continue
            entries.append({
                "name": child.name,
                "path": guard.display(child.resolve()),
                "type": "dir" if child.is_dir() else "file",
            })
        return {"roots": guard.root_labels(), "entries": entries}

    @app.get("/api/files/search")
    async def search(session_id: str = Query(...), q: str = Query(..., min_length=1, max_length=200)):
        session = session_for(session_id)
        require_fs("search")
        guard = guard_for(session)
        needle = q.lower()
        matches: list[dict] = []
        skip = {".git", "node_modules", "__pycache__", ".venv", "venv"}
        for root in guard.roots:
            for current, dirs, files in os.walk(root, followlinks=False):
                dirs[:] = [d for d in dirs if d not in skip]
                for name in files:
                    full = Path(current) / name
                    if needle in name.lower() and guard.allows(full):
                        matches.append({"name": name, "path": guard.display(full), "type": "file"})
                if len(matches) >= 100:
                    return {"entries": matches[:100]}
        return {"entries": matches}

    @app.get("/api/files/read")
    async def read_file(session_id: str = Query(...), path: str = Query(...)):
        session = session_for(session_id)
        require_fs("read")
        guard = guard_for(session)
        try:
            resolved = guard.resolve(path)
            if not resolved.is_file():
                raise HTTPException(404, "Not a file.")
            document = resolved.suffix.lower() in DOCUMENT_TYPES
            if document:
                # PDF, Word and Excel are shown as their text, read-only: saving would destroy the original file.
                content, truncated = extract_text(resolved, settings.max_read_bytes * 5)
            else:
                content, truncated = read_text_limited(resolved, settings.max_read_bytes * 5)
        except PathDenied as exc:
            audit_ui(session, "ui_read", path, "denied", str(exc))
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(415, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(400, f"Could not read this file: {exc.strerror}") from exc
        return {
            "path": guard.display(resolved), "content": content, "truncated": truncated or document,
            "readonly": document, "mtime": resolved.stat().st_mtime,
        }

    @app.post("/api/files/write")
    async def write_file(body: WriteRequest):
        session = session_for(body.session_id)
        require_fs("write")
        guard = guard_for(session)
        try:
            resolved = guard.resolve(body.path)
            guard.resolve(str(resolved.parent))
        except PathDenied as exc:
            audit_ui(session, "ui_write", body.path, "denied", str(exc))
            raise HTTPException(403, str(exc)) from exc
        if resolved.is_dir():
            raise HTTPException(400, "That path is a directory.")
        if body.expected_mtime is not None and resolved.exists() and resolved.stat().st_mtime != body.expected_mtime:
            raise HTTPException(409, "This file changed on disk after you opened it.")
        resolved.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(resolved, body.content)
        label = guard.display(resolved)
        audit_ui(session, "ui_write", label, "success", f"{len(body.content.encode())} bytes")
        return {"path": label, "mtime": resolved.stat().st_mtime}

    @app.post("/api/files/upload")
    async def upload(
        session_id: str = Form(...), file: UploadFile = File(...), target_dir: str | None = Form(None),
        attach: bool = Form(True),
    ):
        session = session_for(session_id)
        destination: Path | None = None
        attachment_id = new_attachment_id()
        try:
            extensions, max_bytes = upload_rules(permissions())
            name = validate_name(file.filename or "", extensions)
            if target_dir:
                # Saving into a project needs the same rights as any other write.
                require_fs("write")
                guard = guard_for(session)
                folder = guard.resolve(target_dir)
                if not folder.is_dir():
                    raise UploadRejected("the target folder does not exist")
                destination = guard.resolve(str(folder / name))
                if destination.exists():
                    if not attach:
                        raise UploadRejected(f"'{guard.display(destination)}' already exists")
                    destination = guard.resolve(str(copy_name(folder, name)))  # Saved as "name copy.ext".

            head = await file.read(CHUNK)
            kind = validate_content(name, head)
            stored = store_path(settings.uploads_dir, session.id, attachment_id, name)
            size = 0
            fd = os.open(stored, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # Never executable.
            try:
                with os.fdopen(fd, "wb") as handle:
                    chunk = head
                    while chunk:
                        size += len(chunk)
                        if size > max_bytes:
                            raise UploadRejected(f"file exceeds the {max_bytes // CHUNK} MB limit")
                        handle.write(chunk)
                        chunk = await file.read(CHUNK)
            except BaseException:
                stored.unlink(missing_ok=True)
                raise
        except UploadRejected as exc:
            audit_ui(session, "upload", file.filename or "(unnamed)", "denied", str(exc))
            raise HTTPException(400, str(exc)) from exc
        except PathDenied as exc:
            audit_ui(session, "upload", file.filename or "(unnamed)", "denied", str(exc))
            raise HTTPException(403, str(exc)) from exc

        saved_to = None
        if destination is not None:
            destination.write_bytes(stored.read_bytes())
            saved_to = guard_for(session).display(destination)
        attachment = Attachment(id=attachment_id, name=name, path=stored, size=size, kind=kind, saved_to=saved_to)
        if not attach and destination is not None:
            stored.unlink(missing_ok=True)  # Saved to the project only; not offered to the chat.
            audit_ui(session, "upload", saved_to, "success", f"{size} bytes")
            return {"id": None, "name": name, "size": size, "saved_to": saved_to}
        session.attachments[attachment_id] = attachment
        audit_ui(session, "upload", name, "success", f"{size} bytes")
        return {"id": attachment_id, "name": name, "size": size, "saved_to": saved_to}

    @app.delete("/api/files/upload/{attachment_id}")
    async def remove_upload(attachment_id: str, session_id: str = Query(...)):
        session = session_for(session_id)
        attachment = session.attachments.pop(attachment_id, None)
        if attachment is None:
            raise HTTPException(404, "No such attachment.")
        attachment.path.unlink(missing_ok=True)
        return {"removed": True}

    # ---- projects and dashboards ----------------------------------------------

    DATA_SUFFIXES = dataviz.SUPPORTED - {".txt", ".dat"}

    @app.get("/api/projects")
    async def list_projects(session_id: str = Query(...)):
        session = session_for(session_id)
        require_fs("list")
        guard = guard_for(session)
        projects = []
        for root in guard.roots:
            if not root.is_dir():
                continue
            for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
                if not child.is_dir() or child.name.startswith(".") or not guard.allows(child):
                    continue
                datasets = sum(
                    1 for f in child.rglob("*")
                    if f.suffix.lower() in DATA_SUFFIXES and f.is_file() and not f.is_symlink() and guard.allows(f)
                )
                projects.append({"name": child.name, "path": guard.display(child.resolve()), "datasets": datasets})
        return {"projects": projects}

    @app.post("/api/projects")
    async def create_project(body: ProjectRequest):
        session = session_for(body.session_id)
        require_fs("create")
        name = body.name.strip()
        if not re.fullmatch(r"[A-Za-z0-9][\w .-]{0,58}", name):
            raise HTTPException(400, "Use letters, numbers, spaces, dots, dashes or underscores.")
        guard = guard_for(session)
        parent = next((r for r in guard.roots if r.is_dir()), None)
        if parent is None:
            raise HTTPException(403, "This skill has no folder to create projects in.")
        try:
            folder = guard.resolve(str(parent / name))
        except PathDenied as exc:
            audit_ui(session, "ui_create_project", name, "denied", str(exc))
            raise HTTPException(403, str(exc)) from exc
        if folder.exists():
            raise HTTPException(409, f"A project named '{name}' already exists.")
        (folder / "data").mkdir(parents=True)
        label = guard.display(folder)
        audit_ui(session, "ui_create_project", label, "success", "created")
        return {"name": folder.name, "path": label}

    @app.post("/api/files/mkdir")
    async def make_folder(body: MkdirRequest):
        """Creates a folder (and any missing parents) inside the permitted paths."""
        session = session_for(body.session_id)
        require_fs("create")
        guard = guard_for(session)
        if any(p in {".", ".."} for p in body.path.replace("\\", "/").split("/")):
            raise HTTPException(400, "Folder names may use letters, numbers, spaces, dots, dashes and underscores.")
        try:
            folder = guard.resolve(body.path)
            guard.resolve(str(folder.parent))
        except PathDenied as exc:
            audit_ui(session, "ui_mkdir", body.path, "denied", str(exc))
            raise HTTPException(403, str(exc)) from exc
        # Only the folders being created are checked; existing parents may have any name.
        new_parts, probe = [], folder
        while not probe.exists() and probe != probe.parent:
            new_parts.append(probe.name)
            probe = probe.parent
        if any(not re.fullmatch(r"[A-Za-z0-9_][\w .()-]{0,80}", p) for p in new_parts):
            raise HTTPException(400, "Folder names may use letters, numbers, spaces, dots, dashes and underscores.")
        if folder.exists() and not folder.is_dir():
            raise HTTPException(409, "A file with that name already exists.")
        existed = folder.is_dir()
        folder.mkdir(parents=True, exist_ok=True)
        label = guard.display(folder)
        if not existed:
            audit_ui(session, "ui_mkdir", label, "success", "created")
        return {"path": label, "created": not existed}

    def existing(guard: FsGuard, session: Session, raw: str, operation: str) -> Path:
        try:
            path = guard.resolve(raw)
        except PathDenied as exc:
            audit_ui(session, operation, raw, "denied", str(exc))
            raise HTTPException(403, str(exc)) from exc
        if not path.exists():
            raise HTTPException(404, f"'{raw}' does not exist.")
        if any(path == root.resolve() for root in guard.roots):
            raise HTTPException(400, "A top-level workspace folder cannot be renamed, moved or deleted.")
        return path

    def new_target(guard: FsGuard, session: Session, raw: str, operation: str) -> Path:
        try:
            target = guard.resolve(raw)
            guard.resolve(str(target.parent))
        except PathDenied as exc:
            audit_ui(session, operation, raw, "denied", str(exc))
            raise HTTPException(403, str(exc)) from exc
        check_name(target.name)
        return target

    @app.post("/api/files/create")
    async def create_file(body: PathRequest):
        """Creates an empty file. Missing parent folders are created too, so 'src/utils/a.py' works in one step."""
        session = session_for(body.session_id)
        require_fs("create")
        guard = guard_for(session)
        for part in [p for p in body.path.replace("\\", "/").split("/") if p]:
            check_name(part)
        target = new_target(guard, session, body.path, "ui_create_file")
        if target.exists():
            raise HTTPException(409, f"'{guard.display(target)}' already exists.")
        target.parent.mkdir(parents=True, exist_ok=True)
        os.close(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644))
        label = guard.display(target)
        audit_ui(session, "ui_create_file", label, "success", "created")
        return {"path": label}

    @app.post("/api/files/rename")
    async def rename_path(body: RenameRequest):
        """Renames or moves a file or folder inside the permitted paths. Never overwrites."""
        session = session_for(body.session_id)
        require_fs("rename")
        guard = guard_for(session)
        source = existing(guard, session, body.path, "ui_rename")
        target = new_target(guard, session, body.new_path, "ui_rename")
        if not target.parent.is_dir():
            raise HTTPException(404, "The destination folder does not exist.")
        if source.is_dir() and (target == source or target.is_relative_to(source)):
            raise HTTPException(400, "A folder cannot be moved into itself.")
        # A case-only rename ('readme.md' -> 'README.md') points at the same file on macOS; that is allowed.
        if target.exists() and not (target.parent.samefile(source.parent) and target.name.lower() == source.name.lower()):
            raise HTTPException(409, f"'{guard.display(target)}' already exists.")
        os.rename(source, target)
        label = guard.display(target)
        audit_ui(session, "ui_rename", f"{guard.display(source)} -> {label}", "success", "renamed")
        return {"path": label}

    @app.post("/api/files/copy")
    async def copy_path(body: CopyRequest):
        """Copies a file or folder into dest_dir. A name clash gets ' copy' added, like VS Code's paste."""
        session = session_for(body.session_id)
        require_fs("create")
        guard = guard_for(session)
        try:
            source = guard.resolve(body.path)
            folder = guard.resolve(body.dest_dir)
        except PathDenied as exc:
            audit_ui(session, "ui_copy", body.path, "denied", str(exc))
            raise HTTPException(403, str(exc)) from exc
        if not source.exists():
            raise HTTPException(404, f"'{body.path}' does not exist.")
        if not folder.is_dir():
            raise HTTPException(404, "The destination folder does not exist.")
        if source.is_dir() and (folder == source or folder.is_relative_to(source)):
            raise HTTPException(400, "A folder cannot be copied into itself.")
        target = new_target(guard, session, str(copy_name(folder, source.name)), "ui_copy")
        if source.is_dir():
            # symlinks=True copies links as links, so nothing outside the workspace is pulled in.
            shutil.copytree(source, target, symlinks=True)
        else:
            shutil.copy2(source, target)
        label = guard.display(target)
        audit_ui(session, "ui_copy", f"{guard.display(source)} -> {label}", "success", "copied")
        return {"path": label}

    @app.post("/api/files/delete")
    async def delete_path(body: PathRequest):
        """Moves a file or folder to the system Trash, so it can be restored."""
        session = session_for(body.session_id)
        require_fs("delete")
        guard = guard_for(session)
        target = existing(guard, session, body.path, "ui_delete")
        try:
            from send2trash import send2trash
        except ImportError as exc:
            raise HTTPException(500, "Deleting needs the send2trash package: pip install send2trash") from exc
        label = guard.display(target)
        try:
            send2trash(str(target))
        except OSError as exc:
            audit_ui(session, "ui_delete", label, "error", str(exc))
            raise HTTPException(500, f"Could not move '{label}' to the Trash: {exc}") from exc
        audit_ui(session, "ui_delete", label, "success", "moved to Trash")
        return {"path": label, "trashed": True}

    @app.post("/api/files/open")
    async def open_outside(body: OpenRequest, request: Request):
        """Opens a generated file outside the app: dashboards (.html) in the default browser, documents, slides and
        spreadsheets in their default app, or shows the file in Finder."""
        from core import launcher
        session = session_for(body.session_id)
        require_fs("read")
        guard = guard_for(session)
        candidates = [body.path]
        if body.base and not Path(body.path).is_absolute():
            # The assistant often names outputs relative to the project it works in.
            candidates.append(f"{body.base.rstrip('/')}/{body.path}")
        target, denied = None, None
        for candidate in candidates:
            try:
                resolved = guard.resolve(candidate)
            except PathDenied as exc:
                denied = denied or exc
                continue
            if resolved.exists():
                target = resolved
                break
            target = target or resolved
        if target is None:
            audit_ui(session, "ui_open", body.path, "denied", str(denied))
            raise HTTPException(403, str(denied))
        if not target.exists():
            raise HTTPException(404, f"'{body.path}' does not exist (it may have been moved or deleted).")
        label = guard.display(target)
        suffix = target.suffix.lower()
        if body.reveal:
            launcher.reveal(target)
            how = "shown in Finder"
        elif target.is_file() and suffix in launcher.BROWSER_PAGES and not target.is_symlink():
            url = f"{str(request.base_url).rstrip('/')}/api/dashboards/view?{urlencode({'session_id': session.id, 'path': label})}"
            launcher.launch(url)
            how = "opened in the browser"
        elif target.is_file() and suffix in launcher.OPENABLE:
            launcher.launch(str(target))
            how = "opened in its default app"
        else:
            raise HTTPException(415, f"{suffix or 'This'} files are opened in the Files window, not outside the app.")
        audit_ui(session, "ui_open", label, "success", how)
        return {"path": label, "opened": how}

    @app.post("/api/files-window")
    async def open_files_window(body: FilesWindowRequest, request: Request):
        """Opens the Files window. The desktop app opens a native window; the browser version asks the system to open
        it in a new browser tab. Either way no pop-up is needed, so pop-up blockers cannot stop it."""
        from core import launcher
        hook = getattr(app.state, "open_files_window", None)
        if hook is not None:
            hook(body.path)
            return {"opened": "window"}
        query = f"?{urlencode({'open': body.path})}" if body.path else ""
        launcher.launch(f"{str(request.base_url).rstrip('/')}/workspace{query}")
        return {"opened": "browser"}

    @app.get("/api/dashboards/view")
    async def view_dashboard(session_id: str = Query(...), path: str = Query(...)):
        """Serves a generated dashboard with a policy that allows styles and images but no scripts."""
        session = session_for(session_id)
        require_fs("read")
        guard = guard_for(session)
        try:
            resolved = guard.resolve(path)
        except PathDenied as exc:
            raise HTTPException(403, str(exc)) from exc
        if resolved.suffix.lower() != ".html" or not resolved.is_file() or resolved.is_symlink():
            raise HTTPException(404, "Not an HTML file.")
        try:
            content, _ = read_text_limited(resolved, 5_000_000)
        except (ValueError, OSError) as exc:
            raise HTTPException(400, "Could not read this file.") from exc
        return Response(
            content, media_type="text/html; charset=utf-8",
            headers={
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
                                           "base-uri 'none'; form-action 'none'; frame-ancestors 'self'",
                "X-Frame-Options": "SAMEORIGIN", "Cache-Control": "no-store",
            },
        )

    # ---- interface ----------------------------------------------------------

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(PROJECT_ROOT / "index.html")

    @app.get("/workspace", include_in_schema=False)
    async def workspace_window():
        """The Files window: explorer and editor, opened beside the chat."""
        return FileResponse(PROJECT_ROOT / "workspace.html")

    app.mount("/static", StaticFiles(directory=PROJECT_ROOT / "static"), name="static")
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    config = app.state.ctx.settings
    print(f"Agentic Chatbox: http://{config.host}:{config.port}")
    uvicorn.run(app, host=config.host, port=config.port, log_level="warning")
