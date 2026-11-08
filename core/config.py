"""Runtime settings. Paths and limits live here; policy lives in arch/."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    base_dir: Path = PROJECT_ROOT
    ollama_url: str = field(
        default_factory=lambda: os.environ.get("WORKSPACE_OLLAMA_URL", "http://127.0.0.1:11434")
    )
    num_ctx: int = field(default_factory=lambda: int(os.environ.get("WORKSPACE_NUM_CTX", "16384")))
    host: str = "127.0.0.1"
    port: int = field(default_factory=lambda: int(os.environ.get("WORKSPACE_PORT", "8000")))

    max_read_bytes: int = 200_000
    max_tool_output_chars: int = 16_000
    max_context_file_chars: int = 12_000
    max_history_chars: int = 40_000
    max_search_results: int = 100
    approval_timeout_seconds: int = 900

    def __post_init__(self) -> None:
        self.base_dir = Path(self.base_dir).resolve()
        host = urlparse(self.ollama_url).hostname
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError(
                f"Ollama URL must point at this machine, got {self.ollama_url!r}. "
                "This application does not send data to remote hosts."
            )

    @property
    def arch_dir(self) -> Path:
        return self.base_dir / "arch"

    @property
    def workspace_root(self) -> Path:
        return self.base_dir / "workspace"

    @property
    def logs_dir(self) -> Path:
        return self.base_dir / "logs"

    @property
    def data_dir(self) -> Path:
        return self.base_dir / "data"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def protected_paths(self) -> list[Path]:
        """Application internals that no tool may touch, whatever arch/ says."""
        names = ["arch", "logs", "data", "core", "static", "tests", "scripts", ".venv", ".git", "main.py"]
        return [self.base_dir / name for name in names]
