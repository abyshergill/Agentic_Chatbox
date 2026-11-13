"""Images, videos, the GPU switch, and choosing a model from Settings."""

import asyncio
import io
import json

import httpx
import pytest
from PIL import Image

from core import hardware, media
from core.adapters import media as media_adapter
from core.models import GgufRuntime
from core.ollama_client import OllamaClient
from core.permissions import PermissionManager
from core.fs_guard import PathDenied
from core.tools import ToolContext, ToolError
from tests.conftest import of_type

GPU = {"os": "Test", "machine": "arm64", "python": "3", "cpu": {"name": "Test CPU", "cores": 8, "memory_gb": 16},
       "gpu": {"available": True, "kind": "metal", "name": "Test GPU", "memory_gb": 16, "detail": "Test GPU."}}


@pytest.fixture(autouse=True)
def fake_hardware(monkeypatch):
    monkeypatch.setattr(hardware, "detect", lambda: GPU)


def png(color="red", size=(64, 48)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def fake_engine(monkeypatch):
    prompts = []

    async def generate(self, request):
        prompts.append(request)
        return png(), "test engine"

    monkeypatch.setattr(media.MediaEngine, "generate", generate)
    return prompts


def tool_ctx(settings):
    return ToolContext(settings, PermissionManager(settings.arch_dir / "permission.md"), ["./workspace/projects"], None,
                       active_path="projects/demo")


# ---- GPU switch


def test_media_config_defaults_and_validation(settings):
    store = hardware.config(settings)
    assert store.load()["use_gpu"] is True and store.load()["image_engine"] == "auto"
    assert store.save({"use_gpu": False, "image_engine": "ollama", "unknown": 1})["use_gpu"] is False
    assert store.load()["image_engine"] == "ollama" and "unknown" not in store.load()
    with pytest.raises(ValueError):
        store.save({"image_engine": "magic"})


def test_gpu_switch_reaches_ollama_and_llama_cpp(settings, monkeypatch, tmp_path):
    sent = []

    class Capture(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            def handler(request):
                sent.append(json.loads(request.content))
                return httpx.Response(200, text=json.dumps({"message": {"content": "hi"}, "done": True}) + "\n")
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Capture)
    client = OllamaClient(settings)

    async def chat():
        async for _ in client._stream("m", [], None):
            pass

    asyncio.run(chat())
    assert "num_gpu" not in sent[-1]["options"]
    hardware.config(settings).save({"use_gpu": False})
    asyncio.run(chat())
    assert sent[-1]["options"]["num_gpu"] == 0

    runtime = GgufRuntime(settings)
    command = runtime._command({"kind": "llama-server", "binary": "llama-server"}, tmp_path / "m.gguf", 1)
    assert command[command.index("-ngl") + 1] == "0"
    hardware.config(settings).save({"use_gpu": True})
    command = runtime._command({"kind": "llama-server", "binary": "llama-server"}, tmp_path / "m.gguf", 1)
    assert command[command.index("-ngl") + 1] == "999"


def test_no_gpu_means_cpu_even_when_switched_on(settings, monkeypatch):
    monkeypatch.setattr(hardware, "detect", lambda: {**GPU, "gpu": {**GPU["gpu"], "available": False}})
    assert hardware.gpu_enabled(settings) is False


# ---- images


def test_generate_image_tool_saves_png(settings, fake_engine):
    ctx = tool_ctx(settings)
    result = asyncio.run(media_adapter.generate_image(ctx, {"prompt": "A red fox in snow", "count": 2, "seed": 7}))
    assert result.changed_paths == ["projects/demo/images/a-red-fox-in-snow-1.png", "projects/demo/images/a-red-fox-in-snow-2.png"]
    assert [r.seed for r in fake_engine] == [7, 8]
    saved = settings.workspace_root / result.changed_paths[0]
    assert Image.open(saved).format == "PNG"
    # A second image with the same prompt gets a new name instead of overwriting.
    again = asyncio.run(media_adapter.generate_image(ctx, {"prompt": "A red fox in snow"}))
    assert again.changed_paths == ["projects/demo/images/a-red-fox-in-snow.png"]
    again = asyncio.run(media_adapter.generate_image(ctx, {"prompt": "A red fox in snow"}))
    assert again.changed_paths == ["projects/demo/images/a-red-fox-in-snow-3.png"]
    preview = media_adapter._image_preview(ctx, {"prompt": "A red fox"})
    assert "Prompt: A red fox" in preview and "Engine:" in preview
    for bad in ({"prompt": ""}, {"prompt": "x", "count": 9}, {"prompt": "x", "width": 5000},
                {"prompt": "x", "path": "projects/demo/a.jpg"}, {"prompt": "x", "path": "../out.png"}):
        with pytest.raises((ToolError, PathDenied)):
            media_adapter._image_check(ctx, bad)


def test_generate_image_through_the_agent(ctx, run_chat, fake_engine, settings):
    ctx.ollama.script = [[("generate_image", {"prompt": "A lighthouse"})], "Here it is."]
    events = run_chat("draw a lighthouse", active_path="projects/demo")
    approval = of_type(events, "approval_required")[0]
    assert "A lighthouse" in approval["preview"]
    result = of_type(events, "tool_result")[0]
    assert result["status"] == "success" and result["changed_paths"] == ["projects/demo/images/a-lighthouse.png"]
    assert (settings.workspace_root / "projects/demo/images/a-lighthouse.png").is_file()


def test_unset_engine_explains_how_to_set_up(settings, monkeypatch):
    monkeypatch.setattr(hardware, "packages", lambda: dict.fromkeys(["torch", "diffusers", "transformers", "accelerate", "imageio", "imageio-ffmpeg"], False))
    engine = media.MediaEngine(settings)

    async def nothing(self):
        return []

    monkeypatch.setattr(media.MediaEngine, "ollama_models", nothing)
    with pytest.raises(media.MediaError, match="Settings > Media & GPU"):
        asyncio.run(engine.generate(media.ImageRequest("a cat")))


def test_image_api_engine(settings, monkeypatch):
    from core.models import ModelStore

    store = ModelStore(settings.data_dir / "models.json")
    store.save({"gguf": [], "providers": [{"id": "img", "name": "Images", "base_url": "https://api.example.com/v1",
                                           "api_key": "sk-test", "models": ["m"], "native_tools": True}]})
    hardware.config(settings).save({"image_engine": "api", "api_provider": "img", "api_image_model": "pic-1"})
    seen = []

    class Fake(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            def handler(request):
                seen.append(request)
                if request.url.path.endswith("/api/tags"):
                    return httpx.Response(200, json={"models": []})
                import base64
                return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png("blue")).decode()}]})
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Fake)
    data, how = asyncio.run(media.MediaEngine(settings).generate(media.ImageRequest("a boat", width=500, height=500)))
    assert Image.open(io.BytesIO(data)).getpixel((0, 0)) == (0, 0, 255)
    request = seen[-1]
    assert request.headers["authorization"] == "Bearer sk-test"
    assert json.loads(request.content) == {"model": "pic-1", "prompt": "a boat", "n": 1, "size": "512x512"}
    assert "Images" in how


