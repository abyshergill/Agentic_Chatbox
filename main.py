"""Local AI Workspace: FastAPI entry point. Run with `python main.py`."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core.agent import AgentRun, ChatOptions
from core.app_context import AppContext
from core.arch_loader import load_registry
from core.config import PROJECT_ROOT, Settings
from core.fs_guard import FsGuard, PathDenied, atomic_write, read_text_limited
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
        yield
        if hasattr(ctx.llm, "shutdown"):
            ctx.llm.shutdown()  # Stops the llama.cpp server for .gguf models.

    app = FastAPI(title="Local AI Workspace", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
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
        """The file view follows the active skill, falling back to the default skill."""
        registry = load_registry(settings.arch_dir)
        command = session.pinned_command or session.active_command
        skill = registry.skill_for(command) if command else None
        if skill is None or not skill.valid:
            skill = registry.default_skill
        paths = skill.allowed_paths if skill is not None and skill.valid else []
        return FsGuard(settings, permissions(), paths)

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
        return {
            "status": "ok",
            "name": registry.system.name,
            "ollama": ollama,
            "catalog": catalog,
            "isolation": isolation_mode(),
            "warnings": registry.warnings + perms.warnings,
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
            "presets": PRESETS,
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
        integrated = {tool.app for tool in ctx.tools.values()}
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
                folder = FsGuard(settings, permissions(), ["./workspace"]).resolve(body.auto_approve)
            except PathDenied as exc:
                raise HTTPException(403, f"Cannot allow actions there: {exc}") from exc
            if not folder.is_dir() or folder == settings.workspace_root.resolve():
                raise HTTPException(400, "Choose a project folder to allow actions in.")
            auto_approve = folder.relative_to(settings.workspace_root.resolve()).as_posix()
            audit_ui(session, "ui_auto_approve", auto_approve, "success", "allow all actions in project for this message")

        run = AgentRun(
            ctx=ctx, session=session,
            options=ChatOptions(
                message=body.message, active_path=body.active_path, selected_file=body.selected_file,
                selection=body.selection, model=body.model, auto_approve=auto_approve,
                context_files=[f[:500] for f in body.context_files], plan_only=body.plan_only,
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
                {"id": a.id, "name": a.name, "size": a.size} for a in session.attachments.values()
            ],
        }

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
            return {
                "roots": guard.root_labels(),
                "entries": [{"name": guard.display(p), "path": guard.display(p), "type": "dir"} for p in roots],
            }
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
                    raise UploadRejected(f"'{guard.display(destination)}' already exists")

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

        attachment = Attachment(id=attachment_id, name=name, path=stored, size=size, kind=kind)
        saved_to = None
        if destination is not None:
            destination.write_bytes(stored.read_bytes())
            saved_to = guard_for(session).display(destination)
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

    DATA_SUFFIXES = {".csv", ".tsv", ".json", ".jsonl"}

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
        parts = [p for p in body.path.replace("\\", "/").split("/") if p]
        if any(not re.fullmatch(r"[A-Za-z0-9_][\w .-]{0,80}", p) or p in {".", ".."} for p in parts):
            raise HTTPException(400, "Folder names may use letters, numbers, spaces, dots, dashes and underscores.")
        try:
            folder = guard.resolve(body.path)
            guard.resolve(str(folder.parent))
        except PathDenied as exc:
            audit_ui(session, "ui_mkdir", body.path, "denied", str(exc))
            raise HTTPException(403, str(exc)) from exc
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

    app.mount("/static", StaticFiles(directory=PROJECT_ROOT / "static"), name="static")
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn

    config = app.state.ctx.settings
    print(f"Local AI Workspace: http://{config.host}:{config.port}")
    uvicorn.run(app, host=config.host, port=config.port, log_level="warning")
