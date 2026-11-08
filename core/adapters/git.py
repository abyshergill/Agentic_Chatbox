"""#git: fixed git invocations. Repository hooks and external helpers are disabled."""

from __future__ import annotations

from ..process import find_executable, run_process
from ..tools import Adapter, Tool, ToolContext, ToolError, ToolResult

SAFE_FLAGS = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "core.pager=cat",
              "-c", "diff.external=", "-c", "core.sshCommand=false"]


async def _git(ctx: ToolContext, args: dict, *command: str) -> ToolResult:
    app = ctx.permissions.app("git")
    binary = find_executable(app.executable or "git")
    if binary is None:
        raise ToolError("git was not found on the safe PATH")
    guard = ctx.guard
    cwd = ctx.resolve_workdir(args.get("path"))
    # Stops discovery from climbing out of the permitted roots into a parent repository.
    root = next((r for r in guard.roots if cwd == r or cwd.is_relative_to(r)), cwd)
    env = {"GIT_CEILING_DIRECTORIES": str(root.parent), "GIT_TERMINAL_PROMPT": "0"}
    result = await run_process(ctx.settings, [binary, *SAFE_FLAGS, *command], cwd, guard.roots, app.timeout(60), env=env)
    return ToolResult.from_process(result)


async def git_status(ctx: ToolContext, args: dict) -> ToolResult:
    return await _git(ctx, args, "status", "--short", "--branch")


async def git_diff(ctx: ToolContext, args: dict) -> ToolResult:
    command = ["diff", "--no-ext-diff"]
    if args.get("staged"):
        command.append("--staged")
    return await _git(ctx, args, *command)


def _check_paths(ctx: ToolContext, args: dict) -> None:
    cwd = ctx.resolve_workdir(args.get("path"))
    guard = ctx.guard
    for item in args["paths"]:
        guard.resolve(str((cwd / item).resolve()))


async def git_add(ctx: ToolContext, args: dict) -> ToolResult:
    _check_paths(ctx, args)
    return await _git(ctx, args, "add", "--", *args["paths"])


async def git_commit(ctx: ToolContext, args: dict) -> ToolResult:
    return await _git(ctx, args, "commit", "-m", args["message"])


REPO = {"type": "string", "description": "Repository directory relative to the workspace"}

adapter = Adapter(
    app_id="git",
    tools=[
        Tool("git_status", "git", "status", "Show the working tree status of a repository.", {"path": REPO}, git_status),
        Tool("git_diff", "git", "diff", "Show unstaged changes, or staged changes with staged=true.",
             {"path": REPO, "staged": {"type": "boolean"}}, git_diff),
        Tool("git_add", "git", "add", "Stage files for commit.",
             {"paths": {"type": "array", "items": {"type": "string"}, "description": "Files relative to the repository"},
              "path": REPO},
             git_add, required=["paths"], precheck=_check_paths),
        Tool("git_commit", "git", "commit", "Commit the staged changes.",
             {"message": {"type": "string"}, "path": REPO}, git_commit, required=["message"]),
    ],
)