# ---- video


@pytest.mark.parametrize("mp4", [False, True])
def test_build_video(tmp_path, monkeypatch, mp4):
    if mp4 and not media.mp4_available():
        pytest.skip("imageio-ffmpeg is not installed")
    monkeypatch.setattr(media, "mp4_available", lambda: mp4)
    frames = [Image.new("RGB", (320, 180), c) for c in ("red", "green", "blue")]
    written = media.build_video(frames, tmp_path / "clip.mp4", seconds_per_image=1, fps=8, captions=["One", "Two"])
    assert written.suffix == (".mp4" if mp4 else ".gif") and written.stat().st_size > 1000
    if not mp4:
        with Image.open(written) as gif:
            assert gif.n_frames >= 3


def test_create_video_tool(settings, fake_engine):
    ctx = tool_ctx(settings)
    still = settings.workspace_root / "projects/demo/photo.png"
    still.write_bytes(png("green", (300, 200)))
    result = asyncio.run(media_adapter.create_video(ctx, {
        "scenes": ["a sunrise", "a sunset"], "images": ["projects/demo/photo.png"], "style": "watercolor",
        "title": "My day", "seconds_per_image": 1, "fps": 6, "keep_images": True}))
    video = result.changed_paths[0]
    assert video.startswith("projects/demo/videos/my-day.") and len(result.changed_paths) == 3
    assert [r.prompt for r in fake_engine] == ["a sunrise, watercolor", "a sunset, watercolor"]
    assert (settings.workspace_root / video).stat().st_size > 1000
    assert "Generate 2 scene image(s)" in media_adapter._video_preview(ctx, {"scenes": ["a", "b"]})
    for bad in ({}, {"images": ["projects/demo/hello.py"]}, {"scenes": ["x"] * 13}, {"scenes": ["x"], "seconds_per_image": 0}):
        with pytest.raises((ToolError, PathDenied)):
            media_adapter._video_check(ctx, bad)


# ---- endpoints


def test_media_endpoints(client, settings, monkeypatch):
    data = client.get("/api/media").json()
    assert data["hardware"]["gpu"]["name"] == "Test GPU" and data["gpu_in_use"] is True
    assert set(data["engines"]) == {"diffusers", "ollama", "api"} and data["video"]["gif"] is True
    saved = client.post("/api/media/settings", json={"use_gpu": False, "image_engine": "diffusers"}).json()
    assert saved["use_gpu"] is False and client.get("/api/media").json()["gpu_in_use"] is False
    assert client.post("/api/media/settings", json={"image_engine": "nope"}).status_code == 400


def test_media_test_endpoint(client, fake_engine):
    made = client.post("/api/media/test", json={"prompt": "a cat"}).json()
    assert made["image"].startswith("data:image/png;base64,") and made["how"] == "test engine"


def test_media_files_are_served_for_previews(client, settings):
    folder = settings.workspace_root / "projects/demo/images"
    folder.mkdir(parents=True)
    (folder / "cat.png").write_bytes(png())
    query = {"session_id": "testsession01", "path": "images/cat.png", "base": "projects/demo"}
    response = client.get("/api/files/media", params=query)
    assert response.status_code == 200 and response.headers["content-type"] == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/files/media", params={**query, "path": "hello.py"}).status_code == 404
    assert client.get("/api/files/media", params={**query, "path": "../../../outside.txt"}).status_code == 404


def test_list_arguments_sent_as_json_text_are_parsed():
    tool = media_adapter.adapter.tools[1]
    args = tool.clean_arguments({"images": '["a.png", "b.png"]', "captions": "[\"One\"]", "fps": "24", "motion": "false"})
    assert args == {"images": ["a.png", "b.png"], "captions": ["One"], "fps": 24, "motion": False}
    assert tool.clean_arguments({"images": "a.png"})["images"] == ["a.png"]


@pytest.mark.parametrize("message", [
    "Generate an image of a cozy wooden cabin in a snowy forest at night",
    "create an html landing page for my bakery",
    "Make a slideshow video titled Circles from slide1.png and slide2.png",
])
def test_everyday_requests_stay_with_the_general_assistant(settings, message):
    from core.arch_loader import load_registry
    from core.routing import route_by_intent

    skill, _ = route_by_intent(message, load_registry(settings.arch_dir))
    assert skill is None  # Not a read-only specialist, and never the agent manager.
