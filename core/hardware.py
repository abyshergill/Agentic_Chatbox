"""GPU detection and the "Use GPU" switch from Settings > Media & GPU.

When a GPU is found and the switch is on, local work uses it: Ollama and llama.cpp offload model layers, and image
generation runs on CUDA (NVIDIA), ROCm (AMD) or Metal (Apple Silicon). With the switch off, everything runs on the CPU.
LM Studio and API providers manage their own hardware and are not affected.
"""

from __future__ import annotations

import functools
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import Settings

DEFAULTS = {
    "use_gpu": True,
    "image_engine": "auto",  # auto | diffusers | ollama | api
    "diffusers_model": "stabilityai/sd-turbo",
    "ollama_image_model": "x/z-image-turbo",
    "api_provider": "",
    "api_image_model": "",
}
ENGINES = {"auto", "diffusers", "ollama", "api"}


class MediaConfig:
    """data/media.json: the GPU switch and which engine draws images."""

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        merged = {**DEFAULTS, **{k: v for k, v in data.items() if k in DEFAULTS}}
        merged["use_gpu"] = bool(merged["use_gpu"])
        if merged["image_engine"] not in ENGINES:
            merged["image_engine"] = "auto"
        return merged

    def save(self, values: dict) -> dict:
        data = self.load()
        for key, value in values.items():
            if key not in DEFAULTS or value is None:
                continue
            if key == "use_gpu":
                data[key] = bool(value)
            elif key == "image_engine":
                if value not in ENGINES:
                    raise ValueError(f"Unknown image engine '{value}'.")
                data[key] = value
            else:
                text = str(value).strip()
                if len(text) > 300:
                    raise ValueError(f"{key.replace('_', ' ').capitalize()} is too long.")
                data[key] = text
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=self.path.parent, prefix=".media-")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        os.replace(temp, self.path)
        return data


def config(settings: Settings) -> MediaConfig:
    return MediaConfig(settings.data_dir / "media.json")


def gpu_enabled(settings: Settings) -> bool:
    """True when local work should use the GPU: one is present and the user has not switched it off."""
    return config(settings).load()["use_gpu"] and detect()["gpu"]["available"]


def _run(command: list[str]) -> str:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=5, check=False).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


@functools.lru_cache(maxsize=1)
def detect() -> dict:
    """What this computer has. Cached: hardware does not change while the app runs."""
    system, machine = platform.system(), platform.machine().lower()
    cpu = {"name": platform.processor() or machine, "cores": os.cpu_count() or 1, "memory_gb": None}
    gpu = {"available": False, "kind": "", "name": "", "memory_gb": None, "detail": "No supported GPU found."}

    if system == "Darwin":
        cpu["name"] = _run(["sysctl", "-n", "machdep.cpu.brand_string"]) or cpu["name"]
        memory = _run(["sysctl", "-n", "hw.memsize"])
        cpu["memory_gb"] = round(int(memory) / 2**30) if memory.isdigit() else None
        if machine == "arm64":
            gpu.update(available=True, kind="metal", name=f"{cpu['name']} GPU", memory_gb=cpu["memory_gb"],
                       detail="Apple Silicon GPU (Metal), sharing the computer's memory.")
    elif hasattr(os, "sysconf"):
        try:
            cpu["memory_gb"] = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30)
        except (ValueError, OSError):
            pass

    if not gpu["available"] and shutil.which("nvidia-smi"):
        line = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"]).splitlines()
        if line:
            name, _, memory = line[0].partition(",")
            gpu.update(available=True, kind="cuda", name=name.strip(),
                       memory_gb=round(float(memory) / 1024, 1) if memory.strip().replace(".", "").isdigit() else None,
                       detail=f"NVIDIA GPU (CUDA){f', {len(line)} devices' if len(line) > 1 else ''}.")
    if not gpu["available"] and shutil.which("rocm-smi"):
        name = _run(["rocm-smi", "--showproductname"])
        if name:
            gpu.update(available=True, kind="rocm", name="AMD GPU", detail="AMD GPU (ROCm).")

    return {"os": f"{system} {platform.release()}", "machine": machine, "python": sys.version.split()[0],
            "cpu": cpu, "gpu": gpu}


def torch_device(use_gpu: bool) -> str:
    """The PyTorch device image generation runs on."""
    import torch  # noqa: PLC0415 - optional dependency

    if use_gpu and torch.cuda.is_available():
        return "cuda"  # NVIDIA, and AMD through ROCm builds of PyTorch.
    if use_gpu and getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def packages() -> dict[str, bool]:
    """Optional packages for media, and whether each is installed."""
    names = {"torch": "torch", "diffusers": "diffusers", "transformers": "transformers", "accelerate": "accelerate",
             "imageio": "imageio", "imageio-ffmpeg": "imageio_ffmpeg"}
    return {label: importlib.util.find_spec(module) is not None for label, module in names.items()}
