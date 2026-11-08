"""Local AI Workspace as a desktop application. Run with `python desktop.py`.

The same app as `python main.py`, shown in its own native window (macOS WebKit, Windows Edge WebView2, or GTK/Qt on
Linux) instead of a browser tab. The server still listens only on 127.0.0.1, on a free port picked at launch.

Desktop extras, exposed to the page as `window.pywebview.api`:
- open_path: open a generated file (a .pptx, a PDF, an image...) in its default app, such as Keynote or PowerPoint.
- reveal_path: show a file in Finder / Explorer.
Both accept only paths inside the workspace, and open_path only document and image types, never programs or scripts.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn
import webview
from webview.menu import Menu, MenuAction, MenuSeparator

from main import app

TITLE = "Local AI Workspace"
SETTINGS = app.state.ctx.settings
# Files the desktop may hand to their default app. Nothing here can execute code when opened.
OPENABLE = {
    ".pptx", ".ppt", ".key", ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".numbers", ".pages", ".odt", ".ods", ".odp",
    ".csv", ".tsv", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".txt", ".md",
}
OLLAMA_PATHS = ("/opt/homebrew/bin/ollama", "/usr/local/bin/ollama", "/Applications/Ollama.app/Contents/Resources/ollama")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def ollama_running() -> bool:
    try:
        return httpx.get(f"{SETTINGS.ollama_url}/api/tags", timeout=1.5).status_code == 200
    except httpx.HTTPError:
        return False


def start_ollama() -> subprocess.Popen | None:
    """Starts `ollama serve` if Ollama is installed but not running. Returns the process only if we started it."""
    if ollama_running():
        return None
    binary = shutil.which("ollama") or next((p for p in OLLAMA_PATHS if Path(p).exists()), None)
    if not binary:
        return None
    process = subprocess.Popen([binary, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               start_new_session=True)
    for _ in range(40):
        if ollama_running():
            break
        time.sleep(0.25)
    return process


def workspace_file(path: str) -> Path:
    """Resolves a workspace-relative path from the UI, refusing anything outside the workspace."""
    root = SETTINGS.workspace_root.resolve()
    relative = str(path or "").strip().lstrip("/")
    if relative.startswith("workspace/"):
        relative = relative[len("workspace/"):]
    target = (root / relative).resolve()  # Follows symlinks, so a link pointing outside is caught below.
    if target != root and not target.is_relative_to(root):
        raise ValueError("That file is outside the workspace.")
    if not target.exists():
        raise ValueError("That file does not exist.")
    return target


def _launch(args: list[str]) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", *args])
    elif sys.platform.startswith("win"):
        os.startfile(args[-1])  # type: ignore[attr-defined]  # noqa: S606 - type is checked by the caller
    else:
        subprocess.Popen(["xdg-open", args[-1]])


class DesktopApi:
    """Methods callable from the page. Each returns True, or a message the UI can show."""

    def open_path(self, path: str):
        try:
            target = workspace_file(path)
        except ValueError as exc:
            return str(exc)
        if not target.is_file() or target.suffix.lower() not in OPENABLE:
            return f"{target.suffix or 'This file'} files cannot be opened from here."
        _launch([str(target)])
        return True

    def absolute_path(self, path: str):
        """For 'Copy Path'. The browser version only ever sees workspace-relative paths."""
        try:
            return {"path": str(workspace_file(path))}
        except ValueError as exc:
            return str(exc)

    def choose_gguf(self):
        """Native file picker for adding a .gguf model. Returns the chosen path, or None."""
        window = webview.windows[0] if webview.windows else None
        if window is None:
            return None
        picked = window.create_file_dialog(webview.FileDialog.OPEN, directory=str(Path.home()),
                                           file_types=("GGUF models (*.gguf)", "All files (*.*)"))
        return picked[0] if picked else None

    def reveal_path(self, path: str):
        try:
            target = workspace_file(path)
        except ValueError as exc:
            return str(exc)
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(target)])
        elif sys.platform.startswith("win"):
            subprocess.Popen(["explorer", "/select,", str(target)])
        else:
            _launch([str(target if target.is_dir() else target.parent)])
        return True


def main() -> None:
    port = free_port()
    SETTINGS.port = port
    url = f"http://127.0.0.1:{port}/"
    ollama = start_ollama()

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        try:
            if httpx.get(f"{url}api/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)

    print(f"{TITLE}: desktop window on {url}", flush=True)
    window = webview.create_window(
        TITLE, url, js_api=DesktopApi(), width=1440, height=900, min_size=(1000, 640), text_select=True,
        background_color="#14171b",
    )

    def open_workspace():
        _launch([str(SETTINGS.workspace_root)])

    def open_logs():
        _launch([str(SETTINGS.logs_dir)])

    menu = [
        Menu("Workspace", [
            MenuAction("Open Workspace Folder", open_workspace),
            MenuAction("Open Logs Folder", open_logs),
            MenuSeparator(),
            MenuAction("Reload", lambda: window.evaluate_js("location.reload()")),
        ]),
    ]
    webview.settings["ALLOW_DOWNLOADS"] = True
    storage = SETTINGS.data_dir / "desktop"
    storage.mkdir(parents=True, exist_ok=True)
    try:
        # private_mode=False keeps the page's local storage (your session, layout) between launches.
        webview.start(menu=menu, private_mode=False, storage_path=str(storage))
    finally:
        app.state.ctx.llm.shutdown()  # Stops a running llama.cpp server for .gguf models.
        server.should_exit = True
        thread.join(timeout=5)
        if ollama is not None:
            ollama.terminate()


if __name__ == "__main__":
    main()
