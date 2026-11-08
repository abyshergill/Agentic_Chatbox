"""Model backends: saved settings, URL rules, OpenAI-compatible streaming, routing, and the Models endpoints."""

import asyncio
import json
import stat

import httpx
import pytest

from core import openai_client
from core.config import Settings
from core.models import ModelRouter, ModelStore, check_base_url, check_gguf, mask
from core.openai_client import OpenAICompatibleClient, to_openai_messages


class FakeOllamaClient:
    def __init__(self, models=None, error=None):
        self.models, self.error = models or [], error

    async def list_models(self):
        if self.error:
            from core.ollama_client import OllamaError
            raise OllamaError(self.error)
        return self.models

    async def supports_tools(self, model):
        return True


@pytest.fixture
def router(tmp_path):
    return ModelRouter(Settings(base_dir=tmp_path), FakeOllamaClient(["qwen:3b"]))


def gguf(tmp_path, name="m.gguf"):
    path = tmp_path / name
    path.write_bytes(b"GGUF" + b"\0" * 32)
    return path


def test_url_rules():
    assert check_base_url("https://api.openai.com/v1/") == "https://api.openai.com/v1"
    assert check_base_url("http://127.0.0.1:1234/v1") == "http://127.0.0.1:1234/v1"
    for bad in ["http://api.example.com/v1", "ftp://x", "not a url", "https:///v1"]:
        with pytest.raises(ValueError):
            check_base_url(bad)


def test_gguf_validation(tmp_path):
    assert check_gguf(str(gguf(tmp_path))).name == "m.gguf"
    (tmp_path / "fake.gguf").write_bytes(b"nope")
    for bad in [tmp_path / "fake.gguf", tmp_path / "missing.gguf", tmp_path / "m.bin"]:
        with pytest.raises(ValueError):
            check_gguf(str(bad))


def test_store_keeps_keys_private(tmp_path):
    store = ModelStore(tmp_path / "data" / "models.json")
    provider = store.upsert_provider("Open AI", "https://api.openai.com/v1", "sk-secret-1234", ["gpt-a", "gpt-b"], True)
    assert provider["id"] == "open-ai"
    assert stat.S_IMODE((tmp_path / "data" / "models.json").stat().st_mode) == 0o600
    # Saving again without a key keeps the old one.
    store.upsert_provider("Open AI", "https://api.openai.com/v1", "", ["gpt-c"], True, provider_id="open-ai")
    assert store.provider("open-ai")["api_key"] == "sk-secret-1234" and store.provider("open-ai")["models"] == ["gpt-c"]
    assert mask("sk-secret-1234") == "••••••1234"
    with pytest.raises(ValueError, match="API key"):
        store.upsert_provider("Other", "https://api.other.com/v1", "", ["m"], True)
    store.upsert_provider("LM Studio", "http://127.0.0.1:1234/v1", "", ["local-model"], False)  # Local needs no key.
    with pytest.raises(ValueError, match="spaces"):
        store.upsert_provider("X", "http://127.0.0.1:1/v1", "", ["has space"], True)
    assert store.remove_provider("open-ai") and not store.remove_provider("open-ai")


def test_history_is_converted_with_call_ids():
    history = [
        {"role": "system", "content": "s"},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "a"}}},
                                                            {"function": {"name": "list_directory", "arguments": {}}}]},
        {"role": "tool", "tool_name": "read_file", "content": "A"},
        {"role": "tool", "tool_name": "list_directory", "content": "B"},
    ]
    converted = to_openai_messages(history)
    calls = converted[1]["tool_calls"]
    assert calls[0]["function"]["arguments"] == '{"path": "a"}'
    assert [m["tool_call_id"] for m in converted[2:]] == [calls[0]["id"], calls[1]["id"]]


def sse(*chunks):
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def mock_api(monkeypatch, handler):
    real = httpx.AsyncClient

    class Patched(real):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(openai_client.httpx, "AsyncClient", Patched)


def test_streaming_text_and_tool_calls(monkeypatch):
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        body = sse(
            {"choices": [{"delta": {"content": "Let me look."}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "read_", "arguments": '{"pa'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "file", "arguments": 'th": "x.py"}'}}]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        )
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    mock_api(monkeypatch, handler)
    tokens = []

    async def on_token(t):
        tokens.append(t)

    client = OpenAICompatibleClient("https://api.example.com/v1", "sk-test", label="Example")
    turn = asyncio.run(client.chat("m", [{"role": "user", "content": "hi"}], [{"type": "function"}], {"read_file"}, on_token))
    assert turn.tool_calls[0].name == "read_file" and turn.tool_calls[0].arguments == {"path": "x.py"}
    assert "".join(tokens) == "Let me look." and turn.done_reason == "tool_calls"
    assert seen["auth"] == "Bearer sk-test" and seen["body"]["stream"] and seen["body"]["tools"]


