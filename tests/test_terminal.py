import asyncio
import sys

import pytest

from core.adapters.terminal import detect_test_command, parse_command
from core.permissions import PermissionManager
from core.process import run_process
from core.sessions import Session
from core.tools import ToolContext, ToolError

darwin_only = pytest.mark.skipif(sys.platform != "darwin", reason="uses sandbox-exec")


@pytest.fixture
def tool_ctx(settings):
    return ToolContext(
        settings=settings, permissions=PermissionManager(settings.arch_dir / "permission.md"),
        skill_paths=["./workspace/projects"], session=Session(id="s" * 12),
    )


@pytest.fixture
def cwd(settings):
    return settings.workspace_root / "projects" / "demo"


def test_allowed_command_parses(tool_ctx, cwd):
    assert parse_command(tool_ctx, "ls -la", cwd) == ["ls", "-la"]


@pytest.mark.parametrize("command", [
    "rm -rf .", "bash -c 'ls'", "sh script.sh", "sudo ls", "curl http://example.com", "/bin/ls", "./evil",
    "git status", "python3 hello.py",  # These must go through #git and #python.
    "ls | grep x", "ls ; rm x", "ls && rm x", "cat hello.py > other", "",
    "find . -exec rm {} ;", "find . -delete",
    "cat ../../../outside.txt", "cat /etc/passwd", "cat ~/.ssh/id_rsa", "grep --file=/etc/passwd x",
    "cat .env", "ls .ssh",
])
def test_rejected_commands(tool_ctx, cwd, command):
    with pytest.raises(ToolError):
        parse_command(tool_ctx, command, cwd)


def test_forbidden_programs_stay_forbidden_even_if_allowlisted(tool_ctx, cwd, base):
    path = base / "arch" / "permission.md"
    path.write_text(path.read_text().replace("Allowed Commands: ls,", "Allowed Commands: bash, ls,"))
    tool_ctx.permissions.reload()
    with pytest.raises(ToolError):
        parse_command(tool_ctx, "bash -c ls", cwd)


def test_symlink_argument_leading_out_is_rejected(tool_ctx, cwd, base):
    (cwd / "sneaky").symlink_to(base.parent / "outside.txt")
    with pytest.raises(ToolError):
        parse_command(tool_ctx, "cat sneaky", cwd)


def test_test_detection(tmp_path):
    assert detect_test_command(tmp_path) is None
    (tmp_path / "package.json").write_text("{}")
    assert detect_test_command(tmp_path)[0] == "npm"


def run(settings, cwd, code, roots=None):
    return asyncio.run(run_process(settings, [sys.executable, "-I", "-c", code], cwd, roots or [cwd], 20))


def test_timeout_kills_the_process(settings, cwd):
    result = asyncio.run(run_process(settings, ["/bin/sleep", "30"], cwd, [cwd], 1))
    assert result.timed_out and not result.ok


def test_environment_is_scrubbed(settings, cwd, monkeypatch):
    monkeypatch.setenv("MY_API_TOKEN", "leak-me")
    result = asyncio.run(run_process(settings, ["/usr/bin/env"], cwd, [cwd], 10))
    assert "leak-me" not in result.stdout


@darwin_only
def test_sandbox_blocks_writes_outside_roots_and_app_config(settings, cwd, base):
    target = base / "arch" / "permission.md"
    before = target.read_text()
    fake_interpreter = asyncio.run(run_process(
        settings, ["/bin/sh", "-c", f"echo hacked >> '{target}'"], cwd, [cwd], 10,
    ))
    assert fake_interpreter.exit_code != 0
    assert target.read_text() == before
    escaped = base.parent / "escaped.txt"
    asyncio.run(run_process(settings, ["/bin/sh", "-c", f"echo x > '{escaped}'"], cwd, [cwd], 10))
    assert not escaped.exists()


@darwin_only
def test_sandbox_allows_writes_inside_roots(settings, cwd):
    result = asyncio.run(run_process(settings, ["/usr/bin/touch", "made.txt"], cwd, [cwd], 10))
    assert result.ok and (cwd / "made.txt").exists()


@darwin_only
def test_sandbox_blocks_secret_files_inside_roots(settings, cwd):
    result = asyncio.run(run_process(settings, ["/bin/cat", ".env"], cwd, [cwd], 10))
    assert result.exit_code != 0 and "TOKEN" not in result.stdout


@darwin_only
def test_sandbox_blocks_network(settings, cwd):
    result = asyncio.run(run_process(settings, ["/usr/bin/nc", "-z", "-w", "2", "127.0.0.1", "8000"], cwd, [cwd], 10))
    assert result.exit_code != 0
