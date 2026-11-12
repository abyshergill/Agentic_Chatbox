"""Connectors (MCP servers), Agent Skills, preferences and model downloads."""

import asyncio
import io
import sys
import zipfile
from pathlib import Path

import pytest

from core.agent import AgentRun, ChatOptions
from core.agent_skills import SkillLibrary, parse_front_matter, render_skill
from core.downloads import file_name_for, normalize_gguf_url
from core.mcp_hub import McpHub, check_server, tool_name
from tests.conftest import of_type, set_permission

DEMO = str(Path(__file__).parent / "fixtures" / "mcp_demo_server.py")


# ---- MCP config and naming ---------------------------------------------------------


def test_check_server_accepts_claude_desktop_shapes():
    local = check_server("files", {"command": "npx", "args": ["-y", "pkg", 3], "env": {"TOKEN": 1}})
    assert local == {"command": "npx", "args": ["-y", "pkg", "3"], "env": {"TOKEN": "1"}, "alwaysAllow": []}
    remote = check_server("web", {"url": "https://example.com/mcp", "type": "sse", "headers": {"A": "b"}})
    assert remote["transport"] == "sse" and remote["headers"] == {"A": "b"}
    for bad in [("bad name!", {"command": "x"}), ("ok", {}), ("ok", {"url": "ftp://x"}), ("ok", {"command": "x", "args": "no"})]:
        with pytest.raises(ValueError):
            check_server(*bad)


def test_tool_names_are_safe_and_unique():
    taken = set()
    first = tool_name("my server", "read.file", taken)
    assert first == "mcp_my_server_read_file"
    taken.add(first)
    assert tool_name("my server", "read file", taken) == "mcp_my_server_read_file_2"
    assert len(tool_name("s", "x" * 200, set())) == 64


# ---- MCP through the agent -------------------------------------------------------------


async def drive(ctx, message, decide=lambda e: True, **options):
    session = ctx.sessions.get("mcpsession01")
    run = AgentRun(ctx=ctx, session=session, options=ChatOptions(message=message, **options))
    run.task = asyncio.create_task(run.run())
    events = []
    while (event := await run.queue.get()) is not None:
        events.append(event)
        if event["type"] == "approval_required":
            ctx.approvals[event["approval_id"]].future.set_result(decide(event))
    await run.task
    return events


def test_mcp_tools_run_through_approval_and_always_allow(ctx):
    ctx.mcp = McpHub(ctx.settings)

    async def scenario():
        await ctx.mcp.save_server("demo", {"command": sys.executable, "args": [DEMO]})
        assert ctx.mcp.status()[0]["status"] == "connected"
        assert {"mcp_demo_add", "mcp_demo_shout"} <= set(ctx.all_tools())

        # Asks first, shows the arguments, and identifies the server and tool for "Always allow".
        ctx.ollama.script = [[("mcp_demo_shout", {"text": "hi"})], "It said HI."]
        events = await drive(ctx, "shout hi")
        approval = of_type(events, "approval_required")[0]
        assert approval["mcp"] == {"server": "demo", "tool": "shout"} and '"text": "hi"' in approval["preview"]
        assert of_type(events, "tool_result")[0]["output"] == "HI"

        # Declined: the tool does not run.
        ctx.ollama.script = [[("mcp_demo_shout", {"text": "no"})], "ok"]
        events = await drive(ctx, "shout no", decide=lambda e: False)
        assert of_type(events, "tool_result")[0]["status"] == "rejected"

        # Always allow: runs without asking.
        ctx.mcp.set_always_allow("demo", "shout", True)
        ctx.ollama.script = [[("mcp_demo_shout", {"text": "yo"})], "ok"]
        events = await drive(ctx, "shout yo")
        assert not of_type(events, "approval_required") and of_type(events, "auto_approved")
        assert of_type(events, "tool_result")[0]["output"] == "YO"

        # Switched off for this chat: not offered, and refused if called anyway.
        ctx.ollama.script = [[("mcp_demo_add", {"a": 1, "b": 2})], "ok"]
        events = await drive(ctx, "add", mcp_servers=[])
        assert "mcp_demo_add" not in ctx.ollama.tool_names[-2]
        assert of_type(events, "tool_result")[0]["status"] == "denied"

        # Plan mode only offers tools the server marks read-only.
        ctx.ollama.script = ["plan"]
        await drive(ctx, "plan it", plan_only=True)
        assert "mcp_demo_add" in ctx.ollama.tool_names[-1] and "mcp_demo_shout" not in ctx.ollama.tool_names[-1]
        await ctx.mcp.stop_all()

    asyncio.run(scenario())


