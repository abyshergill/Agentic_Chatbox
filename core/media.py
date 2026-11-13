"""Images and videos: the engines behind #media's generate_image and create_video.

Images come from one of three engines, chosen in Settings > Media & GPU ("auto" takes the first that is ready):
- diffusers: Stable Diffusion on this computer (default model stabilityai/sd-turbo), on the GPU when allowed.
- ollama: an Ollama image model such as x/z-image-turbo (experimental in Ollama).
- api: an OpenAI-compatible /images/generations endpoint of a provider from Settings > Models. Remote providers
  receive the prompt.

Videos are assembled from images (given, or generated one per scene) with gentle zoom and cross-fades, and saved as
MP4 with imageio-ffmpeg, or as an animated GIF with Pillow when no MP4 encoder is installed.
"""

from __future__ import annotations

import asyncio
import base64
import io
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from . import hardware
from .config import Settings
from .models import ModelStore, is_local_url

TURBO_HINTS = ("turbo", "lightning", "schnell", "lcm", "hyper")


class MediaError(Exception):
    """A failure the user (or the assistant) can act on."""


@dataclass
class ImageRequest:
    prompt: str
    negative_prompt: str = ""
    width: int = 512
    height: int = 512
    steps: int | None = None
    seed: int | None = None


def snap(value: int, low: int = 256, high: int = 1536) -> int:
    """Image sides must be multiples of 64 for Stable Diffusion."""
    return max(low, min(high, int(round(value / 64)) * 64))


