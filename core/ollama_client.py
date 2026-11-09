"""Talks to the local Ollama server and normalizes tool calls across models.

Models differ. Some return structured tool_calls, and some (qwen2.5-coder, for
example) advertise tools but write the call as JSON text. Both end up as the same
ToolCall list here. Text calls are only accepted for names the run actually offers.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import AsyncIterator, Awaitable, Callable

import httpx

from .config import Settings

TAGGED_CALL = re.compile(r"<tool_call>\s*(.*?)\s*(?:</tool_call>|\Z)", re.S)
FENCED_BLOCK = re.compile(r"```(?:json|tool_call|tool)?\s*\n?(.*?)```", re.S)
HOLD_PREFIXES = ("{", "[", "<tool_call", "```json", "```tool")


class OllamaError(Exception):
    pass


@dataclass
class ToolCall:
    name: str
    arguments: dict


@dataclass
class ChatTurn:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    done_reason: str = ""
    thinking: str = ""  # Reasoning from "thinking" models (qwen3, deepseek-r1...); not shown, but kept as a fallback.


def parse_text_tool_calls(text: str, allowed: set[str]) -> tuple[str, list[ToolCall]]:
    """Finds tool calls written as text. Returns (remaining prose, calls)."""
    calls: list[ToolCall] = []

    def collect(blob: str) -> bool:
        found = _calls_from_json(blob, allowed)
        calls.extend(found)
        return bool(found)

    def strip_tagged(match: re.Match) -> str:
        return "" if collect(match.group(1)) else match.group(0)

    remaining = TAGGED_CALL.sub(strip_tagged, text)

    def strip_fenced(match: re.Match) -> str:
        return "" if collect(match.group(1)) else match.group(0)

    remaining = FENCED_BLOCK.sub(strip_fenced, remaining)

    if not calls:
        stripped = remaining.strip()
        if stripped[:1] in "{[" and collect(stripped):
            remaining = ""
        else:
            remaining = _strip_embedded_objects(remaining, allowed, calls)
    if not calls:
        remaining = _strip_named_calls(remaining, allowed, calls)
    return remaining.strip(), calls


def _strip_named_calls(text: str, allowed: set[str], calls: list[ToolCall]) -> str:
    """Handles a tool name followed by its arguments on its own line: read_file {"path": "a"}."""
    if not allowed:
        return text
    names = "|".join(re.escape(name) for name in sorted(allowed, key=len, reverse=True))
    pattern = re.compile(rf"^[ \t`]*({names})[ \t`]*\(?[ \t]*(?=\{{)", re.M)
    decoder = json.JSONDecoder()
    output: list[str] = []
    index = 0
    for match in pattern.finditer(text):
        if match.start() < index:
            continue
        try:
            arguments, end = decoder.raw_decode(text, match.end())
        except json.JSONDecodeError:
            continue
        calls.append(ToolCall(name=match.group(1), arguments=arguments))
        output.append(text[index:match.start()])
        index = end
        trailing = re.match(r"[ \t]*\)?`*", text[index:])
        index += trailing.end() if trailing else 0
    output.append(text[index:])
    return "".join(output)


def _strip_embedded_objects(text: str, allowed: set[str], calls: list[ToolCall]) -> str:
    """Handles prose around bare JSON, e.g. 'Listing now: {"name": ...}'."""
    decoder = json.JSONDecoder()
    output: list[str] = []
    index = 0
    while index < len(text):
        start = text.find("{", index)
        if start < 0:
            output.append(text[index:])
            break
        output.append(text[index:start])
        try:
            value, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            output.append("{")
            index = start + 1
            continue
        found = _calls_from_value(value, allowed)
        if found:
            calls.extend(found)
        else:
            output.append(text[start:end])
        index = end
    return "".join(output)


def _calls_from_json(blob: str, allowed: set[str]) -> list[ToolCall]:
    blob = blob.strip()
    try:
        return _calls_from_value(json.loads(blob), allowed)
    except json.JSONDecodeError:
        pass
    # Several objects on separate lines.
    calls: list[ToolCall] = []
    for line in blob.splitlines():
        try:
            calls.extend(_calls_from_value(json.loads(line), allowed))
        except json.JSONDecodeError:
            return []
    return calls


def _calls_from_value(value: object, allowed: set[str]) -> list[ToolCall]:
    if isinstance(value, list):
        calls: list[ToolCall] = []
        for item in value:
            calls.extend(_calls_from_value(item, allowed))
        return calls
    if not isinstance(value, dict):
        return []
    if isinstance(value.get("function"), dict):
        value = value["function"]
    name = value.get("name")
    arguments = value.get("arguments", value.get("parameters", {}))
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return []
    if isinstance(name, str) and name in allowed and isinstance(arguments, dict):
        return [ToolCall(name=name, arguments=arguments)]
    return []


class TurnBuilder:
    """Assembles one streamed model turn, for any backend.

    Text that might be the start of a tool call is held back from the UI; anything else is forwarded as it arrives.
    At the end, tool calls written as text are recognised for models without native tool calling.
    """

    def __init__(self, on_token: Callable[[str], Awaitable[None]]):
        self.turn = ChatTurn()
        self.on_token = on_token
        self.forwarded = 0
        self.holding = True

    def add_call(self, name: str, arguments: object) -> None:
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError:
                arguments = {}
        self.turn.tool_calls.append(ToolCall(name=name, arguments=arguments if isinstance(arguments, dict) else {}))

    async def feed(self, piece: str) -> None:
        if not piece:
            return
        turn = self.turn
        turn.content += piece
        if self.holding:
            head = turn.content.lstrip()
            if not head:
                return
            if any(head.startswith(prefix) or prefix.startswith(head) for prefix in HOLD_PREFIXES):
                return
            self.holding = False
        # Stop forwarding at anything that may begin a call mid-message.
        visible = turn.content
        marker = min(
            (i for i in (visible.find("<tool_call"), visible.find("```json"), visible.find('{"name"')) if i >= 0),
            default=-1,
        )
        if marker >= 0:
            visible = visible[:marker]
        if len(visible) > self.forwarded:
            await self.on_token(visible[self.forwarded:])
            self.forwarded = len(visible)

    def finish(self, allowed_tools: set[str]) -> ChatTurn:
        turn = self.turn
        if not turn.tool_calls and allowed_tools:
            prose, calls = parse_text_tool_calls(turn.content, allowed_tools)
            if calls:
                turn.content, turn.tool_calls = prose, calls
        return turn


class OllamaClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._capabilities: dict[str, list[str]] = {}

    async def list_models(self) -> list[str]:
        try:
            async with httpx.AsyncClient(base_url=self.settings.ollama_url, timeout=5) as client:
                response = await client.get("/api/tags")
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OllamaError(f"Ollama is not reachable at {self.settings.ollama_url}: {exc}") from exc
        return [
            model["name"]
            for model in response.json().get("models", [])
            # Skip embedding-only and cloud-proxied models.
            if "embedding" not in model["name"] and not model["name"].endswith(("-cloud", ":cloud"))
        ]

    async def supports_tools(self, model: str) -> bool:
        if model not in self._capabilities:
            try:
                async with httpx.AsyncClient(base_url=self.settings.ollama_url, timeout=10) as client:
                    response = await client.post("/api/show", json={"model": model})
                    response.raise_for_status()
                self._capabilities[model] = response.json().get("capabilities") or []
            except httpx.HTTPError:
                return False
        return "tools" in self._capabilities[model]

    async def chat(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict] | None,
        allowed_tools: set[str],
        on_token: Callable[[str], Awaitable[None]],
    ) -> ChatTurn:
        """Streams one model turn. Text that might be a tool call is held back from the UI."""
        builder = TurnBuilder(on_token)
        async for chunk in self._stream(model, messages, tools):
            message = chunk.get("message") or {}
            for raw in message.get("tool_calls") or []:
                function = raw.get("function") or {}
                builder.add_call(function.get("name", ""), function.get("arguments") or {})
            builder.turn.thinking += message.get("thinking") or ""
            await builder.feed(message.get("content") or "")
            if chunk.get("done"):
                builder.turn.done_reason = chunk.get("done_reason", "")
        return builder.finish(allowed_tools)

    async def _stream(self, model: str, messages: list[dict], tools: list[dict] | None) -> AsyncIterator[dict]:
        body: dict = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {"num_ctx": self.settings.num_ctx},
        }
        if tools:
            body["tools"] = tools
        timeout = httpx.Timeout(connect=5, read=600, write=30, pool=5)
        try:
            async with httpx.AsyncClient(base_url=self.settings.ollama_url, timeout=timeout) as client:
                async with client.stream("POST", "/api/chat", json=body) as response:
                    if response.status_code != 200:
                        detail = (await response.aread()).decode("utf-8", errors="replace")[:300]
                        raise OllamaError(f"Ollama returned {response.status_code}: {detail}")
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        chunk = json.loads(line)
                        if chunk.get("error"):
                            raise OllamaError(str(chunk["error"]))
                        yield chunk
        except httpx.HTTPError as exc:
            raise OllamaError(f"Lost connection to Ollama: {exc}") from exc
