"""Every model the app can use, behind one interface the agent already speaks.

Four kinds, told apart by the model id:
- Ollama models, by their Ollama name ('qwen2.5-coder:3b').
- .gguf files run directly with llama.cpp, no Ollama needed ('gguf:<file name>'). The app starts a private llama.cpp
  server on 127.0.0.1 for the selected file and stops it when another file is chosen or the app quits.
- LM Studio models ('lmstudio:<model>'), listed automatically while LM Studio's local server runs.
- Models from an OpenAI-compatible API ('api:<provider>:<model>'), local (LM Studio, vLLM) or hosted (OpenAI,
  Anthropic, Gemini, Groq, OpenRouter...). Hosted models are *remote*: the conversation, including file contents the
  assistant reads, is sent to that provider. They are never chosen automatically.

Provider settings and API keys live in data/models.json (mode 0600), outside every folder the assistant can reach.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlparse

import httpx

from .config import Settings
from .ollama_client import ChatTurn, OllamaClient, OllamaError
from .openai_client import ModelError, OpenAICompatibleClient

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
PROVIDER_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,29}$")

# Suggested endpoints. All speak the OpenAI chat-completions protocol.
PRESETS = [
    {"name": "OpenAI", "base_url": "https://api.openai.com/v1"},
    {"name": "Anthropic", "base_url": "https://api.anthropic.com/v1"},
    {"name": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai"},
    {"name": "Groq", "base_url": "https://api.groq.com/openai/v1"},
    {"name": "OpenRouter", "base_url": "https://openrouter.ai/api/v1"},
    {"name": "Mistral", "base_url": "https://api.mistral.ai/v1"},
    {"name": "DeepSeek", "base_url": "https://api.deepseek.com/v1"},
    {"name": "LM Studio (this computer)", "base_url": "http://127.0.0.1:1234/v1"},
    {"name": "Custom", "base_url": ""},
]


def is_local_url(url: str) -> bool:
    return (urlparse(url).hostname or "") in LOOPBACK


def check_base_url(url: str) -> str:
    url = url.strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("The API address must start with https:// (or http:// for a server on this computer).")
    if parsed.scheme == "http" and parsed.hostname not in LOOPBACK:
        raise ValueError("Remote APIs must use https://, so your API key and data are encrypted in transit.")
    return url


def check_gguf(path: str) -> Path:
    file = Path(path).expanduser().resolve()
    if file.suffix.lower() != ".gguf":
        raise ValueError("Choose a .gguf file.")
    if not file.is_file():
        raise ValueError(f"No file at {file}.")
    with file.open("rb") as handle:
        if handle.read(4) != b"GGUF":
            raise ValueError(f"{file.name} is not a valid GGUF model file.")
    return file


def mask(key: str) -> str:
    return "" if not key else ("•" * 6 + key[-4:] if len(key) > 8 else "•" * 6)


# ---- saved settings ---------------------------------------------------------


class ModelStore:
    """data/models.json: GGUF files and API providers the user added."""

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        data.setdefault("gguf", [])
        data.setdefault("providers", [])
        return data

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=self.path.parent, prefix=".models-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
            os.chmod(temp, 0o600)  # API keys: readable by you only.
            os.replace(temp, self.path)
        except BaseException:
            if os.path.exists(temp):
                os.unlink(temp)
            raise

    def provider(self, provider_id: str) -> dict | None:
        return next((p for p in self.load()["providers"] if p["id"] == provider_id), None)

    def upsert_provider(self, name: str, base_url: str, api_key: str | None, models: list[str], native_tools: bool,
                        provider_id: str | None = None) -> dict:
        name = name.strip()
        if not name or len(name) > 40:
            raise ValueError("Give the provider a name of up to 40 characters.")
        base_url = check_base_url(base_url)
        models = sorted({m.strip() for m in models if m and m.strip()})
        if not models:
            raise ValueError("Add at least one model name, or use 'Find models'.")
        if any(len(m) > 120 or any(c.isspace() for c in m) for m in models):
            raise ValueError("Model names cannot contain spaces.")
        data = self.load()
        existing = next((p for p in data["providers"] if p["id"] == provider_id), None) if provider_id else None
        if existing is None:
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "provider"
            provider_id, n = slug, 2
            while any(p["id"] == provider_id for p in data["providers"]):
                provider_id, n = f"{slug[:27]}-{n}", n + 1
            existing = {"id": provider_id}
            data["providers"].append(existing)
        key = existing.get("api_key", "") if api_key is None or api_key == "" else api_key.strip()
        if not key and not is_local_url(base_url):
            raise ValueError("A hosted API needs an API key.")
        existing.update({"name": name, "base_url": base_url, "api_key": key, "models": models,
                         "native_tools": bool(native_tools)})
        self.save(data)
        return existing

    def remove_provider(self, provider_id: str) -> bool:
        data = self.load()
        kept = [p for p in data["providers"] if p["id"] != provider_id]
        if len(kept) == len(data["providers"]):
            return False
        data["providers"] = kept
        self.save(data)
        return True

    def add_gguf(self, path: str) -> Path:
        file = check_gguf(path)
        data = self.load()
        if str(file) not in [item["path"] for item in data["gguf"]]:
            data["gguf"].append({"path": str(file)})
            self.save(data)
        return file

    def remove_gguf(self, path: str) -> bool:
        data = self.load()
        kept = [item for item in data["gguf"] if item["path"] != path]
        if len(kept) == len(data["gguf"]):
            return False
        data["gguf"] = kept
        self.save(data)
        return True


# ---- running .gguf files with llama.cpp ---------------------------------------


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class GgufRuntime:
    """Runs one .gguf file at a time in a llama.cpp server bound to 127.0.0.1.

    Uses the `llama-server` program from llama.cpp when installed (best tool calling), otherwise the server that
    ships with the llama-cpp-python package.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.process: subprocess.Popen | None = None
        self.model_path: Path | None = None
        self.port = 0
        self.kind = ""
        self.gpu = True
        self._lock = asyncio.Lock()

    @property
    def log_path(self) -> Path:
        return self.settings.logs_dir / "gguf-server.log"

    def detect(self) -> dict:
        binary = os.environ.get("WORKSPACE_LLAMA_SERVER") or shutil.which("llama-server")
        local = Path(sys.executable).parent / "llama-server"
        if not binary and local.exists():
            binary = str(local)
        if binary:
            return {"available": True, "kind": "llama-server", "native_tools": True, "detail": f"llama.cpp server at {binary}",
                    "binary": binary}
        if importlib.util.find_spec("llama_cpp") and importlib.util.find_spec("sse_starlette"):
            return {"available": True, "kind": "llama-cpp-python", "native_tools": False,
                    "detail": "llama-cpp-python server (tools are described in the prompt)"}
        return {"available": False, "kind": "", "native_tools": False,
                "detail": "No llama.cpp runtime found. Install one: pip install \"llama-cpp-python[server]\" "
                          "(inside .venv), or brew install llama.cpp."}

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def status(self) -> dict:
        return {"running": self.running, "model": self.model_path.name if self.running and self.model_path else None,
                **{k: v for k, v in self.detect().items() if k != "binary"}}

    def _command(self, runtime: dict, path: Path, port: int) -> list[str]:
        from .hardware import gpu_enabled

        ctx = str(self.settings.num_ctx)
        self.gpu = gpu_enabled(self.settings)
        if runtime["kind"] == "llama-server":
            return [runtime["binary"], "-m", str(path), "--host", "127.0.0.1", "--port", str(port), "-c", ctx,
                    "-ngl", "999" if self.gpu else "0", "--jinja", "--alias", path.name]
        return [sys.executable, "-m", "llama_cpp.server", "--model", str(path), "--host", "127.0.0.1",
                "--port", str(port), "--n_ctx", ctx, "--n_gpu_layers", "-1" if self.gpu else "0", "--model_alias", path.name]

    async def client(self, path: Path) -> OpenAICompatibleClient:
        """Starts (or reuses) the server for this file and returns a client for it."""
        async with self._lock:
            from .hardware import gpu_enabled

            if not (self.running and self.model_path == path and self.gpu == gpu_enabled(self.settings)):
                self.stop()
                await self._start(path)
            return OpenAICompatibleClient(f"http://127.0.0.1:{self.port}/v1", label=f"llama.cpp ({path.name})")

    async def _start(self, path: Path) -> None:
        runtime = self.detect()
        if not runtime["available"]:
            raise ModelError(runtime["detail"])
        if not path.is_file():
            raise ModelError(f"The model file {path} no longer exists.")
        self.port = free_port()
        self.settings.logs_dir.mkdir(parents=True, exist_ok=True)
        log = self.log_path.open("w", encoding="utf-8")
        self.process = subprocess.Popen(self._command(runtime, path, self.port), stdout=log, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, start_new_session=True)
        log.close()
        self.model_path, self.kind = path, runtime["kind"]
        deadline = time.monotonic() + 240  # Loading a large model can take a while.
        async with httpx.AsyncClient(timeout=2) as http:
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    tail = self.log_path.read_text(encoding="utf-8", errors="replace")[-600:]
                    self.process = None
                    raise ModelError(f"llama.cpp could not load {path.name}. Last log lines:\n{tail}")
                try:
                    if (await http.get(f"http://127.0.0.1:{self.port}/v1/models")).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.5)
        self.stop()
        raise ModelError(f"llama.cpp did not finish loading {path.name} within 4 minutes. See {self.log_path}.")

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None
        self.model_path = None