class MediaEngine:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._pipe = None
        self._pipe_key: tuple[str, str] | None = None
        self._lock = threading.Lock()
        self.cancel = threading.Event()
        self.last_device = ""

    # ---- choosing an engine

    def config(self) -> dict:
        return hardware.config(self.settings).load()

    def provider(self, cfg: dict) -> dict | None:
        if not cfg["api_provider"]:
            return None
        return ModelStore(self.settings.data_dir / "models.json").provider(cfg["api_provider"])

    async def ollama_models(self) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=3) as http:
                response = await http.get(f"{self.settings.ollama_url}/api/tags")
            return [m.get("name", "") for m in response.json().get("models", [])]
        except (httpx.HTTPError, ValueError):
            return []

    async def status(self) -> dict:
        cfg = self.config()
        pkgs = hardware.packages()
        diffusers_ready = pkgs["torch"] and pkgs["diffusers"] and pkgs["transformers"]
        ollama_model = cfg["ollama_image_model"]
        installed = await self.ollama_models()
        ollama_ready = bool(ollama_model) and any(name == ollama_model or name.split(":")[0] == ollama_model for name in installed)
        provider = self.provider(cfg)
        api_ready = bool(provider and cfg["api_image_model"])
        engines = {"diffusers": diffusers_ready, "ollama": ollama_ready, "api": api_ready}
        chosen = cfg["image_engine"]
        if chosen == "auto":
            chosen = next((name for name, ready in engines.items() if ready), "")
        elif not engines[chosen]:
            chosen = ""
        return {
            "engine": chosen, "engines": engines, "config": cfg,
            "video": {"mp4": pkgs["imageio"] and pkgs["imageio-ffmpeg"], "gif": True},
            "provider_remote": bool(provider and not is_local_url(provider["base_url"])),
            "provider_name": provider["name"] if provider else "",
            "model_cached": self.model_cached(cfg["diffusers_model"]) if diffusers_ready else False,
            "device": self.last_device,
        }

    @staticmethod
    def model_cached(model: str) -> bool:
        if Path(model).expanduser().is_dir():
            return True
        try:
            from huggingface_hub import try_to_load_from_cache  # noqa: PLC0415
        except ImportError:
            return False
        return isinstance(try_to_load_from_cache(model, "model_index.json"), str)

    async def describe(self) -> str:
        """One line for the preview: which engine will draw, and where."""
        status = await self.status()
        engine, cfg = status["engine"], status["config"]
        if engine == "diffusers":
            where = "GPU" if hardware.gpu_enabled(self.settings) else "CPU"
            return f"Engine: Stable Diffusion ({cfg['diffusers_model']}) on this computer's {where}"
        if engine == "ollama":
            return f"Engine: Ollama ({cfg['ollama_image_model']}) on this computer"
        if engine == "api":
            remote = " — REMOTE: the prompt is sent to this provider" if status["provider_remote"] else ""
            return f"Engine: {status['provider_name']} API ({cfg['api_image_model']}){remote}"
        return "Engine: none is set up (Settings > Media & GPU)"

    # ---- generating

    async def generate(self, request: ImageRequest) -> tuple[bytes, str]:
        """Returns PNG bytes and a short description of how it was made."""
        status = await self.status()
        engine = status["engine"]
        if not request.prompt.strip():
            raise MediaError("the prompt is empty")
        if engine == "diffusers":
            return await asyncio.to_thread(self._diffusers, request, status["config"])
        if engine == "ollama":
            return await self._ollama(request, status["config"])
        if engine == "api":
            return await self._api(request, status["config"])
        raise MediaError(
            "image generation is not set up. Ask the user to open Settings > Media & GPU and install local image "
            "generation (Stable Diffusion), or choose an Ollama image model or an image API.")

    def _diffusers(self, request: ImageRequest, cfg: dict) -> tuple[bytes, str]:
        model = cfg["diffusers_model"]
        with self._lock:
            pipe, device = self._pipeline(model, cfg["use_gpu"] and hardware.detect()["gpu"]["available"])
            import torch  # noqa: PLC0415

            turbo = any(hint in model.lower() for hint in TURBO_HINTS)
            steps = request.steps or (2 if turbo else 25)
            steps = max(1, min(steps, 8 if turbo else 60))
            generator = torch.Generator("cpu").manual_seed(request.seed) if request.seed is not None else None
            self.cancel.clear()

            def on_step(pipeline, step, timestep, kwargs):
                if self.cancel.is_set():
                    pipeline._interrupt = True  # noqa: SLF001 - diffusers' documented way to stop early
                return kwargs

            started = time.monotonic()
            options = {"prompt": request.prompt, "num_inference_steps": steps, "width": snap(request.width),
                       "height": snap(request.height), "generator": generator, "callback_on_step_end": on_step}
            if turbo:
                options["guidance_scale"] = 0.0
            elif request.negative_prompt:
                options["negative_prompt"] = request.negative_prompt
            try:
                image = pipe(**options).images[0]
            except Exception as exc:  # torch and diffusers raise many kinds of errors
                raise MediaError(f"image generation failed: {exc}") from exc
            if self.cancel.is_set():
                raise MediaError("image generation was stopped")
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            return buffer.getvalue(), f"{model} on {device.upper()}, {steps} steps, {time.monotonic() - started:.1f}s"

    def _pipeline(self, model: str, use_gpu: bool):
        device = hardware.torch_device(use_gpu)
        if self._pipe is not None and self._pipe_key == (model, device):
            return self._pipe, device
        try:
            import torch  # noqa: PLC0415
            from diffusers import AutoPipelineForText2Image  # noqa: PLC0415
        except ImportError as exc:
            raise MediaError("Stable Diffusion is not installed (Settings > Media & GPU > Install)") from exc
        self._pipe = None
        dtype = torch.float16 if device == "cuda" else torch.float32  # float16 can give black images on MPS and CPU.
        try:
            try:
                pipe = AutoPipelineForText2Image.from_pretrained(model, torch_dtype=dtype, variant="fp16")
            except (OSError, ValueError):
                pipe = AutoPipelineForText2Image.from_pretrained(model, torch_dtype=dtype)
        except Exception as exc:
            raise MediaError(f"could not load the image model {model}: {exc}") from exc
        pipe = pipe.to(device)
        if device == "mps":
            pipe.enable_attention_slicing()  # Less memory on Macs with 8-16 GB.
        pipe.set_progress_bar_config(disable=True)
        self._pipe, self._pipe_key, self.last_device = pipe, (model, device), device
        return pipe, device

    def unload(self) -> None:
        """Frees the model, e.g. after the GPU switch changes."""
        with self._lock:
            self._pipe, self._pipe_key = None, None

    def download_model(self) -> str:
        """Downloads the configured Stable Diffusion model into the Hugging Face cache (blocking)."""
        from huggingface_hub import snapshot_download  # noqa: PLC0415

        model = self.config()["diffusers_model"]
        if Path(model).expanduser().is_dir():
            return model
        patterns = ["*.json", "*.txt", "*.model", "*fp16.safetensors"]
        path = snapshot_download(model, allow_patterns=patterns)
        if not any(Path(path).rglob("*.safetensors")):  # The model has no fp16 files: take the full ones.
            snapshot_download(model, allow_patterns=["*.json", "*.txt", "*.model", "*.safetensors"])
        return model

    async def _ollama(self, request: ImageRequest, cfg: dict) -> tuple[bytes, str]:
        body: dict = {"model": cfg["ollama_image_model"], "prompt": request.prompt, "stream": False,
                      "width": snap(request.width), "height": snap(request.height)}
        if request.steps:
            body["steps"] = request.steps
        options: dict = {}
        if request.seed is not None:
            options["seed"] = request.seed
        if not hardware.gpu_enabled(self.settings):
            options["num_gpu"] = 0
        if options:
            body["options"] = options
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=5, read=900, write=30, pool=5)) as http:
                response = await http.post(f"{self.settings.ollama_url}/api/generate", json=body)
        except httpx.HTTPError as exc:
            raise MediaError(f"Ollama is not reachable: {exc}") from exc
        if response.status_code != 200:
            raise MediaError(f"Ollama answered {response.status_code}: {response.text[:300]}")
        data = response.json()
        encoded = data.get("image") or (data.get("images") or [None])[0]
        if not encoded:
            raise MediaError(f"{cfg['ollama_image_model']} returned no image. This Ollama version may not support "
                             "image generation over its API; choose another engine in Settings > Media & GPU.")
        return _as_png(base64.b64decode(encoded)), f"Ollama {cfg['ollama_image_model']}, {time.monotonic() - started:.1f}s"

    async def _api(self, request: ImageRequest, cfg: dict) -> tuple[bytes, str]:
        provider = self.provider(cfg)
        if provider is None:
            raise MediaError("the image API provider no longer exists (Settings > Media & GPU)")
        headers = {"Authorization": f"Bearer {provider['api_key']}"} if provider.get("api_key") else {}
        body = {"model": cfg["api_image_model"], "prompt": request.prompt, "n": 1,
                "size": f"{snap(request.width)}x{snap(request.height)}"}
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10, read=300, write=30, pool=10)) as http:
                response = await http.post(f"{provider['base_url'].rstrip('/')}/images/generations", json=body, headers=headers)
                if response.status_code != 200:
                    raise MediaError(f"{provider['name']} answered {response.status_code}: {response.text[:300]}")
                item = (response.json().get("data") or [{}])[0]
                if item.get("b64_json"):
                    raw = base64.b64decode(item["b64_json"])
                elif str(item.get("url", "")).startswith("https://"):
                    raw = (await http.get(item["url"])).content
                else:
                    raise MediaError(f"{provider['name']} returned no image")
        except httpx.HTTPError as exc:
            raise MediaError(f"{provider['name']} is not reachable: {exc}") from exc
        return _as_png(raw), f"{provider['name']} {cfg['api_image_model']}, {time.monotonic() - started:.1f}s"


