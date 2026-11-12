"""Agentic Chatbox as a desktop application. Run with `python desktop.py`.

The same app as `python main.py`, shown in its own native window (macOS WebKit, Windows Edge WebView2, or GTK/Qt on
Linux) instead of a browser tab. The server still listens only on 127.0.0.1, on a free port picked at launch.

Desktop extras, exposed to the page as `window.pywebview.api`:
- open_path: open a generated file (a .pptx, a PDF, an image...) in its default app, such as Keynote or PowerPoint.
- reveal_path: show a file in Finder / Explorer.
- choose_folder: a native folder picker for opening a folder in the explorer.
open_path and reveal_path accept only paths inside the workspace, a folder the user opened, or arch/, and open_path only document and image types, never programs or scripts.
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

import json
from urllib.parse import urlencode

import httpx
import uvicorn
import webview
from webview.menu import Menu, MenuAction, MenuSeparator

from main import app

TITLE = "Agentic Chatbox"
SETTINGS = app.state.ctx.settings
from core.launcher import OPENABLE  # Files the desktop may hand to their default app.
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
    """Resolves a path from the UI: workspace-relative, or absolute inside a folder the user opened or arch/.

    Refuses anything else, including symlinks that point outside those places.
    """
    root = SETTINGS.workspace_root.resolve()
    text = str(path or "").strip()
    allowed = [root, SETTINGS.arch_dir.resolve(), *(Path(f).resolve() for f in SETTINGS.extra_roots)]
    if text.startswith("/") and any(Path(text).resolve().is_relative_to(a) for a in allowed[1:]):
        target = Path(text).resolve()
    else:
        relative = text.lstrip("/")
        if relative.startswith("workspace/"):
            relative = relative[len("workspace/"):]
        target = (root / relative).resolve()  # Follows symlinks, so a link pointing outside is caught below.
    if not any(target == a or target.is_relative_to(a) for a in allowed):
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


# The chat window's address, and the Files window while it is open.
WINDOWS: dict = {"url": "", "files": None, "main": None}


def _bring_to_front(window) -> None:
    window.restore()
    window.show()
    window.on_top = True  # pywebview has no focus(); toggling "always on top" raises the window.
    window.on_top = False


class DesktopApi:
    """Methods callable from the page. Each returns True, or a message the UI can show."""

    def open_files_window(self, path: str | None = None):
        """Opens (or brings forward) the Files window: explorer and editor, beside the chat."""
        files = WINDOWS["files"]
        if files is not None:
            _bring_to_front(files)
            if path:
                message = json.dumps({"type": "open-file", "path": str(path), "id": f"py-{time.time_ns()}"})
                files.evaluate_js(f"window.__fromApp && window.__fromApp({message})")
            return True
        query = f"?{urlencode({'open': path})}" if path else ""
        files = webview.create_window(
            f"Files · {TITLE}", f"{WINDOWS['url']}workspace{query}", js_api=self, width=1280, height=860,
            min_size=(700, 480), text_select=True, background_color="#faf9f5",
        )
        WINDOWS["files"] = files

        def closed():
            WINDOWS["files"] = None

        files.events.closed += closed
        return True

    def focus_chat(self):
        if WINDOWS["main"] is not None:
            _bring_to_front(WINDOWS["main"])
        return True

    def relay(self, message: str):
        """Passes a message from one window to the others (open this file, files changed, project chosen...)."""
        try:
            data = json.loads(message)
        except (TypeError, json.JSONDecodeError):
            return "invalid message"
        if not isinstance(data, dict) or not isinstance(data.get("type"), str):
            return "invalid message"
        payload = json.dumps(data)  # Re-encoded, so only JSON reaches evaluate_js.

        def deliver():
            for window in list(webview.windows):
                try:
                    window.evaluate_js(f"window.__fromApp && window.__fromApp({payload})")
                except Exception:  # noqa: BLE001 - a window closing mid-delivery is fine.
                    pass

        threading.Thread(target=deliver, daemon=True).start()
        return True

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

    def choose_folder(self):
        """Native folder picker for 'Open Folder…' and 'Where should files be saved?'. Returns the path, or None."""
        window = webview.windows[0] if webview.windows else None
        if window is None:
            return None
        picked = window.create_file_dialog(webview.FileDialog.FOLDER, directory=str(Path.home()))
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
    WINDOWS["url"] = url
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
        TITLE, url, js_api=DesktopApi(), width=1440, height=920, min_size=(900, 600), text_select=True,
        background_color="#faf9f5",
    )
    WINDOWS["main"] = window
    # The page asks the server to open the Files window, so it works even before the JavaScript bridge is ready.
    app.state.open_files_window = lambda path: DesktopApi().open_files_window(path)

    def click(element_id: str):
        window.evaluate_js(f"document.getElementById({element_id!r})?.click()")

    def open_workspace():
        _launch([str(SETTINGS.workspace_root)])

    def open_logs():
        _launch([str(SETTINGS.logs_dir)])

    menu = [
        Menu("Chat", [
            MenuAction("New Chat", lambda: click("new-chat")),
            MenuAction("Files Window", lambda: DesktopApi().open_files_window()),
            MenuAction("Show or Hide Sidebar", lambda: window.evaluate_js(
                "document.getElementById('app').classList.contains('sidebar-collapsed')"
                " ? document.getElementById('show-sidebar').click() : document.getElementById('collapse-sidebar').click()")),
            MenuSeparator(),
            MenuAction("Settings…", lambda: click("open-settings")),
            MenuAction("Models…", lambda: click("nav-models")),
            MenuAction("Connectors…", lambda: click("nav-connectors")),
            MenuAction("Skills…", lambda: click("nav-skills")),
        ]),
        Menu("Workspace", [
            MenuAction("Open Workspace Folder", open_workspace),
            MenuAction("Open Logs Folder", open_logs),
            MenuAction("Open Skills Folder", lambda: _launch([str(SETTINGS.skills_dir)])),
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
