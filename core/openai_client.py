"""Client for OpenAI-compatible chat APIs: the local llama.cpp server that runs .gguf files, and hosted providers
(OpenAI, Anthropic, Gemini, Groq, OpenRouter, Mistral, DeepSeek, LM Studio, vLLM...).

The agent keeps conversation history in Ollama's shape (tool calls without ids, tool results named by tool).
OpenAI-style APIs need every tool result to reference the id of the call it answers, so ids are assigned here.
"""

from __future__ import annotations

import json
from typing import AsyncIterator, Awaitable, Callable

import httpx

from .ollama_client import ChatTurn, OllamaError, TurnBuilder


class ModelError(OllamaError):
    """A model backend failed. Subclasses OllamaError so the agent's existing error handling applies."""


def to_openai_messages(messages: list[dict]) -> list[dict]:
    """Converts the agent's history to OpenAI's format, pairing tool results with call ids in order."""
    converted: list[dict] = []
    pending: list[str] = []  # Ids of calls awaiting a result, oldest first.
    counter = 0
    for message in messages:
        role = message.get("role")
        if role == "assistant" and message.get("tool_calls"):
            calls = []
            for call in message["tool_calls"]:
                function = call.get("function") or {}
                counter += 1
                call_id = call.get("id") or f"call_{counter}"
                pending.append(call_id)
                arguments = function.get("arguments", {})
                calls.append({"id": call_id, "type": "function", "function": {
                    "name": function.get("name", ""),
                    "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments),
                }})
            converted.append({"role": "assistant", "content": message.get("content") or None, "tool_calls": calls})
        elif role == "tool":
            if pending:
                converted.append({"role": "tool", "tool_call_id": pending.pop(0), "content": message.get("content", "")})
            else:  # A result without a call (should not happen): keep the information as plain text.
                converted.append({"role": "user", "content": f"Tool result:\n{message.get('content', '')}"})
        else:
            converted.append({"role": role, "content": message.get("content", "")})
    return converted


class OpenAICompatibleClient:
    """One endpoint, e.g. https://api.openai.com/v1 or http://127.0.0.1:PORT/v1."""

    def __init__(self, base_url: str, api_key: str = "", label: str = "model server", timeout: float = 600,
                 extra_headers: dict | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.label = label
        self.timeout = timeout
        self.extra_headers = extra_headers or {}

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json", **self.extra_headers}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _describe(self, response: httpx.Response, body: str) -> str:
        detail = body[:300]
        try:
            data = json.loads(body)
            error = data.get("error")
            detail = (error.get("message") if isinstance(error, dict) else error) or data.get("detail") or detail
        except (json.JSONDecodeError, AttributeError):
            pass
        if response.status_code in (401, 403):
            return f"{self.label} rejected the API key ({response.status_code}): {detail}"
        return f"{self.label} returned {response.status_code}: {detail}"

    async def list_models(self) -> list[str]:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(f"{self.base_url}/models", headers=self._headers())
        except httpx.HTTPError as exc:
            raise ModelError(f"{self.label} is not reachable: {exc}") from exc
        if response.status_code != 200:
            raise ModelError(self._describe(response, response.text))
        data = response.json()
        items = data.get("data", data.get("models", [])) if isinstance(data, dict) else data
        return sorted({str(item.get("id") or item.get("name")) for item in items if isinstance(item, dict)} - {"None"})

    async def chat(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict] | None,
        allowed_tools: set[str],
        on_token: Callable[[str], Awaitable[None]],
        options: dict | None = None,
    ) -> ChatTurn:
        builder = TurnBuilder(on_token)
        partial: dict[int, dict] = {}  # Streamed tool calls arrive in pieces, keyed by index.
        async for chunk in self._stream(model, messages, tools, options or {}):
            for choice in chunk.get("choices") or []:
                delta = choice.get("delta") or choice.get("message") or {}
                for raw in delta.get("tool_calls") or []:
                    slot = partial.setdefault(raw.get("index", len(partial)), {"name": "", "arguments": ""})
                    function = raw.get("function") or {}
                    slot["name"] += function.get("name") or ""
                    arguments = function.get("arguments")
                    if isinstance(arguments, dict):
                        slot["arguments"] = json.dumps(arguments)
                    elif arguments:
                        slot["arguments"] += arguments
                await builder.feed(delta.get("content") or "")
                if choice.get("finish_reason"):
                    builder.turn.done_reason = choice["finish_reason"]
        for index in sorted(partial):
            if partial[index]["name"]:
                builder.add_call(partial[index]["name"], partial[index]["arguments"])
        return builder.finish(allowed_tools)

    async def _stream(self, model: str, messages: list[dict], tools: list[dict] | None, options: dict) -> AsyncIterator[dict]:
        body: dict = {"model": model, "messages": to_openai_messages(messages), "stream": True, **options}
        if tools:
            body["tools"] = tools
        timeout = httpx.Timeout(connect=10, read=self.timeout, write=30, pool=10)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", f"{self.base_url}/chat/completions", json=body, headers=self._headers()) as response:
                    if response.status_code != 200:
                        raise ModelError(self._describe(response, (await response.aread()).decode("utf-8", errors="replace")))
                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if chunk.get("error"):
                            error = chunk["error"]
                            raise ModelError(f"{self.label}: {error.get('message') if isinstance(error, dict) else error}")
                        yield chunk
        except httpx.HTTPError as exc:
            raise ModelError(f"Lost connection to {self.label}: {exc}") from exc