def _as_png(raw: bytes) -> bytes:
    from PIL import Image  # noqa: PLC0415

    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except Exception as exc:
        raise MediaError("the engine returned data that is not an image") from exc
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


_ENGINES: dict[Path, MediaEngine] = {}


def engine_for(settings: Settings) -> MediaEngine:
    """One engine per app, so the loaded model is reused between images."""
    key = settings.base_dir
    if key not in _ENGINES:
        _ENGINES[key] = MediaEngine(settings)
    return _ENGINES[key]


# ---- video -------------------------------------------------------------------------


def mp4_available() -> bool:
    packages = hardware.packages()
    return packages["imageio"] and packages["imageio-ffmpeg"]


def build_video(images: list, target: Path, *, seconds_per_image: float = 3.0, fps: int = 24,
                transition: float = 0.8, motion: bool = True, captions: list[str] | None = None,
                size: tuple[int, int] | None = None) -> Path:
    """Writes a slideshow video from PIL images. Returns the written path (.mp4, or .gif without an MP4 encoder)."""
    from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415

    if not images:
        raise MediaError("no images to make a video from")
    as_gif = target.suffix.lower() == ".gif" or not mp4_available()
    if as_gif:
        target = target.with_suffix(".gif")
        fps = min(fps, 12)
    width, height = size or images[0].size
    cap = 640 if as_gif else 1920
    scale = min(1.0, cap / max(width, height))
    width, height = int(width * scale) // 16 * 16, int(height * scale) // 16 * 16
    frames_each = max(1, round(seconds_per_image * fps))
    fade = min(max(0, round(transition * fps)), frames_each // 2)
    font = None
    if captions:
        try:
            font = ImageFont.load_default(size=max(16, height // 18))
        except TypeError:
            font = ImageFont.load_default()

    def fit(image):
        image = image.convert("RGB")
        ratio = max(width / image.width, height / image.height)
        resized = image.resize((max(width, round(image.width * ratio)), max(height, round(image.height * ratio))), Image.LANCZOS)
        left, top = (resized.width - width) // 2, (resized.height - height) // 2
        return resized.crop((left, top, left + width, top + height))

    def caption(frame, text):
        if not text or font is None:
            return frame
        draw = ImageDraw.Draw(frame, "RGBA")
        box = draw.textbbox((0, 0), text, font=font)
        tw, th = box[2] - box[0], box[3] - box[1]
        pad = th // 2 + 6
        draw.rectangle((0, height - th - 3 * pad, width, height), fill=(0, 0, 0, 140))
        draw.text(((width - tw) // 2, height - th - 2 * pad), text, font=font, fill=(255, 255, 255, 255))
        return frame

    def clip(index):
        base = fit(images[index])
        text = captions[index] if captions and index < len(captions) else ""
        for n in range(frames_each):
            frame = base
            if motion:
                zoom = 1 + 0.08 * n / frames_each
                cw, ch = width / zoom, height / zoom
                left, top = (width - cw) / 2, (height - ch) / 2
                frame = base.resize((width, height), Image.BICUBIC, box=(left, top, left + cw, top + ch))
            yield caption(frame.copy(), text)

    def frames():
        tail: list = []
        for index in range(len(images)):
            current = list(clip(index))
            if tail and fade:
                for n in range(fade):
                    yield Image.blend(tail[n], current[n], (n + 1) / (fade + 1))
                current = current[fade:]
            if index < len(images) - 1 and fade:
                tail = current[-fade:]
                current = current[:-fade]
            yield from current

    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.stem}.part{target.suffix}")
    try:
        if as_gif:
            sequence = list(frames())
            sequence[0].save(temp, format="GIF", save_all=True, append_images=sequence[1:], duration=round(1000 / fps), loop=0, optimize=True)
        else:
            import imageio.v2 as imageio  # noqa: PLC0415
            import numpy as np  # noqa: PLC0415

            with imageio.get_writer(temp, format="FFMPEG", fps=fps, codec="libx264", quality=8,
                                    pixelformat="yuv420p", macro_block_size=16) as writer:
                for frame in frames():
                    writer.append_data(np.asarray(frame))
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)
    return target
