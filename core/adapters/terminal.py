"""#terminal: a controlled command runner, deliberately not a shell.

Commands are split into argv and executed directly, so pipes, redirects, and
substitutions have no effect. Only programs in `Allowed Commands` can run.
"""

from __future__ import annotations

import shlex
from pathlib import Path

from ..fs_guard import PathDenied, is_secret_name
from ..process import find_executable, run_process
from ..tools import Adapter, Tool, ToolContext, ToolError, ToolResult

SHELL_OPERATORS = {"|", "||", "&&", "&", ";", ">", ">>", "<", "<<", "2>", "2>&1"}
# Never runnable, even if someone adds them to Allowed Commands.
FORBIDDEN = {"sh", "bash", "zsh", "fish", "dash", "ksh", "csh", "tcsh", "sudo", "su", "doas", "env", "xargs",
             "nohup", "eval", "exec", "ssh", "scp", "sftp", "curl", "wget", "nc", "dd", "osascript", "open", "launchctl"}
FORBIDDEN_FIND_ARGS = {"-exec", "-execdir", "-ok", "-okdir", "-delete", "-fprint", "-fprintf", "-fls"}


def extra_tool_dirs(ctx: ToolContext) -> list[Path]:
    app = ctx.permissions.app("terminal")
    if app is None:
        return []
    return [Path(item).expanduser().resolve() for item in app.csv("Extra Paths")]


def parse_command(ctx: ToolContext, command: str, cwd: Path) -> list[str]:
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise ToolError(f"could not parse command: {exc}") from exc
    if not argv:
        raise ToolError("empty command")

    program = argv[0]
    if "/" in program:
        raise ToolError("use a bare program name from the allowlist, not a path")
    if program in FORBIDDEN:
        raise ToolError(f"'{program}' is never allowed")

    # These would bypass the #python and #git operation limits, so they get pointed at the right tool.
    if program in {"python", "python3"}:
        raise ToolError("use run_project_tests to run tests, or execute_python to run a script")
    for other in ctx.permissions.apps.values():
        if other.id != "terminal" and other.executable and Path(other.executable).name == program:
            raise ToolError(f"use the #{other.id} tools for '{program}'")

    app = ctx.permissions.app("terminal")
    allowed = app.csv("Allowed Commands") if app else []
    if program not in allowed:
        raise ToolError(f"'{program}' is not in Allowed Commands. Allowed: {', '.join(allowed)}")

    operators = SHELL_OPERATORS.intersection(argv[1:])
    if operators:
        raise ToolError(f"shell operators are not supported ({', '.join(sorted(operators))}); run one program at a time")
    if program == "find" and FORBIDDEN_FIND_ARGS.intersection(argv):
        raise ToolError("find actions that execute or delete are not allowed")

    guard = ctx.guard
    for token in argv[1:]:
        value = token.split("=", 1)[1] if token.startswith("-") and "=" in token else token
        if value.startswith("-") or not value:
            continue
        if is_secret_name(Path(value).name):
            raise ToolError(f"'{value}' looks like a secret and cannot be accessed")
        candidate = Path(value)
        escapes = value.startswith("~") or candidate.is_absolute() or ".." in candidate.parts
        if escapes or (cwd / candidate).exists():  # exists() catches symlinks leading out.
            if value.startswith("~"):
                raise ToolError("home-relative paths are not allowed")
            try:
                guard.resolve(str((cwd / candidate).resolve()))
            except PathDenied as exc:
                raise ToolError(str(exc)) from exc
    return argv


def _check_command(ctx: ToolContext, args: dict) -> None:
    parse_command(ctx, args["command"], ctx.resolve_workdir(args.get("cwd")))


async def execute_command(ctx: ToolContext, args: dict) -> ToolResult:
    cwd = ctx.resolve_workdir(args.get("cwd"))
    argv = parse_command(ctx, args["command"], cwd)  # Revalidated after approval.
    extra = extra_tool_dirs(ctx)
    resolved = find_executable(argv[0], extra)
    if resolved is None:
        raise ToolError(f"'{argv[0]}' is not installed or is not on the safe PATH")
    app = ctx.permissions.app("terminal")
    result = await run_process(
        ctx.settings, [resolved, *argv[1:]], cwd, ctx.guard.roots, app.timeout(), extra_paths=extra
    )
    return ToolResult.from_process(result)


def detect_test_command(project: Path) -> list[str] | None:
    if (project / "package.json").exists():
        return ["npm", "test", "--silent"]
    if (project / "Cargo.toml").exists():
        return ["cargo", "test"]
    if (project / "go.mod").exists():
        return ["go", "test", "./..."]
    if (project / "Makefile").exists():
        return ["make", "test"]
    python_project = (project / "tests").is_dir() or any(project.glob("test_*.py")) or (project / "pyproject.toml").exists()
    if python_project:
        if find_executable("pytest"):
            return ["pytest", "-q"]
        return ["python3", "-m", "unittest", "discover", "-s", "tests" if (project / "tests").is_dir() else ".", "-v"]
    return None


async def run_project_tests(ctx: ToolContext, args: dict) -> ToolResult:
    project = ctx.resolve_workdir(args.get("path"))
    argv = detect_test_command(project)
    if argv is None:
        raise ToolError(f"no recognized test setup in '{ctx.guard.display(project)}'")
    extra = extra_tool_dirs(ctx)
    resolved = find_executable(argv[0], extra)
    if resolved is None:
        raise ToolError(f"'{argv[0]}' is not installed or is not on the safe PATH")
    app = ctx.permissions.app("terminal")
    result = await run_process(
        ctx.settings, [resolved, *argv[1:]], project, ctx.guard.roots, app.timeout(), extra_paths=extra
    )
    outcome = ToolResult.from_process(result)
    outcome.output = f"ran: {' '.join(argv)}\n{outcome.output}"
    return outcome


def _tests_preview(ctx: ToolContext, args: dict) -> str:
    project = ctx.resolve_workdir(args.get("path"))
    argv = detect_test_command(project)
    command = " ".join(argv) if argv else "(no test setup found)"
    return f"Run in {ctx.guard.display(project)}: {command}\nTest suites run project code."


adapter = Adapter(
    app_id="terminal",
    tools=[
        Tool("execute_command", "terminal", "execute_command",
             "Run one allowlisted program with arguments, without a shell. Pipes and redirects do not work. "
             "To run tests, use run_project_tests instead.",
             {"command": {"type": "string", "description": "e.g. ls -la"},
              "cwd": {"type": "string", "description": "Working directory relative to the workspace"}},
             execute_command, required=["command"], precheck=_check_command),
        Tool("run_project_tests", "terminal", "run_project_tests",
             "Run a project's test suite. Detects the right test command automatically, so use this for any request to run tests.",
             {"path": {"type": "string", "description": "Project directory relative to the workspace"}},
             run_project_tests, preview=_tests_preview),
    ],
)
