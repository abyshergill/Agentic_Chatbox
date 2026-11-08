"""The single gate for every filesystem path the AI or the UI touches.

A path is usable only if, after symlinks are resolved, it sits inside the skill's
allowed paths, those paths sit inside the global roots from permission.md, and it
is neither an application internal nor a secret.
"""

from __future__ import annotations

import fnmatch
import os
import tempfile
from pathlib import Path

from .config import Settings
from .permissions import PermissionManager

SECRET_PATTERNS = (
    ".env", ".env.*", "*.env", ".ssh", ".aws", ".gnupg", ".netrc", ".npmrc", ".pypirc",
    ".git-credentials", ".docker", ".kube",
    "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*", "*.pem", "*.key", "*.p12", "*.pfx", "*.keystore",
    "credentials*", "secrets*", "secret.*",
)
BLOCKED_DIRS = {".git"}  # Writing to .git/hooks would be arbitrary code execution.


class PathDenied(Exception):
    """Raised when a path fails validation. The message is safe to show the model."""


def is_secret_name(name: str) -> bool:
    lowered = name.lower()
    return any(fnmatch.fnmatch(lowered, pattern) for pattern in SECRET_PATTERNS)


class FsGuard:
    def __init__(self, settings: Settings, permissions: PermissionManager, skill_paths: list[str]):
        self.settings = settings
        self.permissions = permissions
        self.skill_paths = skill_paths
        self.global_roots = self._global_roots()
        self.roots = self._effective_roots()

    def _global_roots(self) -> list[Path]:
        app = self.permissions.app("filesystem")
        if app is None:
            return []
        return [self._resolve_config_path(item) for item in app.csv("Roots")]

    def _effective_roots(self) -> list[Path]:
        """Intersects skill paths with global roots, so a skill can never widen access."""
        roots: list[Path] = []
        for item in self.skill_paths:
            wanted = self._resolve_config_path(item)
            for allowed in self.global_roots:
                if wanted.is_relative_to(allowed):
                    candidate = wanted
                elif allowed.is_relative_to(wanted):
                    candidate = allowed
                else:
                    continue
                if candidate not in roots:
                    roots.append(candidate)
        return roots

    def _resolve_config_path(self, value: str) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self.settings.base_dir / path
        return path.resolve()

    def resolve(self, user_path: str | None) -> Path:
        """Returns the real path for a request, or raises PathDenied."""
        path = self._to_path(user_path)
        self._authorize(path)
        return path

    def _to_path(self, user_path: str | None) -> Path:
        if user_path is None or not str(user_path).strip():
            if not self.roots:
                raise PathDenied("this skill has no accessible filesystem roots")
            return self.roots[0]
        text = str(user_path).strip()
        if "\x00" in text:
            raise PathDenied("path contains a null byte")
        if text.startswith("~"):
            raise PathDenied("home-relative paths are not allowed")
        path = Path(text)
        if not path.is_absolute():
            parts = path.parts
            if parts and parts[0] == "workspace":  # Models often include this prefix.
                path = Path(*parts[1:]) if len(parts) > 1 else Path(".")
            path = self.settings.workspace_root / path
        return path.resolve()

    def _authorize(self, path: Path) -> None:
        root = next((r for r in self.roots if path == r or path.is_relative_to(r)), None)
        if root is None:
            raise PathDenied(f"'{self.display(path)}' is outside the paths this skill may access")

        folded = str(path).casefold()
        for protected in self.settings.protected_paths:
            mark = str(protected).casefold()
            if folded == mark or folded.startswith(mark + os.sep):
                raise PathDenied(f"'{self.display(path)}' is an application internal and cannot be accessed")

        # Only look below the root: the folder the app is installed in is not the model's doing.
        for part in path.relative_to(root).parts:
            if part.lower() in BLOCKED_DIRS:
                raise PathDenied(f"'{self.display(path)}' is inside {part}, which file tools cannot access")
            if is_secret_name(part):
                raise PathDenied(f"'{self.display(path)}' looks like a secret and cannot be accessed")

    def allows(self, path: Path) -> bool:
        try:
            self.resolve(str(path))
        except PathDenied:
            return False
        return True

    def display(self, path: Path) -> str:
        """Workspace-relative label, so host layout is not exposed to UI or model."""
        try:
            return path.relative_to(self.settings.workspace_root).as_posix() or "."
        except ValueError:
            return str(path)  # Only reachable for roots the user configured outside workspace/.

    def root_labels(self) -> list[str]:
        return [self.display(root) for root in self.roots]


def atomic_write(path: Path, content: str | bytes) -> None:
    """Writes via a temp file and rename, which replaces a symlink rather than following it."""
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-write-")
    try:
        binary = isinstance(content, bytes)
        with os.fdopen(fd, "wb" if binary else "w", **({} if binary else {"encoding": "utf-8"})) as handle:
            handle.write(content)
        if path.exists():
            os.chmod(temp_name, path.stat().st_mode & 0o777)
        else:
            os.chmod(temp_name, 0o644)
        os.replace(temp_name, path)
    except BaseException:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
        raise


def read_text_limited(path: Path, limit: int) -> tuple[str, bool]:
    """Reads text without following a final symlink. Returns (text, truncated)."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as handle:
        data = handle.read(limit + 1)
    truncated = len(data) > limit
    data = data[:limit]
    if b"\x00" in data:
        raise ValueError("this looks like a binary file")
    return data.decode("utf-8", errors="replace"), truncated
