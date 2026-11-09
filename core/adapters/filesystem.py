"""#filesystem: all file access for the model, always through FsGuard."""

from __future__ import annotations

import difflib
import fnmatch
import os
from pathlib import Path

from ..fs_guard import PathDenied, atomic_write, read_text_limited
from ..tools import Adapter, Tool, ToolContext, ToolError, ToolResult
from ..documents import DocumentError, extract_text
from ..uploads import read_attachment_text

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "target", "dist", "build", ".pytest_cache"}
PATH = {"type": "string", "description": "Path relative to the workspace, e.g. projects/my_app/settings.py"}


def _walk(ctx: ToolContext, start: Path):
    guard = ctx.guard
    for current, dirs, files in os.walk(start, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and guard.allows(Path(current) / d))
        for name in sorted(files):
            path = Path(current) / name
            if guard.allows(path):
                yield path


async def list_directory(ctx: ToolContext, args: dict) -> ToolResult:
    guard = ctx.guard
    path = guard.resolve(args.get("path") or ctx.active_path)
    if not path.is_dir():
        raise ToolError(f"'{guard.display(path)}' is not a directory")
    lines = []
    for child in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if guard.allows(child):
            lines.append(f"{child.name}/" if child.is_dir() else f"{child.name}  ({child.stat().st_size} bytes)")
    return ToolResult(True, f"{guard.display(path)}:\n" + ("\n".join(lines) or "(empty)"))


def _find_attachment(ctx: ToolContext, reference: str):
    """Matches an id or file name. Models often pass the name they were shown."""
    attachments = getattr(ctx.session, "attachments", None) or {}
    if reference in attachments:
        return attachments[reference]
    name = Path(reference).name
    return next((a for a in attachments.values() if a.name == name), None)


async def read_file(ctx: ToolContext, args: dict) -> ToolResult:
    guard = ctx.guard
    try:
        path = guard.resolve(args["path"])
    except PathDenied:
        # The user chose to share attachments, so a mistaken read_file can still find them.
        attachment = _find_attachment(ctx, args["path"])
        if attachment is None:
            raise
        return ToolResult(True, read_attachment_text(attachment, ctx.settings.max_read_bytes))
    if not path.exists():
        attachment = _find_attachment(ctx, args["path"])
        if attachment is not None:
            return ToolResult(True, read_attachment_text(attachment, ctx.settings.max_read_bytes))
    if not path.is_file():
        raise ToolError(f"'{guard.display(path)}' is not a file")
    try:
        # PDF, Word and Excel become text; HTML and code stay as their source so they can be edited.
        text, truncated = extract_text(path, ctx.settings.max_read_bytes, html_as_text=False)
    except (ValueError, DocumentError) as exc:
        raise ToolError(str(exc)) from exc
    start, end = args.get("start_line"), args.get("end_line")
    if start or end:
        lines = text.splitlines()
        text = "\n".join(lines[max((start or 1) - 1, 0): end])
    return ToolResult(True, text + ("\n(file truncated)" if truncated else ""))


def _diff_preview(ctx: ToolContext, args: dict) -> str:
    guard = ctx.guard
    path = guard.resolve(args["path"])
    old = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    label = guard.display(path)
    diff = difflib.unified_diff(
        old.splitlines(), args["content"].splitlines(), f"a/{label}", f"b/{label}", lineterm=""
    )
    return "\n".join(diff) or "(no changes)"


def _must_be_writable(ctx: ToolContext, args: dict) -> None:
    guard = ctx.guard
    path = guard.resolve(args["path"])
    if path.is_dir():
        raise ToolError(f"'{guard.display(path)}' is a directory")


def _must_not_exist(ctx: ToolContext, args: dict) -> None:
    guard = ctx.guard
    path = guard.resolve(args["path"])
    if path.exists():
        raise ToolError(f"'{guard.display(path)}' already exists")


async def _write(ctx: ToolContext, args: dict) -> ToolResult:
    guard = ctx.guard
    path = guard.resolve(args["path"])  # Revalidated after approval, immediately before writing.
    if path.is_dir():
        raise ToolError(f"'{guard.display(path)}' is a directory")
    guard.resolve(str(path.parent))
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, args["content"])
    label = guard.display(path)
    return ToolResult(True, f"Wrote {len(args['content'].encode())} bytes to {label}", [label])


async def write_file(ctx: ToolContext, args: dict) -> ToolResult:
    return await _write(ctx, args)


async def create_file(ctx: ToolContext, args: dict) -> ToolResult:
    _must_not_exist(ctx, args)
    return await _write(ctx, {"path": args["path"], "content": args.get("content", "")})


async def create_directory(ctx: ToolContext, args: dict) -> ToolResult:
    guard = ctx.guard
    path = guard.resolve(args["path"])
    path.mkdir(parents=True, exist_ok=True)
    label = guard.display(path)
    return ToolResult(True, f"Created directory {label}", [label])


async def delete_file(ctx: ToolContext, args: dict) -> ToolResult:
    guard = ctx.guard
    path = guard.resolve(args["path"])
    label = guard.display(path)
    if path in guard.roots:
        raise ToolError("a permitted root cannot be deleted")
    if not path.exists():
        raise ToolError(f"'{label}' does not exist")
    if path.is_dir() and any(path.iterdir()):
        raise ToolError(f"'{label}' is not empty; delete its files first")  # No recursive delete.
    try:
        from send2trash import send2trash

        send2trash(str(path))  # To the system Trash, so it can be restored.
    except ImportError:
        path.rmdir() if path.is_dir() else path.unlink()
        return ToolResult(True, f"Deleted {label}", [label])
    except OSError as exc:
        raise ToolError(f"could not move '{label}' to the Trash: {exc}") from exc
    return ToolResult(True, f"Moved {label} to the Trash (restorable)", [label])


