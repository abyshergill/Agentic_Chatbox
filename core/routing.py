"""Works out what a chat message is asking for: /skill, #application, or plain language."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .arch_loader import Registry, Skill

COMMAND = re.compile(r"^\s*([/#])([A-Za-z][\w-]*)(?:\s+(.*))?\s*$", re.S)
WORD = re.compile(r"[a-z][a-z0-9+#]+")
STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "into", "code", "writes", "write", "handles", "use",
    "using", "make", "general", "tasks", "task", "files", "file", "project", "projects", "user", "please", "can",
    "you", "your", "changes", "server", "create", "creates", "generate", "new",
    # Short function words, which otherwise add up to a match ("an image of a cabin in the woods").
    "an", "in", "of", "to", "on", "at", "by", "or", "as", "is", "it", "be", "me", "my", "we", "us", "do", "so", "if",
    "up", "no", "are", "was", "its", "all", "any", "how", "what", "who", "why", "not", "but", "out", "about", "some",
    "want", "need", "like", "give", "show", "get", "let", "would", "could", "should", "page", "pages",
}
# Agents that only start when the user names their skill (system.md: "Only the user can start it").
EXPLICIT_ONLY_AGENTS = {"agent_manager"}
MIN_ROUTE_SCORE = 3


@dataclass
class ParsedInput:
    kind: str  # "skill", "app", or "plain"
    name: str
    text: str


def parse_input(message: str) -> ParsedInput:
    match = COMMAND.match(message)
    if match:
        prefix, name, text = match.groups()
        return ParsedInput("skill" if prefix == "/" else "app", name, (text or "").strip())
    return ParsedInput("plain", "", message.strip())


def _words(text: str) -> set[str]:
    return {word for word in WORD.findall(text.lower()) if word not in STOPWORDS}


def route_by_intent(message: str, registry: Registry) -> tuple[Skill | None, list[str]]:
    """Picks the best skill by matching words, or returns None for the default skill.

    This is deliberate: it is instant, costs no model call, and the reason can be
    shown to the user. A title match counts for more than a description match.
    """
    wanted = _words(message)
    best: Skill | None = None
    best_score = 0
    best_terms: list[str] = []
    for skill in registry.valid_skills():
        if skill.command == registry.system.default_skill or skill.agent_id in EXPLICIT_ONLY_AGENTS:
            continue
        title = wanted & _words(skill.title)
        description = (wanted & _words(skill.description)) - title
        score = 3 * len(title) + len(description)
        if score > best_score:
            best, best_score, best_terms = skill, score, sorted(title | description)
    if best_score < MIN_ROUTE_SCORE:
        return None, []
    return best, best_terms
