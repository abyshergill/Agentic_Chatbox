import asyncio
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.agent import AgentRun, ChatOptions
from core.config import PROJECT_ROOT, Settings
from core.ollama_client import ChatTurn, ToolCall
from main import create_app


class FakeOllama:
    """Plays back a scripted model, so tests exercise the real enforcement code."""

    def __init__(self):
        self.script = []
        self.calls = []
        self.tool_names = []
        self.models = ["fake:model", "qwen2.5-coder:3b"]
        self.native = True

    async def list_models(self):
        return self.models

    async def supports_tools(self, model):
        return self.native

    async def chat(self, model, messages, tools, allowed, on_token):
        self.calls.append({"model": model, "messages": [dict(m) for m in messages]})
        self.tool_names.append(sorted(allowed))
        step = self.script.pop(0) if self.script else "done"
        if isinstance(step, str):
            await on_token(step)
            return ChatTurn(content=step)
        return ChatTurn(tool_calls=[ToolCall(name, args) for name, args in step])


@pytest.fixture(autouse=True)
def no_local_server_probing(monkeypatch):
    """Tests never look for a real LM Studio on this machine; tests that need it opt in."""
    monkeypatch.setenv("WORKSPACE_DETECT_LOCAL", "0")


@pytest.fixture(autouse=True)
def no_real_trash(monkeypatch):
    """Tests never touch the real Trash: 'moving to Trash' just removes the temporary file."""
    import os
    import shutil as _shutil

    import send2trash

    monkeypatch.setattr(send2trash, "send2trash", lambda p: _shutil.rmtree(p) if os.path.isdir(p) else os.remove(p))


@pytest.fixture
def base(tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    shutil.copytree(PROJECT_ROOT / "arch", root / "arch")
    project = root / "workspace" / "projects" / "demo"
    project.mkdir(parents=True)
    (project / "hello.py").write_text("print('hi')\n")
    (project / ".env").write_text("TOKEN=abc\n")
    (tmp_path / "outside.txt").write_text("host secret\n")
    return root


@pytest.fixture
def settings(base):
    return Settings(base_dir=base)


@pytest.fixture
def app(settings):
    application = create_app(settings)
    application.state.ctx.ollama = FakeOllama()
    return application


@pytest.fixture
def ctx(app):
    return app.state.ctx


@pytest.fixture
def client(app):
    return TestClient(app, base_url="http://127.0.0.1:8000")


@pytest.fixture
def run_chat(ctx):
    """Runs one message and answers approvals with `decide(event) -> bool`."""

    def runner(message, decide=lambda event: True, session_id="testsession01", on_event=None, **options):
        session = ctx.sessions.get(session_id)
        run = AgentRun(ctx=ctx, session=session, options=ChatOptions(message=message, **options))

        async def drive():
            run.task = asyncio.create_task(run.run())
            events = []
            while True:
                event = await run.queue.get()
                if event is None:
                    break
                events.append(event)
                if on_event:
                    on_event(event, run)
                if event["type"] == "approval_required":
                    ctx.approvals[event["approval_id"]].future.set_result(decide(event))
            await run.task
            return events

        return asyncio.run(drive())

    return runner


def of_type(events, kind):
    return [event for event in events if event["type"] == kind]


def set_permission(base: Path, app: str, field: str, value: str):
    path = base / "arch" / "permission.md"
    lines = path.read_text().splitlines()
    inside = False
    for index, line in enumerate(lines):
        if line.startswith("#") and not line.startswith("# "):
            inside = line.strip() == f"#{app}"
        if inside and line.startswith(f"{field}:"):
            lines[index] = f"{field}: {value}"
    path.write_text("\n".join(lines) + "\n")