async def rename_file(ctx: ToolContext, args: dict) -> ToolResult:
    guard = ctx.guard
    source, destination = guard.resolve(args["source"]), guard.resolve(args["destination"])
    if not source.exists():
        raise ToolError(f"'{guard.display(source)}' does not exist")
    if destination.exists():
        raise ToolError(f"'{guard.display(destination)}' already exists")
    if source in guard.roots:
        raise ToolError("a permitted root cannot be renamed")
    guard.resolve(str(destination.parent))
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.rename(destination)
    labels = [guard.display(source), guard.display(destination)]
    return ToolResult(True, f"Renamed {labels[0]} to {labels[1]}", labels)


async def search_files(ctx: ToolContext, args: dict) -> ToolResult:
    guard = ctx.guard
    limit = ctx.settings.max_search_results
    pattern = args["pattern"].lower()
    if not any(c in pattern for c in "*?["):
        pattern = f"*{pattern}*"
    matches = []
    for start in _search_starts(ctx, args):
        for path in _walk(ctx, start):
            if fnmatch.fnmatch(path.name.lower(), pattern):
                matches.append(guard.display(path))
                if len(matches) >= limit:
                    break
    return ToolResult(True, "\n".join(matches[:limit]) or "No matching files.")


async def search_file_content(ctx: ToolContext, args: dict) -> ToolResult:
    guard = ctx.guard
    query = args["query"].lower()
    matches: list[str] = []
    limit = ctx.settings.max_search_results
    for start in _search_starts(ctx, args):
        for path in _walk(ctx, start):
            if len(matches) >= limit or path.stat().st_size > ctx.settings.max_read_bytes:
                continue
            try:
                text, _ = read_text_limited(path, ctx.settings.max_read_bytes)
            except (ValueError, OSError):
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if query in line.lower():
                    matches.append(f"{guard.display(path)}:{number}: {line.strip()[:200]}")
                    if len(matches) >= limit:
                        break
    return ToolResult(True, "\n".join(matches) or "No matches.")


def _search_starts(ctx: ToolContext, args: dict) -> list[Path]:
    guard = ctx.guard
    if args.get("path") or ctx.active_path:
        return [guard.resolve(args.get("path") or ctx.active_path)]
    return guard.roots


async def read_attachment(ctx: ToolContext, args: dict) -> ToolResult:
    attachment = _find_attachment(ctx, args["attachment_id"])
    if attachment is None:
        raise ToolError("no attachment with that id in this conversation")
    text = read_attachment_text(attachment, ctx.settings.max_read_bytes)
    return ToolResult(True, text)


def _write_summary(verb: str):
    def preview(ctx: ToolContext, args: dict) -> str:
        guard = ctx.guard
        if "destination" in args:
            return f"{verb} {guard.display(guard.resolve(args['source']))} to {guard.display(guard.resolve(args['destination']))}"
        return f"{verb} {guard.display(guard.resolve(args['path']))}"

    return preview


adapter = Adapter(
    app_id="filesystem",
    tools=[
        Tool("list_directory", "filesystem", "list", "List the files and folders in a directory.",
             {"path": PATH}, list_directory),
        Tool("read_file", "filesystem", "read", "Read a file as text, optionally only a line range. Works for code, text, Markdown, CSV and HTML, and extracts the text of PDF, Word (.docx, .doc), RTF and Excel files.",
             {"path": PATH, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}},
             read_file, required=["path"]),
        Tool("write_file", "filesystem", "write", "Create a file or replace its entire contents.",
             {"path": PATH, "content": {"type": "string", "description": "The complete new file contents"}},
             write_file, required=["path", "content"], precheck=_must_be_writable, preview=_diff_preview),
        Tool("create_file", "filesystem", "create", "Create a new file. Fails if it already exists.",
             {"path": PATH, "content": {"type": "string"}},
             create_file, required=["path"], precheck=_must_not_exist,
             preview=lambda ctx, args: _diff_preview(ctx, {"path": args["path"], "content": args.get("content", "")})),
        Tool("create_directory", "filesystem", "create", "Create a directory, including missing parents.",
             {"path": PATH}, create_directory, required=["path"], preview=_write_summary("Create directory")),
        Tool("delete_file", "filesystem", "delete", "Move a file or an empty directory to the Trash.",
             {"path": PATH}, delete_file, required=["path"], destructive=True,
             precheck=lambda ctx, args: ctx.guard.resolve(args["path"]) and None, preview=_write_summary("Permanently delete")),
        Tool("rename_file", "filesystem", "rename", "Rename or move a file or directory.",
             {"source": PATH, "destination": PATH}, rename_file, required=["source", "destination"],
             precheck=lambda ctx, args: (ctx.guard.resolve(args["source"]), ctx.guard.resolve(args["destination"])) and None,
             preview=_write_summary("Move")),
        Tool("search_files", "filesystem", "search", "Find files by name or glob, e.g. settings.py or *.csv.",
             {"pattern": {"type": "string"}, "path": PATH}, search_files, required=["pattern"]),
        Tool("search_file_content", "filesystem", "search", "Search inside files for text (case-insensitive).",
             {"query": {"type": "string"}, "path": PATH}, search_file_content, required=["query"]),
        Tool("read_attachment", "filesystem", "read", "Read a file the user attached, by its attachment id or file name.",
             {"attachment_id": {"type": "string"}}, read_attachment, required=["attachment_id"]),
    ],
)
