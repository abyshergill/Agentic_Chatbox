import pytest

webview = pytest.importorskip("webview")
import desktop  # noqa: E402


@pytest.fixture
def api(settings, monkeypatch):
    monkeypatch.setattr(desktop, "SETTINGS", settings)
    launched = []
    monkeypatch.setattr(desktop, "_launch", lambda args: launched.append(args))
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda args, **_: launched.append(args))
    return desktop.DesktopApi(), launched


def test_opens_documents_inside_the_workspace(api, settings):
    bridge, launched = api
    deck = settings.workspace_root / "projects" / "demo" / "presentations" / "d.pptx"
    deck.parent.mkdir(parents=True)
    deck.write_bytes(b"PK")
    assert bridge.open_path("projects/demo/presentations/d.pptx") is True
    assert bridge.open_path("workspace/projects/demo/presentations/d.pptx") is True
    assert launched and launched[0][-1] == str(deck.resolve())


def test_refuses_programs_and_paths_outside(api, settings, tmp_path):
    bridge, launched = api
    assert "cannot be opened" in bridge.open_path("projects/demo/hello.py")
    assert "outside" in bridge.open_path("../outside.txt")
    assert "outside" in bridge.reveal_path("projects/../../outside.txt")
    link = settings.workspace_root / "projects" / "demo" / "escape.pdf"
    (tmp_path / "secret.pdf").write_bytes(b"x")
    link.symlink_to(tmp_path / "secret.pdf")
    assert "outside" in bridge.open_path("projects/demo/escape.pdf")
    assert launched == []


def test_reveal_shows_a_workspace_file(api):
    bridge, launched = api
    assert bridge.reveal_path("projects/demo/hello.py") is True and launched
