"""Opens files and pages outside the app: a document in its default app, a dashboard in the default browser, or a
file in Finder / Explorer. Used by the chat's file links and the explorer, in both the desktop app and the browser
version (the server always runs on this computer)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Files that may be handed to their default app. Nothing here runs code when opened.
OPENABLE = {
    ".pptx", ".ppt", ".key", ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".xlsm", ".numbers", ".pages", ".odt", ".ods",
    ".odp", ".rtf", ".csv", ".tsv", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".txt", ".md", ".mp4", ".mov",
    ".webm", ".m4v",
}
# Opened as a page in the browser, through the app's own viewer (which forbids scripts).
BROWSER_PAGES = {".html", ".htm"}


def launch(target: str) -> None:
    """Opens a file or an http(s) URL with the system's default handler."""
    if sys.platform == "darwin":
        subprocess.Popen(["open", target], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif sys.platform.startswith("win"):
        os.startfile(target)  # type: ignore[attr-defined]  # noqa: S606 - callers validate the target
    else:
        subprocess.Popen(["xdg-open", target], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def reveal(path: Path) -> None:
    """Shows a file in Finder / Explorer (or opens its folder on Linux)."""
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)], stdin=subprocess.DEVNULL)
    elif sys.platform.startswith("win"):
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        launch(str(path if path.is_dir() else path.parent))
