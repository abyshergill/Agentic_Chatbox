"""Getting models from Settings > Models: download a .gguf file from a link, or pull a model into Ollama.

Downloads run in the background, one task each, and report progress through `jobs()`. A .gguf download is written to
models/<name>.gguf.part and renamed when complete, after checking that it really is a GGUF file; files in models/ show
up in the model list automatically.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx

from .config import Settings

HF_BLOB = re.compile(r"^https://(?:www\.)?huggingface\.co/([^/]+/[^/]+)/blob/(.+)$")
FILE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
OLLAMA_NAME = re.compile(r"^[A-Za-z0-9][\w./:-]{0,199}$")


def normalize_gguf_url(url: str) -> str:
    """Accepts Hugging Face page links ('/blob/') by turning them into download links ('/resolve/')."""
    url = url.strip()
    match = HF_BLOB.match(url)
    if match:
        url = f"https://huggingface.co/{match.group(1)}/resolve/{match.group(2)}"
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Give an https:// link to a .gguf file (for example from huggingface.co).")
    return url


def file_name_for(url: str) -> str:
    name = unquote(Path(urlparse(url).path).name)
    name = FILE_NAME.sub("-", name).strip("-.")
    if not name.lower().endswith(".gguf"):
        raise ValueError("The link must point at a .gguf file. On Hugging Face, open the file and copy its link.")
    return name


@dataclass
class Job:
    id: str
    kind: str  # gguf | ollama
    name: str
    source: str
    status: str = "running"  # running | done | error | cancelled
    received: int = 0
    total: int = 0
    detail: str = ""
    started: float = field(default_factory=time.time)

    def public(self) -> dict:
        data = asdict(self)
        data["percent"] = round(100 * self.received / self.total, 1) if self.total else None
        return data


class Downloads:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._jobs: dict[str, Job] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    @property
    def models_dir(self) -> Path:
        return self.settings.base_dir / "models"

    def jobs(self) -> list[dict]:
        return [job.public() for job in sorted(self._jobs.values(), key=lambda j: j.started, reverse=True)][:20]

    def _start(self, job: Job, work) -> Job:
        self._jobs[job.id] = job
        self._tasks[job.id] = asyncio.create_task(work(job))
        return job

    def cancel(self, job_id: str) -> bool:
        task = self._tasks.get(job_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    def clear_finished(self) -> None:
        for job_id in [j.id for j in self._jobs.values() if j.status != "running"]:
            self._jobs.pop(job_id, None)
            self._tasks.pop(job_id, None)

    # ---- .gguf from a link

    def download_gguf(self, url: str) -> Job:
        url = normalize_gguf_url(url)
        name = file_name_for(url)
        if (self.models_dir / name).exists():
            raise ValueError(f"models/{name} already exists.")
        if any(j.kind == "gguf" and j.name == name and j.status == "running" for j in self._jobs.values()):
            raise ValueError(f"{name} is already downloading.")
        return self._start(Job(secrets.token_hex(6), "gguf", name, url), self._download)

    async def _download(self, job: Job) -> None:
        self.models_dir.mkdir(parents=True, exist_ok=True)
        part = self.models_dir / f"{job.name}.part"
        timeout = httpx.Timeout(connect=20, read=120, write=30, pool=20)
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                async with client.stream("GET", job.source, headers={"User-Agent": "agentic-chatbox"}) as response:
                    if response.status_code != 200:
                        hint = " The file may be gated: accept its license on Hugging Face first." if response.status_code in (401, 403) else ""
                        raise ValueError(f"the server answered {response.status_code}.{hint}")
                    if urlparse(str(response.url)).scheme != "https":
                        raise ValueError("the download was redirected to an insecure address")
                    job.total = int(response.headers.get("content-length") or 0)
                    checked = False
                    with part.open("wb") as handle:
                        async for chunk in response.aiter_bytes(1 << 20):
                            if not checked:
                                if not chunk.startswith(b"GGUF"):
                                    raise ValueError("that link does not lead to a GGUF model file")
                                checked = True
                            handle.write(chunk)
                            job.received += len(chunk)
            if job.total and job.received != job.total:
                raise ValueError("the download ended early; try again")
            part.rename(self.models_dir / job.name)
            job.status, job.detail = "done", f"Saved to models/{job.name}"
        except asyncio.CancelledError:
            job.status, job.detail = "cancelled", "Cancelled"
        except (httpx.HTTPError, OSError, ValueError) as exc:
            job.status, job.detail = "error", str(exc) or type(exc).__name__
        finally:
            part.unlink(missing_ok=True)

    # ---- ollama pull

    def pull_ollama(self, name: str) -> Job:
        name = name.strip()
        if not OLLAMA_NAME.match(name):
            raise ValueError("Give an Ollama model name, like qwen2.5-coder:7b or llama3.2.")
        if any(j.kind == "ollama" and j.name == name and j.status == "running" for j in self._jobs.values()):
            raise ValueError(f"{name} is already being pulled.")
        return self._start(Job(secrets.token_hex(6), "ollama", name, self.settings.ollama_url), self._pull)

    async def _pull(self, job: Job) -> None:
        timeout = httpx.Timeout(connect=10, read=600, write=30, pool=10)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", f"{self.settings.ollama_url}/api/pull",
                                         json={"model": job.name, "stream": True}) as response:
                    if response.status_code != 200:
                        raise ValueError(f"Ollama answered {response.status_code}: {(await response.aread())[:200]!r}")
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        data = json.loads(line)
                        if data.get("error"):
                            raise ValueError(data["error"])
                        job.detail = data.get("status", job.detail)
                        if data.get("total"):
                            job.total, job.received = int(data["total"]), int(data.get("completed") or 0)
            job.status, job.detail = "done", f"{job.name} is ready"
        except asyncio.CancelledError:
            job.status, job.detail = "cancelled", "Cancelled"
        except httpx.ConnectError:
            job.status, job.detail = "error", "Ollama is not running. Start it with: ollama serve"
        except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
            job.status, job.detail = "error", str(exc) or type(exc).__name__

    # ---- media support: Python packages and the Stable Diffusion model

    def install_packages(self, name: str, packages: list[str]) -> Job:
        """pip-installs packages into the app's own Python environment."""
        if any(j.kind == "install" and j.status == "running" for j in self._jobs.values()):
            raise ValueError("An installation is already running.")
        return self._start(Job(secrets.token_hex(6), "install", name, " ".join(packages)), self._install)

    async def _install(self, job: Job) -> None:
        import sys

        job.detail = "Starting pip…"
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--progress-bar", "off",
                *job.source.split(), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                stdin=asyncio.subprocess.DEVNULL)
            tail: list[str] = []
            assert process.stdout is not None
            async for raw in process.stdout:
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    tail = (tail + [line])[-6:]
                    job.detail = line[:200]
            if await process.wait() != 0:
                raise ValueError("pip failed: " + " | ".join(tail)[-400:])
            job.status, job.detail = "done", "Installed. Image and video tools are ready."
        except asyncio.CancelledError:
            if process and process.returncode is None:
                process.kill()
            job.status, job.detail = "cancelled", "Cancelled"
        except (OSError, ValueError) as exc:
            job.status, job.detail = "error", str(exc) or type(exc).__name__

    def run_blocking(self, kind: str, name: str, work, done: str) -> Job:
        """Runs a blocking function in a thread as a job (e.g. downloading the image model)."""
        if any(j.kind == kind and j.status == "running" for j in self._jobs.values()):
            raise ValueError(f"{name} is already running.")

        async def runner(job: Job) -> None:
            job.detail = "Downloading… this can take several minutes"
            try:
                await asyncio.to_thread(work)
                job.status, job.detail = "done", done
            except asyncio.CancelledError:
                job.status, job.detail = "cancelled", "Cancelled (the download finishes in the background)"
            except Exception as exc:  # noqa: BLE001 - shown to the user as the job's error
                job.status, job.detail = "error", str(exc)[:400] or type(exc).__name__

        return self._start(Job(secrets.token_hex(6), kind, name, ""), runner)
