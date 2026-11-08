"""The explorer's file operations: create, rename/move, copy, delete. All stay inside the workspace."""

import pytest

SID = "testsession01"


@pytest.fixture
def ws(client, settings):
    root = settings.workspace_root / "projects" / "demo"
    post = lambda url, **body: client.post(url, json={"session_id": SID, **body})
    return root, post


def test_create_file_with_missing_parents(ws):
    root, post = ws
    made = post("/api/files/create", path="projects/demo/src/utils/a.py")
    assert made.status_code == 200 and made.json()["path"] == "projects/demo/src/utils/a.py"
    assert (root / "src/utils/a.py").read_text() == ""
    assert post("/api/files/create", path="projects/demo/src/utils/a.py").status_code == 409
    for bad in ["projects/demo/a?.py", "projects/demo/ lead.py", "projects/demo/../../x.py", "../outside.txt"]:
        assert post("/api/files/create", path=bad).status_code in (400, 403), bad


def test_rename_and_move(ws):
    root, post = ws
    (root / "data").mkdir()
    assert post("/api/files/rename", path="projects/demo/hello.py", new_path="projects/demo/main.py").json()["path"] == "projects/demo/main.py"
    assert (root / "main.py").exists() and not (root / "hello.py").exists()
    # Case-only rename is allowed on case-insensitive disks.
    assert post("/api/files/rename", path="projects/demo/main.py", new_path="projects/demo/Main.py").status_code == 200
    moved = post("/api/files/rename", path="projects/demo/Main.py", new_path="projects/demo/data/Main.py")
    assert moved.status_code == 200 and (root / "data/Main.py").exists()


def test_rename_refuses_overwrite_self_nesting_roots_and_escape(ws):
    root, post = ws
    (root / "a.txt").write_text("a")
    (root / "b.txt").write_text("b")
    (root / "dir/sub").mkdir(parents=True)
    assert post("/api/files/rename", path="projects/demo/a.txt", new_path="projects/demo/b.txt").status_code == 409
    assert (root / "b.txt").read_text() == "b"
    assert post("/api/files/rename", path="projects/demo/dir", new_path="projects/demo/dir/sub/dir").status_code == 400
    assert post("/api/files/rename", path="projects", new_path="renamed").status_code in (400, 403)
    assert post("/api/files/rename", path="projects/demo/a.txt", new_path="../a.txt").status_code == 403
    assert post("/api/files/rename", path="projects/demo/a.txt", new_path="projects/demo/a:b.txt").status_code == 400


def test_copy_paste_and_duplicate_names(ws):
    root, post = ws
    (root / "report.csv").write_text("x")
    (root / "folder/inner").mkdir(parents=True)
    (root / "folder/inner/f.txt").write_text("f")
    first = post("/api/files/copy", path="projects/demo/report.csv", dest_dir="projects/demo").json()["path"]
    second = post("/api/files/copy", path="projects/demo/report.csv", dest_dir="projects/demo").json()["path"]
    assert (first, second) == ("projects/demo/report copy.csv", "projects/demo/report copy 2.csv")
    tree = post("/api/files/copy", path="projects/demo/folder", dest_dir="projects/demo").json()["path"]
    assert tree == "projects/demo/folder copy" and (root / "folder copy/inner/f.txt").read_text() == "f"
    assert post("/api/files/copy", path="projects/demo/folder", dest_dir="projects/demo/folder/inner").status_code == 400


def test_copy_keeps_links_as_links(ws, tmp_path):
    root, post = ws
    (root / "linked").mkdir()
    (tmp_path / "secret.txt").write_text("host secret")
    (root / "linked/escape.txt").symlink_to(tmp_path / "secret.txt")
    post("/api/files/copy", path="projects/demo/linked", dest_dir="projects/demo").json()["path"]
    assert (root / "linked copy/escape.txt").is_symlink()  # Not a copy of the outside file's contents.


def test_delete_goes_to_trash(ws, monkeypatch):
    root, post = ws
    trashed = []
    import send2trash
    monkeypatch.setattr(send2trash, "send2trash", lambda path: trashed.append(path))
    (root / "old.txt").write_text("x")
    assert post("/api/files/delete", path="projects/demo/old.txt").json()["trashed"] is True
    assert trashed == [str((root / "old.txt").resolve())]
    assert post("/api/files/delete", path="projects").status_code == 400
    assert post("/api/files/delete", path="projects/demo/missing.txt").status_code == 404
    assert post("/api/files/delete", path="../outside.txt").status_code == 403


def test_disabled_rename_and_delete_are_refused(ws, base):
    from tests.conftest import set_permission

    root, post = ws
    set_permission(base, "filesystem", "Operations", "list, read, search, write, create")
    assert post("/api/files/rename", path="projects/demo/hello.py", new_path="projects/demo/x.py").status_code == 403
    assert post("/api/files/delete", path="projects/demo/hello.py").status_code == 403
    assert (root / "hello.py").exists()


def test_file_operations_are_audited(ws, ctx):
    root, post = ws
    post("/api/files/create", path="projects/demo/n.txt")
    post("/api/files/rename", path="projects/demo/n.txt", new_path="projects/demo/m.txt")
    operations = [entry["operation"] for entry in ctx.audit.history(SID)]
    assert "ui_create_file" in operations and "ui_rename" in operations
