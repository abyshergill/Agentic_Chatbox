"""#python: runs an existing script from inside the permitted paths."""

from __future__ import annotations

from ..fs_guard import read_text_limited
from ..process import find_executable, run_process
from ..tools import Adapter, Tool, ToolContext, ToolError, ToolResult


def _check(ctx: ToolContext, args: dict) -> None:
    guard = ctx.guard
    path = guard.resolve(args["path"])
    if path.suffix != ".py" or not path.is_file():
        raise ToolError(f"'{guard.display(path)}' is not an existing .py file")


def _preview(ctx: ToolContext, args: dict) -> str:
    guard = ctx.guard
    path = guard.resolve(args["path"])
    text, truncated = read_text_limited(path, 4000)
    suffix = "\n... (script continues)" if truncated else ""
    return f"Run {guard.display(path)} {' '.join(args.get('args', []))}\n\n{text}{suffix}"


async def execute_python(ctx: ToolContext, args: dict) -> ToolResult:
    _check(ctx, args)  # Revalidated after approval, in case the file changed.
    path = ctx.guard.resolve(args["path"])
    app = ctx.permissions.app("python")
    interpreter = find_executable(app.executable or "python3")
    if interpreter is None:
        raise ToolError(f"'{app.executable}' was not found on the safe PATH")
    result = await run_process(
        ctx.settings, [interpreter, str(path), *args.get("args", [])], path.parent, ctx.guard.roots, app.timeout()
    )
    return ToolResult.from_process(result)


adapter = Adapter(
    app_id="python",
    tools=[
        Tool("execute_python", "python", "execute_script", "Run a Python script file that is already in the workspace.",
             {"path": {"type": "string"}, "args": {"type": "array", "items": {"type": "string"}}},
             execute_python, required=["path"], precheck=_check, preview=_preview),
    ],
)