def test_disabling_mcp_in_permission_file_removes_its_tools(ctx, base):
    set_permission(base, "mcp", "Access", "disabled")
    ctx.mcp = McpHub(ctx.settings)

    async def scenario():
        await ctx.mcp.save_server("demo", {"command": sys.executable, "args": [DEMO]})
        ctx.ollama.script = [[("mcp_demo_add", {"a": 1, "b": 2})], "ok"]
        events = await drive(ctx, "add")
        assert of_type(events, "tool_result")[0]["status"] == "denied"
        await ctx.mcp.stop_all()

    asyncio.run(scenario())


def test_broken_servers_report_errors_without_raising(settings):
    hub = McpHub(settings)

    async def scenario():
        await hub.save_server("missing", {"command": "no-such-program-xyz"})
        await hub.import_servers({"mcpServers": {"bad": {"command": sys.executable, "args": ["-c", "import sys; sys.exit(3)"]}}})
        rows = {row["name"]: row for row in hub.status()}
        assert rows["missing"]["status"] == "error" and "not found" in rows["missing"]["error"]
        assert rows["bad"]["status"] == "error"
        assert hub.tools() == {}
        await hub.set_enabled("missing", False)
        assert {row["name"]: row for row in hub.status()}["missing"]["status"] == "disabled"
        assert await hub.remove_server("missing") and not await hub.remove_server("missing")

    asyncio.run(scenario())


def test_mcp_endpoints_never_return_secrets(client, ctx):
    ctx.mcp.store.save({"svc": {"command": "no-such-program-xyz", "env": {"API_TOKEN": "secret-value-1234"}, "disabled": True}})
    detail = client.get("/api/mcp/servers/svc").json()
    assert "secret-value" not in str(detail) and detail["config"]["env"]["API_TOKEN"].endswith("1234")
    # Saving the masked value back keeps the real one.
    client.post("/api/mcp/servers", json={"name": "svc", "original": "svc", "config": {**detail["config"], "disabled": True}})
    assert ctx.mcp.store.load()["svc"]["env"]["API_TOKEN"] == "secret-value-1234"
    assert client.get("/api/mcp").json()["servers"][0]["env_keys"] == ["API_TOKEN"]
    assert client.post("/api/mcp/import", json={"config": {"nope": 1}}).status_code == 400


# ---- Agent Skills ----------------------------------------------------------------------------


def test_front_matter_round_trip():
    text = render_skill("weekly-report", 'Use when: "status" or weekly report', "# Steps\n1. Do it")
    meta, body = parse_front_matter(text)
    assert meta == {"name": "weekly-report", "description": 'Use when: "status" or weekly report'}
    assert body.strip() == "# Steps\n1. Do it"
    meta, _ = parse_front_matter("---\nname: x\ndescription: >\n  folded\n  text\n---\nbody")
    assert meta["description"] == "folded text"


@pytest.fixture
def library(tmp_path):
    return SkillLibrary(tmp_path / "skills", tmp_path / "data" / "skills.json")


def test_skill_library_crud(library):
    library.save("notes", "Take notes. Use for meetings.", "Write notes.")
    (library.root / "notes" / "template.md").write_text("TEMPLATE")
    assert [s.name for s in library.active()] == ["notes"]
    assert library.read_file("notes", "template.md") == "TEMPLATE"
    with pytest.raises(PermissionError):
        library.read_file("notes", "../../data/skills.json")
    library.set_enabled("notes", False)
    assert library.active() == [] and library.get("notes").enabled is False
    library.save("meeting-notes", "Take notes.", "New body", original="notes")
    assert library.get("notes") is None and library.get("meeting-notes").enabled is False
    with pytest.raises(ValueError):
        library.save("Bad Name", "x", "y")
    with pytest.raises(ValueError):
        library.save("meeting-notes", "x", "y")  # Exists already.
    library.delete("meeting-notes")
    assert library.list() == []


