"""Runs external programs: no shell, scrubbed environment, timeout, OS sandbox.

On macOS each process runs under sandbox-exec. Writes are limited to the skill's
roots, the network is denied, and home-directory contents and secret files are
unreadable. Linux has no such sandbox yet; health reports isolation as "none" and
the UI warns about it.
"""

from __future__ import annotations

import asyncio
import os
import resource
import shutil
import signal
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import Settings

SAFE_PATH = "/opt/homebrew/bin:/usr/local/bin:/Library/Frameworks/Python.framework/Versions/Current/bin:/usr/bin:/bin"
MAX_CAPTURE_BYTES = 2_000_000
SANDBOX_EXEC = "/usr/bin/sandbox-exec"


@dataclass
class ProcessResult:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    truncated: bool
    isolation: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


def isolation_mode() -> str:
    if sys.platform == "darwin" and os.path.exists(SANDBOX_EXEC):
        return "sandbox-exec"
    return "none"


def search_path(extra: list[Path] | None = None) -> str:
    return ":".join([*(str(p) for p in extra or []), SAFE_PATH])


def find_executable(name: str, extra: list[Path] | None = None) -> str | None:
    return shutil.which(name, path=search_path(extra))


def _quote(path: Path | str) -> str:
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


def sandbox_profile(settings: Settings, roots: list[Path], tmp: Path, extra: list[Path] | None = None) -> str:
    home = Path.home().resolve()
    writable = " ".join(f"(subpath {_quote(p)})" for p in roots)
    # Extra tool directories (nvm, cargo) are readable so their programs can start.
    # A bin folder usually links into its parent (npm into ../lib), so that must be readable too.
    tool_dirs = [p.parent if p.name == "bin" else p for p in extra or []]
    readable = " ".join(f"(subpath {_quote(p)})" for p in [*roots, *tool_dirs])
    protected = " ".join(f"(subpath {_quote(p)})" for p in settings.protected_paths)
    return f"""(version 1)
(allow default)
(deny network*)
(deny file-write*)
(allow file-write* {writable} (regex #"^/dev/(null|zero|tty|dtracehelper|fd/.*)$"))
(deny file-read-data (subpath {_quote(home)}))
(allow file-read-data {readable} (literal {_quote(home / ".gitconfig")}))
(deny file-read* file-write* {protected})
(deny file-read-data (regex #"/\\.(env|ssh|aws|gnupg|netrc|npmrc|pypirc|git-credentials)(/|\\.|$)"))
(deny file-read-data (regex #"/(id_rsa|id_dsa|id_ecdsa|id_ed25519)[^/]*$"))
(deny file-read-data (regex #"\\.(pem|key|p12|pfx|keystore)$"))
(allow file-read* file-write* (subpath {_quote(tmp)}))
"""


def _scrubbed_env(tmp: Path, extra: dict[str, str] | None, tool_dirs: list[Path] | None) -> dict[str, str]:
    env = {
        "PATH": search_path(tool_dirs),
        "HOME": str(Path.home()),
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
        "TMPDIR": str(tmp),
        "PYTHONDONTWRITEBYTECODE": "1",
        "NO_COLOR": "1",
        "TERM": "dumb",
    }
    env.update(extra or {})
    return env


def _limit_resources() -> None:  # Runs in the child just before exec.
    os.umask(0o022)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    limit = 1024 * 1024 * 1024
    if hard == resource.RLIM_INFINITY or hard >= limit:
        resource.setrlimit(resource.RLIMIT_FSIZE, (limit, hard))


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


async def _read_capped(stream: asyncio.StreamReader, limit: int) -> tuple[bytes, bool]:
    buffer = bytearray()
    truncated = False
    while chunk := await stream.read(65536):
        room = limit - len(buffer)
        if room > 0:
            buffer.extend(chunk[:room])
        if len(chunk) > room:
            truncated = True  # Keep draining so the child does not block on a full pipe.
    return bytes(buffer), truncated


async def run_process(
    settings: Settings,
    argv: list[str],
    cwd: Path,
    roots: list[Path],
    timeout: int,
    env: dict[str, str] | None = None,
    extra_paths: list[Path] | None = None,
    sandboxed: bool = True,
) -> ProcessResult:
    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    mode = isolation_mode() if sandboxed else "none"
    with tempfile.TemporaryDirectory(dir=settings.tmp_dir) as tmp_name:
        tmp = Path(tmp_name).resolve()
        command = list(argv)
        if mode == "sandbox-exec":
            command = [SANDBOX_EXEC, "-p", sandbox_profile(settings, roots, tmp, extra_paths), *argv]

        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd),
            env=_scrubbed_env(tmp, env, extra_paths),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            preexec_fn=_limit_resources,
        )
        reader = asyncio.gather(
            _read_capped(proc.stdout, MAX_CAPTURE_BYTES),
            _read_capped(proc.stderr, MAX_CAPTURE_BYTES),
        )
        timed_out = False
        try:
            (out, out_cut), (err, err_cut) = await asyncio.wait_for(reader, timeout)
            await proc.wait()
        except TimeoutError:
            timed_out = True
            _kill_group(proc)
            await proc.wait()
            out, err, out_cut, err_cut = b"", b"", False, False
        except asyncio.CancelledError:
            _kill_group(proc)
            await proc.wait()
            raise
        else:
            _kill_group(proc)  # Clean up any background children left behind.

    return ProcessResult(
        exit_code=proc.returncode,
        stdout=out.decode("utf-8", errors="replace"),
        stderr=err.decode("utf-8", errors="replace"),
        timed_out=timed_out,
        truncated=out_cut or err_cut,
        isolation=mode,
    )