def test_api_errors_are_explained(monkeypatch):
    mock_api(monkeypatch, lambda request: httpx.Response(401, json={"error": {"message": "Incorrect API key"}}))
    client = OpenAICompatibleClient("https://api.example.com/v1", "bad", label="Example")

    async def noop(_):
        pass

    with pytest.raises(openai_client.ModelError, match="rejected the API key.*Incorrect API key"):
        asyncio.run(client.chat("m", [], None, set(), noop))


def test_router_catalog_and_routing(router, tmp_path, monkeypatch):
    router.store.add_gguf(str(gguf(tmp_path)))
    (tmp_path / "models").mkdir()
    gguf(tmp_path / "models", "dropped.gguf")
    router.store.upsert_provider("Groq", "https://api.groq.com/openai/v1", "k", ["llama-x"], True)
    router.store.upsert_provider("LM Studio", "http://127.0.0.1:1234/v1", "", ["local"], False)
    ids = asyncio.run(router.list_models())
    assert ids == ["qwen:3b", "gguf:m.gguf", "gguf:dropped.gguf", "api:groq:llama-x", "api:lm-studio:local"]
    assert asyncio.run(router.is_remote("api:groq:llama-x")) and not asyncio.run(router.is_remote("api:lm-studio:local"))
    assert not asyncio.run(router.is_remote("gguf:m.gguf")) and not asyncio.run(router.is_remote("qwen:3b"))
    assert asyncio.run(router.supports_tools("api:groq:llama-x")) and not asyncio.run(router.supports_tools("api:lm-studio:local"))

    captured = {}

    async def fake_chat(self, model, messages, tools, allowed, on_token, options=None):
        captured.update(base=self.base_url, key=self.api_key, model=model)
        from core.ollama_client import ChatTurn
        return ChatTurn(content="ok")

    monkeypatch.setattr(OpenAICompatibleClient, "chat", fake_chat)
    asyncio.run(router.chat("api:groq:llama-x", [], None, set(), None))
    assert captured == {"base": "https://api.groq.com/openai/v1", "key": "k", "model": "llama-x"}
    with pytest.raises(openai_client.ModelError, match="no longer configured"):
        asyncio.run(router.chat("api:gone:x", [], None, set(), None))


def test_no_models_anywhere_explains_options(tmp_path):
    router = ModelRouter(Settings(base_dir=tmp_path), FakeOllamaClient(error="Ollama is not reachable"))
    from core.ollama_client import OllamaError
    with pytest.raises(OllamaError, match="add a .gguf file"):
        asyncio.run(router.list_models())


def test_automatic_choice_never_picks_a_remote_model(ctx, run_chat, settings):
    from tests.conftest import FakeOllama, of_type

    class RemoteOnly(FakeOllama):
        async def is_remote(self, model):
            return model.startswith("api:")

    fake = RemoteOnly()
    fake.models = ["api:openai:gpt-x"]
    ctx.llm = fake
    events = run_chat("hello")
    assert "No local model" in of_type(events, "error")[0]["message"] and not fake.calls
    fake.script = ["hi"]
    run_chat("hello", model="api:openai:gpt-x")  # Chosen explicitly: allowed.
    assert fake.calls and fake.calls[0]["model"] == "api:openai:gpt-x"


def test_models_endpoints(client, ctx, settings, tmp_path):
    ctx.llm = ModelRouter(settings, FakeOllamaClient(["qwen:3b"]))
    assert client.get("/api/models").json()["providers"] == []
    saved = client.post("/api/models/providers", json={"name": "OpenAI", "base_url": "https://api.openai.com/v1",
                                                       "api_key": "sk-abcdefgh1234", "models": ["gpt-x"]})
    assert saved.status_code == 200
    provider = saved.json()["providers"][0]
    assert provider["api_key"] == "••••••1234" and provider["remote"] and "sk-abcdefgh1234" not in saved.text
    assert client.post("/api/models/providers", json={"name": "Bad", "base_url": "http://evil.example.com/v1",
                                                      "api_key": "k", "models": ["m"]}).status_code == 400
    added = client.post("/api/models/gguf", json={"path": str(gguf(tmp_path))})
    assert added.status_code == 200 and added.json()["gguf"][0]["name"] == "m.gguf"
    assert client.post("/api/models/gguf", json={"path": "/etc/passwd"}).status_code == 400
    catalog = client.get("/api/health").json()["catalog"]
    assert {(m["id"], m["remote"]) for m in catalog} == {("qwen:3b", False), ("gguf:m.gguf", False), ("api:openai:gpt-x", True)}
    assert client.delete("/api/models/providers/openai").status_code == 200
    assert client.post("/api/models/gguf/remove", json={"path": str((tmp_path / "m.gguf").resolve())}).json()["gguf"] == []
    # Cross-site pages cannot reach these endpoints.
    assert client.post("/api/models/gguf", json={"path": "x"}, headers={"origin": "https://evil.example"}).status_code == 403
