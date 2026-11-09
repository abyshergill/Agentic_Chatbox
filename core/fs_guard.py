"""The single gate for every filesystem path the AI or the UI touches.

A path is usable only if, after symlinks are resolved, it sits inside the skill's
allowed paths (intersected with the global roots from permission.md) or inside a
folder the user opened in the explorer, and it is neither an application internal
nor a secret.

The user's own explorer additionally sees arch/ (include_config=True), so agents and
skills can be edited in the app. The assistant's tools never get that flag.
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
    def __init__(self, settings: Settings, permissions: PermissionManager, skill_paths: list[str],
                 include_config: bool = False, include_user_folders: bool = True, base: str | None = None):
        self.settings = settings
        self.base = base  # The selected project. Relative paths the workspace does not have are tried there.
        self.permissions = permissions
        self.skill_paths = skill_paths
        self.include_config = include_config
        self.config_root = settings.arch_dir.resolve()
        self.global_roots = self._global_roots()
        self.roots = self._effective_roots()
        if include_user_folders and self.global_roots:  # Filesystem access disabled means no folders at all.
            for folder in settings.extra_roots:
                folder = Path(folder).resolve()
                if folder not in self.roots and not any(folder.is_relative_to(r) for r in self.roots):
                    self.roots.append(folder)
        if include_config and self.config_root not in self.roots:
            self.roots.append(self.config_root)

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
            in_workspace = (self.settings.workspace_root / path).resolve()
            project = self._base_dir()
            if project is None or in_workspace.exists() or project == in_workspace:
                return in_workspace
            # "orders.csv" or "data/orders.csv" usually means inside the selected project, as in any editor.
            in_project = (project / path).resolve()
            first = path.parts[0] if path.parts else ""
            if in_project.exists() or not (self.settings.workspace_root / first).exists():
                return in_project
            return in_workspace
        return path.resolve()

    def _base_dir(self) -> Path | None:
        if not self.base or not str(self.base).strip():
            return None
        base = Path(str(self.base).strip())
        if not base.is_absolute():
            parts = base.parts
            if parts and parts[0] == "workspace":
                base = Path(*parts[1:]) if len(parts) > 1 else Path(".")
            base = self.settings.workspace_root / base
        base = base.resolve()
        if base.is_file():
            base = base.parent
        return base if base.is_dir() else None

    def _authorize(self, path: Path) -> None:
        root = next((r for r in self.roots if path == r or path.is_relative_to(r)), None)
        if root is None:
            raise PathDenied(f"'{self.display(path)}' is outside the paths this skill may access")

        folded = str(path).casefold()
        editing_config = self.include_config and (path == self.config_root or path.is_relative_to(self.config_root))
        for protected in self.settings.protected_paths:
            if editing_config and protected.resolve() == self.config_root:
                continue
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
            return path.as_posix()  # Folders the user opened outside workspace/, and arch/.

    def root_labels(self) -> list[str]:
        return [self.display(root) for root in self.roots]

    def root_kind(self, root: Path) -> str:
        """'config' for arch/, 'folder' for a folder the user opened, 'workspace' otherwise."""
        if root == self.config_root:
            return "config"
        if root in {Path(f).resolve() for f in self.settings.extra_roots}:
            return "folder"
        return "workspace"


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
    from .textcodec import decode, looks_binary

    if looks_binary(data):
        raise ValueError("this looks like a binary file")
    return decode(data, partial=truncated), truncated  # UTF-8, UTF-16, Windows-1252 or Latin-1.