def zip_of(files: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return buffer.getvalue()


def test_skill_zip_import_is_safe(library):
    good = zip_of({"report/SKILL.md": "---\nname: report\ndescription: Reports.\n---\nDo it", "report/ref.md": "R"})
    assert library.import_zip(good) == ["report"]
    assert library.read_file("report", "ref.md") == "R"
    with pytest.raises(ValueError, match="already installed"):
        library.import_zip(good)
    assert library.import_zip(good, replace=True) == ["report"]
    with pytest.raises(ValueError, match="Unsafe"):
        library.import_zip(zip_of({"../evil/SKILL.md": "---\nname: evil\ndescription: x\n---\n"}))
    with pytest.raises(ValueError, match="No SKILL.md"):
        library.import_zip(zip_of({"readme.txt": "hi"}))
    with pytest.raises(ValueError):
        library.import_zip(b"not a zip")


def test_skills_reach_the_model(ctx, settings, run_chat):
    library = ctx.skills
    library.save("haiku", "Write haiku. Use when the user wants a poem.", "Always write 5-7-5.")
    (settings.skills_dir / "haiku" / "examples.md").write_text("An old silent pond")

    # The catalog is in the system prompt and use_skill loads the instructions.
    ctx.ollama.script = [[("use_skill", {"name": "haiku"})], [("read_skill_file", {"name": "haiku", "path": "examples.md"})], "done"]
    events = run_chat("write a poem")
    system = ctx.ollama.calls[0]["messages"][0]["content"]
    assert "## Agent Skills" in system and "- haiku: Write haiku." in system
    results = of_type(events, "tool_result")
    assert "Always write 5-7-5." in results[0]["output"] and "examples.md" in results[0]["output"]
    assert results[1]["output"] == "An old silent pond"
    assert not of_type(events, "approval_required")  # Reading skills needs no approval.

    # Chosen in the composer: the instructions go straight into the prompt.
    ctx.ollama.script = ["ok"]
    run_chat("a poem please", session_id="testsession02", skills=["haiku", "missing"])
    assert "## Skills the user turned on for this message" in ctx.ollama.calls[-1]["messages"][0]["content"]
    assert "Always write 5-7-5." in ctx.ollama.calls[-1]["messages"][0]["content"]

    # Disabled skills are neither listed nor loadable.
    library.set_enabled("haiku", False)
    ctx.ollama.script = [[("use_skill", {"name": "haiku"})], "ok"]
    events = run_chat("poem", session_id="testsession03")
    assert "## Agent Skills" not in ctx.ollama.calls[-2]["messages"][0]["content"]  # It was the only skill.
    assert of_type(events, "tool_result")[0]["status"] == "failed"


def test_file_tools_cannot_touch_skills(ctx, run_chat):
    ctx.skills.save("safe", "Safe skill.", "Body")
    ctx.ollama.script = [[("read_file", {"path": str(ctx.settings.skills_dir / "safe" / "SKILL.md")})], "ok"]
    events = run_chat("read it")
    assert of_type(events, "tool_result")[0]["status"] == "denied"


def test_skill_endpoints(client):
    response = client.post("/api/agent-skills", json={"name": "demo-skill", "description": "Demo. Use for demos.", "body": "Steps"})
    assert response.status_code == 200 and response.json()["body"] == "Steps"
    assert [s["name"] for s in client.get("/api/agent-skills").json()["skills"]] == ["demo-skill"]
    assert client.post("/api/agent-skills/demo-skill/toggle", json={"enabled": False}).json()["skills"][0]["enabled"] is False
    files = {"file": ("s.zip", zip_of({"SKILL.md": "---\nname: zipped\ndescription: Z.\n---\nZ"}), "application/zip")}
    assert client.post("/api/agent-skills/import", files=files).json()["added"] == ["zipped"]
    assert client.delete("/api/agent-skills/demo-skill").status_code == 200
    assert client.get("/api/agent-skills/demo-skill").status_code == 404


# ---- preferences --------------------------------------------------------------------------------


def test_preferences_are_added_to_the_prompt(client, ctx, run_chat):
    saved = client.post("/api/preferences", json={"display_name": "Sam", "custom_instructions": "Answer in French."}).json()
    assert saved == {"display_name": "Sam", "custom_instructions": "Answer in French."}
    ctx.ollama.script = ["bonjour"]
    run_chat("hello")
    system = ctx.ollama.calls[-1]["messages"][0]["content"]
    assert "The user's name is Sam." in system and "Answer in French." in system
    assert client.post("/api/preferences", json={"custom_instructions": "x" * 9000}).status_code == 422


# ---- model downloads -------------------------------------------------------------------------------


def test_gguf_links_are_normalized_and_checked():
    url = normalize_gguf_url("https://huggingface.co/org/repo/blob/main/model-Q4_K_M.gguf")
    assert url == "https://huggingface.co/org/repo/resolve/main/model-Q4_K_M.gguf"
    assert file_name_for(url) == "model-Q4_K_M.gguf"
    assert file_name_for("https://x.org/a/My%20Model.gguf?download=true") == "My-Model.gguf"
    for bad in ["http://example.com/m.gguf", "file:///etc/passwd", "ftp://x/m.gguf"]:
        with pytest.raises(ValueError):
            normalize_gguf_url(bad)
    with pytest.raises(ValueError):
        file_name_for("https://example.com/model.bin")


def test_download_endpoints_validate(client):
    assert client.post("/api/models/download", json={"url": "http://insecure/m.gguf"}).status_code == 400
    assert client.post("/api/models/ollama/pull", json={"name": "bad name;rm"}).status_code == 400
    assert client.get("/api/models/downloads").json() == {"jobs": []}


# ---- LM Studio ----------------------------------------------------------------------------------


def test_lmstudio_models_are_listed_and_routed(tmp_path, monkeypatch):
    from core import models
    from core.config import Settings
    from core.ollama_client import ChatTurn, OllamaError

    class NoOllama:
        async def list_models(self):
            raise OllamaError("not running")

    chats = []

    async def fake_list(self):
        return ["qwen2.5-7b-instruct", "text-embedding-nomic-embed-text-v1.5"]

    async def fake_chat(self, model, messages, tools, allowed, on_token, options=None):
        chats.append((self.base_url, model, bool(tools)))
        return ChatTurn(content="hi")

    monkeypatch.setattr(models.OpenAICompatibleClient, "list_models", fake_list)
    monkeypatch.setattr(models.OpenAICompatibleClient, "chat", fake_chat)
    router = models.ModelRouter(Settings(base_dir=tmp_path, detect_local_servers=True), NoOllama())

    async def scenario():
        assert await router.list_models() == ["lmstudio:qwen2.5-7b-instruct"]  # Embedding models are left out.
        assert await router.supports_tools("lmstudio:qwen2.5-7b-instruct")
        assert not await router.is_remote("lmstudio:qwen2.5-7b-instruct")
        turn = await router.chat("lmstudio:qwen2.5-7b-instruct", [], [{"type": "function"}], set(), lambda t: None)
        assert turn.content == "hi"

    asyncio.run(scenario())
    assert chats == [("http://127.0.0.1:1234/v1", "qwen2.5-7b-instruct", True)]
    with pytest.raises(ValueError):
        Settings(base_dir=tmp_path, lmstudio_url="http://10.0.0.5:1234/v1")


# ---- opening generated files outside the app -------------------------------------------------------


def test_generated_files_open_outside_the_app(client, base, monkeypatch):
    from core import launcher

    opened = []
    monkeypatch.setattr(launcher, "launch", lambda target: opened.append(("open", target)))
    monkeypatch.setattr(launcher, "reveal", lambda path: opened.append(("reveal", str(path))))
    project = base / "workspace" / "projects" / "demo"
    (project / "dashboards").mkdir()
    (project / "dashboards" / "sales.html").write_text("<h1>Sales</h1>")
    (project / "deck.pptx").write_bytes(b"PK")

    def post(path, **extra):
        return client.post("/api/files/open", json={"session_id": "testsession01", "path": path, **extra})

    # A dashboard opens in the browser, through the script-free viewer.
    assert post("projects/demo/dashboards/sales.html").json()["opened"] == "opened in the browser"
    assert opened[-1][1].startswith("http://127.0.0.1:8000/api/dashboards/view?session_id=testsession01&path=projects")
    # Slides open in their app; a path relative to the active project works too.
    assert post("deck.pptx", base="projects/demo").status_code == 200
    assert opened[-1] == ("open", str((project / "deck.pptx").resolve()))
    assert post("projects/demo/deck.pptx", reveal=True).status_code == 200 and opened[-1][0] == "reveal"
    # Refused: programs and code, missing files, secrets, and anything outside the allowed folders.
    count = len(opened)
    assert post("projects/demo/hello.py").status_code == 415
    assert post("projects/demo/missing.docx").status_code == 404
    assert post("projects/demo/.env").status_code == 403
    assert post(str(base.parent / "outside.txt")).status_code == 403
    assert len(opened) == count


def test_files_window_is_served(client):
    page = client.get("/workspace")
    assert page.status_code == 200 and "files-window.js" in page.text
    assert client.get("/static/js/files-window.js").status_code == 200


def test_files_window_opens_without_popups(client, app, monkeypatch):
    from core import launcher

    launched = []
    monkeypatch.setattr(launcher, "launch", launched.append)
    # Browser version: the system opens it in a new tab, so no pop-up blocker is involved.
    assert client.post("/api/files-window", json={"path": "arch/permission.md"}).json() == {"opened": "browser"}
    assert launched == ["http://127.0.0.1:8000/workspace?open=arch%2Fpermission.md"]
    # Desktop app: a native window, via the hook desktop.py installs.
    opened = []
    app.state.open_files_window = opened.append
    assert client.post("/api/files-window", json={}).json() == {"opened": "window"}
    assert opened == [None] and len(launched) == 1
