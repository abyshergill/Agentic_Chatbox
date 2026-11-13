"""#media: generate images, and make videos from images or scene descriptions.

Images are drawn by the engine chosen in Settings > Media & GPU (Stable Diffusion on this computer, an Ollama image
model, or an image API). Markdown, Python, HTML and other text files are written with #filesystem's create_file.
"""

from __future__ import annotations

import asyncio
import io
import re
from pathlib import Path

from .. import hardware
from ..fs_guard import atomic_write
from ..media import ImageRequest, MediaError, build_video, engine_for, mp4_available
from ..tools import Adapter, Tool, ToolContext, ToolError, ToolResult

IMAGE_TYPES = {".png"}
VIDEO_TYPES = {".mp4", ".gif"}
SOURCE_IMAGES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
MAX_SCENES = 12


def _slug(text: str) -> str:
    words = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-").split("-")
    return "-".join(words[:6])[:50] or "image"


def _free(path: Path) -> Path:
    """path, or path-2, path-3... if it exists."""
    if not path.exists():
        return path
    for n in range(2, 1000):
        candidate = path.with_name(f"{path.stem}-{n}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise ToolError(f"too many files named like {path.name}")


def _target(ctx: ToolContext, args: dict, folder: str, name: str, allowed: set[str]) -> Path:
    guard = ctx.guard
    raw = (args.get("path") or "").strip()
    if raw:
        target = guard.resolve(raw)
        if target.suffix.lower() not in allowed:
            raise ToolError(f"the path must end in {' or '.join(sorted(allowed))}")
        if target.exists() and not args.get("overwrite"):
            raise ToolError(f"'{guard.display(target)}' already exists. Choose another name, or pass overwrite true.")
        return target
    return guard.resolve(str(ctx.output_dir() / folder / name))


def _engine_line(ctx: ToolContext) -> str:
    cfg = hardware.config(ctx.settings).load()
    packages = hardware.packages()
    engine = cfg["image_engine"]
    if engine == "auto":
        engine = "diffusers" if packages["torch"] and packages["diffusers"] else "ollama" if cfg["ollama_image_model"] else "api"
    if engine == "diffusers":
        where = "GPU" if hardware.gpu_enabled(ctx.settings) else "CPU"
        return f"Engine: Stable Diffusion ({cfg['diffusers_model']}) on this computer's {where}"
    if engine == "ollama":
        return f"Engine: Ollama ({cfg['ollama_image_model']}) on this computer"
    provider = engine_for(ctx.settings).provider(cfg)
    if provider is None:
        return "Engine: none is set up yet (Settings > Media & GPU)"
    from ..models import is_local_url

    remote = "" if is_local_url(provider["base_url"]) else " — REMOTE: the prompt is sent to this provider"
    return f"Engine: {provider['name']} API ({cfg['api_image_model']}){remote}"


def _check_write(ctx: ToolContext) -> None:
    if not ctx.permissions.check("filesystem", "write").allowed:
        raise ToolError("writing files is not permitted")


def _size(args: dict) -> tuple[int, int]:
    width, height = int(args.get("width", 512)), int(args.get("height", 512))
    if not (128 <= width <= 2048 and 128 <= height <= 2048):
        raise ToolError("width and height must be between 128 and 2048 pixels")
    return width, height


async def _draw(ctx: ToolContext, prompt: str, args: dict, seed: int | None) -> tuple[bytes, str]:
    width, height = _size(args)
    request = ImageRequest(prompt=prompt, negative_prompt=args.get("negative_prompt") or "", width=width,
                           height=height, steps=args.get("steps"), seed=seed)
    engine = engine_for(ctx.settings)
    try:
        return await engine.generate(request)
    except MediaError as exc:
        raise ToolError(str(exc)) from exc
    except asyncio.CancelledError:
        engine.cancel.set()  # Stops a local generation that is still running in its thread.
        raise


# ---- generate_image


def _image_plan(ctx: ToolContext, args: dict) -> list[Path]:
    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        raise ToolError("prompt is empty: describe the image to draw")
    count = int(args.get("count", 1))
    if not 1 <= count <= 4:
        raise ToolError("count must be between 1 and 4")
    _size(args)
    first = _target(ctx, args, "images", f"{_slug(prompt)}.png", IMAGE_TYPES)
    if count == 1:
        return [_free(first) if not args.get("path") else first]
    return [first.with_name(f"{first.stem}-{n}{first.suffix}") for n in range(1, count + 1)]


def _image_check(ctx: ToolContext, args: dict) -> None:
    _check_write(ctx)
    _image_plan(ctx, args)


def _image_preview(ctx: ToolContext, args: dict) -> str:
    paths = _image_plan(ctx, args)
    width, height = _size(args)
    return "\n".join([f"Generate {len(paths)} image(s), {width}×{height}", _engine_line(ctx),
                      f"Prompt: {args['prompt'].strip()}", "",
                      *[f"Save to {ctx.guard.display(p)}" for p in paths]])


async def generate_image(ctx: ToolContext, args: dict) -> ToolResult:
    _image_check(ctx, args)  # Revalidated after approval.
    paths = _image_plan(ctx, args)
    seed = args.get("seed")
    saved, how = [], ""
    for index, target in enumerate(paths):
        data, how = await _draw(ctx, args["prompt"].strip(), args, None if seed is None else int(seed) + index)
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(target, data)
        saved.append(ctx.guard.display(target))
    return ToolResult(True, f"Saved {len(saved)} image(s): {', '.join(saved)} ({how}). They are shown to the user "
                            "in the chat; do not describe details you cannot see.", saved)


# ---- create_video


def _video_plan(ctx: ToolContext, args: dict) -> tuple[list[str], list[Path], Path]:
    scenes = [s.strip() for s in args.get("scenes") or [] if s.strip()]
    sources = [ctx.guard.resolve(p) for p in args.get("images") or [] if p.strip()]
    if not scenes and not sources:
        raise ToolError("give scenes (one image prompt per scene) or images (paths of existing images)")
    if len(scenes) + len(sources) > MAX_SCENES:
        raise ToolError(f"at most {MAX_SCENES} scenes and images in one video")
    for source in sources:
        if source.suffix.lower() not in SOURCE_IMAGES:
            raise ToolError(f"'{ctx.guard.display(source)}' is not an image (png, jpg, webp, gif, bmp)")
        if not source.is_file():
            raise ToolError(f"'{ctx.guard.display(source)}' does not exist")
    seconds = int(args.get("seconds_per_image", 3))
    if not 1 <= seconds <= 20:
        raise ToolError("seconds_per_image must be between 1 and 20")
    title = args.get("title") or (scenes[0] if scenes else sources[0].stem)
    suffix = ".mp4" if mp4_available() else ".gif"
    target = _target(ctx, args, "videos", f"{_slug(title)}{suffix}", VIDEO_TYPES)
    if not args.get("path"):
        target = _free(target)
    return scenes, sources, target


def _video_check(ctx: ToolContext, args: dict) -> None:
    _check_write(ctx)
    _video_plan(ctx, args)


def _video_preview(ctx: ToolContext, args: dict) -> str:
    scenes, sources, target = _video_plan(ctx, args)
    seconds = int(args.get("seconds_per_image", 3))
    count = len(scenes) + len(sources)
    total = seconds * count - (0.8 * (count - 1) if args.get("transition", True) else 0)
    lines = [f"Create a {total:.0f}-second video ({'MP4' if target.suffix == '.mp4' else 'animated GIF'})",
             f"Save to {ctx.guard.display(target)}"]
    if scenes:
        lines += [_engine_line(ctx), f"Generate {len(scenes)} scene image(s):", *[f"  {n}. {s}" for n, s in enumerate(scenes, 1)]]
    if sources:
        lines += [f"Use {len(sources)} existing image(s):", *[f"  {ctx.guard.display(s)}" for s in sources]]
    return "\n".join(lines)


async def create_video(ctx: ToolContext, args: dict) -> ToolResult:
    from PIL import Image

    _video_check(ctx, args)
    scenes, sources, target = _video_plan(ctx, args)
    width, height = _size({"width": args.get("width") or 768, "height": args.get("height") or 432})
    frames, created, how = [], [], ""
    for source in sources:
        with Image.open(source) as image:
            frames.append(image.convert("RGB"))
    seed = args.get("seed")
    style = (args.get("style") or "").strip()
    for index, scene in enumerate(scenes):
        prompt = f"{scene}, {style}" if style else scene
        data, how = await _draw(ctx, prompt, {**args, "width": width, "height": height},
                                None if seed is None else int(seed) + index)
        frames.append(Image.open(io.BytesIO(data)).convert("RGB"))
        if args.get("keep_images"):
            still = _free(target.parent / "frames" / f"{target.stem}-{index + 1}.png")
            still.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(still, data)
            created.append(ctx.guard.display(still))
    try:
        written = await asyncio.to_thread(
            build_video, frames, target, seconds_per_image=float(args.get("seconds_per_image") or 3),
            fps=int(args.get("fps") or 24), transition=0.8 if args.get("transition", True) else 0,
            motion=bool(args.get("motion", True)), captions=args.get("captions") or None, size=(width, height))
    except MediaError as exc:
        raise ToolError(str(exc)) from exc
    label = ctx.guard.display(written)
    note = "" if written.suffix == ".mp4" else " (saved as an animated GIF: install video support in Settings > Media & GPU for MP4)"
    made = f"; images drawn with {how}" if how else ""
    return ToolResult(True, f"Video saved to {label}: {len(frames)} scene(s), {written.stat().st_size / 1e6:.1f} MB{note}{made}.",
                      [label, *created])


adapter = Adapter(
    app_id="media",
    tools=[
        Tool("generate_image", "media", "image",
             "Generate pictures from a text description: illustrations, photos, art, icons, concept images, "
             "backgrounds. Write a detailed visual prompt (subject, setting, style, lighting, colours). Saved as PNG "
             "to path, or <current folder>/images/<prompt>.png. For charts of data use #data instead.",
             {"prompt": {"type": "string", "description": "What to draw, in detail"},
              "negative_prompt": {"type": "string", "description": "Optional: what to avoid, e.g. 'blurry, text'"},
              "width": {"type": "integer", "description": "Pixels, default 512 (multiples of 64 work best)"},
              "height": {"type": "integer", "description": "Pixels, default 512"},
              "count": {"type": "integer", "description": "How many variations, 1-4 (default 1)"},
              "steps": {"type": "integer", "description": "Optional quality steps; leave out for the model's default"},
              "seed": {"type": "integer", "description": "Optional, for a repeatable result"},
              "path": {"type": "string", "description": "Optional .png path"},
              "overwrite": {"type": "boolean", "description": "Replace the file if it exists"}},
             generate_image, required=["prompt"], precheck=_image_check, preview=_image_preview),
        Tool("create_video", "media", "video",
             "Make a short video: give scenes (one image prompt per scene; each is generated, then joined with "
             "slow zoom and cross-fades) and/or images (paths of existing pictures for a slideshow). Optional "
             "captions, one per scene. Saved as MP4 to path, or <current folder>/videos/<title>.mp4.",
             {"scenes": {"type": "array", "items": {"type": "string"}, "description": "Image prompts, one per scene, in order"},
              "images": {"type": "array", "items": {"type": "string"}, "description": "Existing image paths, shown before the scenes"},
              "captions": {"type": "array", "items": {"type": "string"}, "description": "Optional text on each scene, in order"},
              "style": {"type": "string", "description": "Optional style added to every scene prompt, e.g. 'watercolor'"},
              "title": {"type": "string", "description": "Short title, used for the file name"},
              "seconds_per_image": {"type": "integer", "description": "1-20, default 3"},
              "fps": {"type": "integer", "description": "Frames per second, default 24"},
              "width": {"type": "integer", "description": "Default 768"},
              "height": {"type": "integer", "description": "Default 432"},
              "motion": {"type": "boolean", "description": "Slow zoom on each scene (default true)"},
              "transition": {"type": "boolean", "description": "Cross-fade between scenes (default true)"},
              "keep_images": {"type": "boolean", "description": "Also save each generated scene as a PNG"},
              "seed": {"type": "integer", "description": "Optional, for repeatable scenes"},
              "path": {"type": "string", "description": "Optional .mp4 (or .gif) path"},
              "overwrite": {"type": "boolean", "description": "Replace the file if it exists"}},
             create_video, precheck=_video_check, preview=_video_preview),
    ],
)
