"""'Allow all actions in this project': no prompts inside the project, everything else unchanged."""

from tests.conftest import of_type, set_permission


def fail_if_asked(event):
    raise AssertionError(f"should not ask: {event['tool']}")


def statuses(events):
    return [e["status"] for e in of_type(events, "tool_result")]


def test_changes_inside_the_project_run_without_asking(ctx, run_chat, settings):
    demo = settings.workspace_root / "projects" / "demo"
    (demo / "old.txt").write_text("x")
    ctx.ollama.script = [[("write_file", {"path": "projects/demo/new.txt", "content": "hi"})],
                         [("rename_file", {"source": "projects/demo/new.txt", "destination": "projects/demo/renamed.txt"})],
                         [("delete_file", {"path": "projects/demo/old.txt"})], "done"]
    events = run_chat("/general do it", decide=fail_if_asked, auto_approve="projects/demo")
    assert statuses(events) == ["success", "success", "success"]
    assert (demo / "renamed.txt").read_text() == "hi" and not (demo / "old.txt").exists()
    assert len(of_type(events, "auto_approved")) == 3
    assert {e["approval"] for e in ctx.audit.history("testsession01")[-3:]} == {"auto_project"}


def test_paths_outside_the_project_still_ask(ctx, run_chat, settings):
    (settings.workspace_root / "projects" / "other").mkdir()
    asked = []
    ctx.ollama.script = [[("write_file", {"path": "projects/other/x.txt", "content": "x"})],
                         [("rename_file", {"source": "projects/demo/hello.py", "destination": "projects/other/hello.py"})], "ok"]
    events = run_chat("/general do it", decide=lambda e: asked.append(e["tool"]) or False, auto_approve="projects/demo")
    assert asked == ["write_file", "rename_file"] and statuses(events) == ["rejected", "rejected"]


def test_browser_and_delegation_always_ask(ctx, run_chat, base):
    set_permission(base, "browser", "Access", "enabled")
    asked = []
    ctx.ollama.script = [[("open_url", {"url": "https://example.com"})], "ok"]
    run_chat("#browser open https://example.com", decide=lambda e: asked.append(e["tool"]) or False, auto_approve="projects/demo")
    assert asked == ["open_url"]


def test_disabled_operations_stay_disabled(ctx, run_chat, base, settings):
    set_permission(base, "filesystem", "Operations", "list, read, search, write, create, rename")
    ctx.ollama.script = [[("delete_file", {"path": "projects/demo/hello.py"})], "ok"]
    events = run_chat("/general delete", decide=fail_if_asked, auto_approve="projects/demo")
    assert statuses(events) == ["denied"] and (settings.workspace_root / "projects/demo/hello.py").exists()


def test_secrets_stay_blocked(ctx, run_chat):
    ctx.ollama.script = [[("write_file", {"path": "projects/demo/.env", "content": "x"})], "ok"]
    events = run_chat("/general write the env file", decide=fail_if_asked, auto_approve="projects/demo")
    assert statuses(events) == ["denied"]


def test_commands_are_confined_to_the_project(ctx, run_chat, settings, monkeypatch):
    from core.adapters import terminal
    from core.process import ProcessResult

    seen = {}

    async def fake_run(settings_, argv, cwd, roots, timeout, **kwargs):
        seen.update(cwd=cwd, roots=roots)
        return ProcessResult(0, "ok", "", False, False, "sandbox")

    monkeypatch.setattr(terminal, "run_process", fake_run)
    ctx.ollama.script = [[("execute_command", {"command": "ls"})], "ok"]
    events = run_chat("/general list files with ls", decide=fail_if_asked, auto_approve="projects/demo")
    demo = (settings.workspace_root / "projects" / "demo").resolve()
    assert statuses(events) == ["success"] and seen["cwd"] == demo and seen["roots"] == [demo]


def test_skill_folders_are_never_widened(ctx, run_chat, base, settings):
    path = base / "arch/agents/embedded_dev/skills/rust_firmware.md"
    path.write_text(path.read_text().replace("./workspace/projects", "./workspace/projects/demo/fw"))
    (settings.workspace_root / "projects/demo/fw").mkdir()
    asked = []
    ctx.ollama.script = [[("write_file", {"path": "projects/demo/fw/main.rs", "content": "x"})],
                         [("write_file", {"path": "projects/demo/top.txt", "content": "x"})], "ok"]
    events = run_chat("/rust-firmware go", decide=lambda e: asked.append(e["target"]) or True, auto_approve="projects/demo")
    assert asked == [] and statuses(events) == ["success", "denied"]  # Inside fw: auto. Outside the skill: denied.


def test_endpoint_validates_the_folder(client):
    body = {"session_id": "testsession01", "message": "hi"}
    assert client.post("/api/chat", json={**body, "auto_approve": "../"}).status_code in (400, 403)
    assert client.post("/api/chat", json={**body, "auto_approve": "projects/demo/hello.py"}).status_code == 400
    assert client.post("/api/chat", json={**body, "auto_approve": "projects/nope"}).status_code == 400
