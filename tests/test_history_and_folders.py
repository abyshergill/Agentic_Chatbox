"""Chat history in SQLite, folders the user opens, saving attachments to a chosen folder, and editing arch/ in the UI."""

from core.app_context import AppContext
from core.fs_guard import FsGuard, PathDenied
from core.permissions import PermissionManager
from main import create_app
from tests.conftest import FakeOllama

SID = "testsession01"


def test_conversations_are_saved_listed_renamed_and_deleted(client, ctx, run_chat):
    ctx.ollama.script = ["Hello there."]
    run_chat("Summarise my sales data please")
    chats = client.get("/api/chats").json()["chats"]
    assert [c["title"] for c in chats] == ["Summarise my sales data please"] and chats[0]["messages"] == 2

    assert client.post(f"/api/chats/{SID}/rename", json={"title": "  Sales  summary "}).json()["title"] == "Sales summary"
    assert client.get("/api/chats", params={"q": "sales"}).json()["chats"][0]["title"] == "Sales summary"
    assert client.get(f"/api/chats/{SID}").json()["messages"][1]["content"] == "Hello there."

    assert client.delete(f"/api/chats/{SID}").json()["deleted"] is True
    assert client.get("/api/chats").json()["chats"] == []
    assert client.get(f"/api/chats/{SID}").status_code == 404


def test_history_survives_a_restart(settings, run_chat, ctx):
    ctx.ollama.script = ["Remembered."]
    run_chat("first message")
    ctx.db.close()

    restarted = create_app(settings)
    restarted.state.ctx.ollama = FakeOllama()
    session = restarted.state.ctx.sessions.get(SID)
    assert [m["content"] for m in session.messages] == ["first message", "Remembered."]


def test_empty_conversations_are_not_saved(client):
    client.get("/api/session", params={"session_id": SID})
    assert client.get("/api/chats").json()["chats"] == []


def test_open_any_folder_and_use_it(client, settings, tmp_path, ctx):
    outside = tmp_path / "my reports (2024)"
    outside.mkdir()
    (outside / "sales.csv").write_text("a,b\n1,2\n")

    opened = client.post("/api/folders", json={"path": str(outside)}).json()
    assert opened["path"] == outside.resolve().as_posix()
    top = client.get("/api/files/tree", params={"session_id": SID}).json()["entries"]
    mine = next(e for e in top if e["root"] == "folder")
    assert mine["name"] == "my reports (2024)" and top[0] is not None

    listed = client.get("/api/files/tree", params={"session_id": SID, "path": mine["path"]}).json()["entries"]
    assert [e["name"] for e in listed] == ["sales.csv"]
    assert client.post("/api/files/mkdir", json={"session_id": SID, "path": f"{mine['path']}/out"}).status_code == 200

    # Every skill may use it, on top of its own Allowed Paths.
    guard = FsGuard(settings, PermissionManager(settings.arch_dir / "permission.md"), ["./workspace/projects"])
    assert guard.resolve(str(outside / "sales.csv")).name == "sales.csv"

    # Remembered across restarts, and removable without touching the disk.
    assert AppContext.create(settings).settings.extra_roots == [outside.resolve()]
    assert client.post("/api/folders/remove", json={"path": str(outside)}).json()["folders"] == []
    assert outside.is_dir()
    assert ctx.db.folders() == []


def test_open_folder_create_and_refusals(client, settings, tmp_path):
    new = tmp_path / "brand" / "new"
    assert client.post("/api/folders", json={"path": str(new)}).status_code == 404
    assert client.post("/api/folders", json={"path": str(new), "create": True}).status_code == 200 and new.is_dir()
    assert client.post("/api/folders", json={"path": "relative/path"}).status_code == 400
    assert client.post("/api/folders", json={"path": "/"}).status_code == 400
    assert client.post("/api/folders", json={"path": str(settings.base_dir / "core")}).status_code in {400, 404}
    (settings.base_dir / "data").mkdir(exist_ok=True)
    assert client.post("/api/folders", json={"path": str(settings.base_dir / "data")}).status_code == 400
    secrets = tmp_path / ".ssh"
    secrets.mkdir()
    assert client.post("/api/folders", json={"path": str(secrets)}).status_code == 400


def test_attachment_is_copied_to_the_chosen_folder(client, settings, ctx, run_chat):
    target = "projects/demo"
    first = client.post("/api/files/upload", data={"session_id": SID, "target_dir": target},
                        files={"file": ("sales.csv", b"a,b\n1,2\n")}).json()
    assert first["saved_to"] == "projects/demo/sales.csv" and first["id"]
    again = client.post("/api/files/upload", data={"session_id": SID, "target_dir": target},
                        files={"file": ("sales.csv", b"a,b\n3,4\n")}).json()
    assert again["saved_to"] == "projects/demo/sales copy.csv"  # Never overwrites.

    ctx.ollama.script = ["ok"]
    run_chat("/general what is in it", active_path=target)
    prompt = ctx.ollama.calls[0]["messages"][0]["content"]
    assert "a copy is saved at projects/demo/sales.csv" in prompt
    assert "Save every file you generate" in prompt and target in prompt


def test_without_a_folder_the_assistant_asks_where_to_save(ctx, run_chat):
    ctx.ollama.script = ["ok"]
    run_chat("/general make a report")
    assert "ask the user where to save" in ctx.ollama.calls[0]["messages"][0]["content"]


def test_user_edits_skills_in_the_explorer_but_the_assistant_cannot(client, settings):
    top = client.get("/api/files/tree", params={"session_id": SID}).json()["entries"]
    arch = next(e for e in top if e["root"] == "config")
    path = f"{arch['path']}/agents/general/skills/general_assistant.md"
    original = client.get("/api/files/read", params={"session_id": SID, "path": path}).json()["content"]
    assert client.post("/api/files/write", json={"session_id": SID, "path": path, "content": original + "\nExtra.\n"}).status_code == 200
    assert (settings.arch_dir / "agents/general/skills/general_assistant.md").read_text().endswith("Extra.\n")

    guard = FsGuard(settings, PermissionManager(settings.arch_dir / "permission.md"), ["./arch", "./workspace"])
    try:
        guard.resolve(path)
    except PathDenied:
        pass
    else:
        raise AssertionError("the assistant's guard must not reach arch/")