# ---- the router -----------------------------------------------------------------


@dataclass
class ModelEntry:
    id: str
    label: str
    kind: str  # ollama | gguf | lmstudio | api
    remote: bool = False
    detail: str = ""
    path: Path | None = None
    provider: dict = field(default_factory=dict)
    model: str = ""


class ModelRouter:
    """The agent's model backend. Same methods as OllamaClient, routed by model id."""

    def __init__(self, settings: Settings, ollama: OllamaClient | None = None):
        self.settings = settings
        self.ollama = ollama or OllamaClient(settings)
        self.store = ModelStore(settings.data_dir / "models.json")
        self.gguf = GgufRuntime(settings)
        self.ollama_error: str | None = None
        self._lmstudio: tuple[float, list[str]] = (0.0, [])
        self.lmstudio_running = False

    async def lmstudio_models(self) -> list[str]:
        """Models loaded in or downloaded to LM Studio, while its server runs. Cached for a few seconds."""
        if not self.settings.detect_local_servers:
            return []
        stamp, models = self._lmstudio
        if time.monotonic() - stamp < 5:
            return models
        try:
            found = await OpenAICompatibleClient(self.settings.lmstudio_url, label="LM Studio").list_models()
            models = [m for m in found if "embed" not in m.lower()]
            self.lmstudio_running = True
        except Exception:  # noqa: BLE001 - not running is the normal case.
            models, self.lmstudio_running = [], False
        self._lmstudio = (time.monotonic(), models)
        return models

    @property
    def models_dir(self) -> Path:
        return self.settings.base_dir / "models"

    def gguf_files(self) -> list[Path]:
        files = [Path(item["path"]) for item in self.store.load()["gguf"]]
        if self.models_dir.is_dir():
            files += sorted(p.resolve() for p in self.models_dir.glob("*.gguf") if p.is_file())
        unique: list[Path] = []
        for file in files:
            if file not in unique:
                unique.append(file)
        return unique

    async def catalog(self) -> list[ModelEntry]:
        entries: list[ModelEntry] = []
        try:
            entries += [ModelEntry(name, name, "ollama", detail="Ollama") for name in await self.ollama.list_models()]
            self.ollama_error = None
        except OllamaError as exc:
            self.ollama_error = str(exc)
        entries += [ModelEntry(f"lmstudio:{name}", name, "lmstudio", detail="LM Studio", model=name)
                    for name in await self.lmstudio_models()]
        seen: set[str] = set()
        for file in self.gguf_files():
            model_id = f"gguf:{file.name}" if f"gguf:{file.name}" not in seen else f"gguf:{file.parent.name}/{file.name}"
            seen.add(model_id)
            entries.append(ModelEntry(model_id, file.name, "gguf", detail=str(file), path=file))
        for provider in self.store.load()["providers"]:
            remote = not is_local_url(provider["base_url"])
            for model in provider["models"]:
                entries.append(ModelEntry(f"api:{provider['id']}:{model}", f"{model} ({provider['name']})", "api",
                                          remote=remote, detail=provider["base_url"], provider=provider, model=model))
        return entries

    async def _entry(self, model_id: str) -> ModelEntry | None:
        if not model_id.startswith(("gguf:", "api:", "lmstudio:")):
            return None
        return next((e for e in await self.catalog() if e.id == model_id), None)

    async def list_models(self) -> list[str]:
        entries = await self.catalog()
        if not entries:
            raise OllamaError(
                "No models are available. Start Ollama (ollama serve), add a .gguf file, or add an API model "
                "in Settings > Models.")
        return [entry.id for entry in entries]

    async def is_remote(self, model_id: str) -> bool:
        entry = await self._entry(model_id)
        return bool(entry and entry.remote)

    async def supports_tools(self, model: str) -> bool:
        if model.startswith("gguf:"):
            return self.gguf.detect()["native_tools"]
        if model.startswith("api:"):
            entry = await self._entry(model)
            return bool(entry and entry.provider.get("native_tools", True))
        if model.startswith("lmstudio:"):
            return True  # LM Studio's server accepts tools and falls back to prompting for models without support.
        return await self.ollama.supports_tools(model)

    async def chat(self, model: str, messages: list[dict], tools: list[dict] | None, allowed_tools: set[str],
                   on_token: Callable[[str], Awaitable[None]]) -> ChatTurn:
        if not model.startswith(("gguf:", "api:", "lmstudio:")):
            return await self.ollama.chat(model, messages, tools, allowed_tools, on_token)
        if model.startswith("lmstudio:"):
            client = OpenAICompatibleClient(self.settings.lmstudio_url, label="LM Studio")
            return await client.chat(model.split(":", 1)[1], messages, tools, allowed_tools, on_token)
        entry = await self._entry(model)
        if entry is None:
            raise ModelError(f"Model '{model}' is no longer configured. Pick another in the Model menu.")
        if entry.kind == "gguf":
            client = await self.gguf.client(entry.path)
            return await client.chat(entry.path.name, messages, tools, allowed_tools, on_token)
        provider = entry.provider
        client = OpenAICompatibleClient(provider["base_url"], provider.get("api_key", ""), label=provider["name"])
        return await client.chat(entry.model, messages, tools, allowed_tools, on_token)

    def shutdown(self) -> None:
        self.gguf.stop()
