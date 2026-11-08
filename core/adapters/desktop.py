"""#vscode and #browser: launch desktop apps with fixed arguments and validated targets."""

from __future__ import annotations

import sys
from urllib.parse import urlparse

from ..process import find_executable, run_process
from ..tools import Adapter, Tool, ToolContext, ToolError, ToolResult


async def _launch(ctx: ToolContext, argv: list[str], description: str) -> ToolResult:
    # GUI apps cannot start inside the sandbox. Their arguments are fixed and validated instead.
    cwd = ctx.resolve_workdir(None)
    result = await run_process(ctx.settings, argv, cwd, ctx.guard.roots, 30, sandboxed=False)
    if not result.ok:
        raise ToolError(f"could not {description}: {result.stderr.strip() or result.exit_code}")
    return ToolResult(True, f"Asked the application to {description}.")


def _code_binary(ctx: ToolContext) -> str:
    app = ctx.permissions.app("vscode")
    binary = find_executable(app.executable or "code")
    if binary is None:
        raise ToolError("the VS Code 'code' command is not on the safe PATH")
    return binary


async def open_project(ctx: ToolContext, args: dict) -> ToolResult:
    guard = ctx.guard
    path = guard.resolve(args.get("path") or ctx.active_path)
    if not path.is_dir():
        raise ToolError(f"'{guard.display(path)}' is not a directory")
    return await _launch(ctx, [_code_binary(ctx), str(path)], f"open {guard.display(path)} in VS Code")


async def open_file(ctx: ToolContext, args: dict) -> ToolResult:
    guard = ctx.guard
    path = guard.resolve(args["path"])
    if not path.is_file():
        raise ToolError(f"'{guard.display(path)}' is not a file")
    location = f"{path}:{args['line']}" if args.get("line") else str(path)
    return await _launch(ctx, [_code_binary(ctx), "--goto", location], f"open {guard.display(path)} in VS Code")


def _check_url(ctx: ToolContext, args: dict) -> None:
    parsed = urlparse(args["url"])
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ToolError("only http and https URLs can be opened")


async def open_url(ctx: ToolContext, args: dict) -> ToolResult:
    _check_url(ctx, args)
    app = ctx.permissions.app("browser")
    binary = find_executable(app.executable or "firefox")
    if binary:
        argv = [binary, args["url"]]
    elif sys.platform == "darwin":
        argv = ["/usr/bin/open", "-a", app.executable or "Safari", args["url"]]
    else:
        raise ToolError(f"browser '{app.executable}' was not found")
    return await _launch(ctx, argv, "open the URL in the browser")


VSCODE_WORDS = ("vs code", "vscode", "visual studio")
BROWSER_WORDS = ("browser", "firefox", "safari", "chrome", "website", "web page", "webpage")

vscode_tools = [
    Tool("open_project", "vscode", "open_project", "Show a project folder to the user in the VS Code window. Only when the user asks for VS Code.",
         {"path": {"type": "string"}}, open_project,
         precheck=lambda ctx, args: ctx.guard.resolve(args.get("path") or ctx.active_path) and None,
         only_if_mentioned=VSCODE_WORDS),
    Tool("open_file", "vscode", "open_file", "Show a file to the user in the VS Code window. Returns no contents: use read_file to see what a file says.",
         {"path": {"type": "string"}, "line": {"type": "integer"}}, open_file, required=["path"],
         precheck=lambda ctx, args: ctx.guard.resolve(args["path"]) and None,
         only_if_mentioned=VSCODE_WORDS),
]
browser_tools = [
    Tool("open_url", "browser", "open_url", "Open a web address in the browser.",
         {"url": {"type": "string"}}, open_url, required=["url"], precheck=_check_url,
         only_if_mentioned=BROWSER_WORDS),
]
adapter = Adapter(app_id="desktop", tools=[*vscode_tools, *browser_tools])
