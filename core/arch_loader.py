"""Discovers agents, skills, and commands from the Markdown files in arch/.

Nothing here is hardcoded: adding a folder under arch/agents/ and a line to
command.md is enough to add a skill. Files are re-read on each request, so edits
apply without a restart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

SKILL_FIELDS = ("Title", "Description", "Allowed Paths", "Model")
COMMAND_LINE = re.compile(r"^(/[A-Za-z0-9][\w-]*)\s*->\s*(\S+\.md)\s*$")
AGENT_LINE = re.compile(r"^-\s*([\w-]+)\s*:\s*(.+)$")
HEADER_LINE = re.compile(r"^([A-Za-z][A-Za-z ]*):\s*(.*)$")


@dataclass
class Skill:
    id: str
    agent_id: str
    path: Path
    title: str = ""
    description: str = ""
    allowed_paths: list[str] = field(default_factory=list)
    model: str = ""
    prompt: str = ""
    errors: list[str] = field(default_factory=list)
    command: str | None = None

    @property
    def valid(self) -> bool:
        return not self.errors


@dataclass
class Agent:
    id: str
    name: str = ""
    description: str = ""
    capabilities: list[str] = field(default_factory=list)
    applications: list[str] = field(default_factory=list)
    read_only: bool = False  # 'Read Only: yes' in agent.md: only tools that look, never change.
    prompt: str = ""
    skills: list[Skill] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class SystemConfig:
    name: str = "Local AI Workspace"
    default_skill: str = ""
    max_iterations: int = 12
    prompt: str = ""
    listed_agents: dict[str, str] = field(default_factory=dict)


@dataclass
class Registry:
    system: SystemConfig
    agents: dict[str, Agent]
    commands: dict[str, Skill]
    warnings: list[str]

    def skill_for(self, command: str) -> Skill | None:
        return self.commands.get(command)

    @property
    def default_skill(self) -> Skill | None:
        return self.commands.get(self.system.default_skill)

    def valid_skills(self) -> list[Skill]:
        return [skill for skill in self.commands.values() if skill.valid]


def parse_skill(path: Path, agent_id: str) -> Skill:
    """Parses a skill file: Title, Description and Allowed Paths on the first three lines, in that order.

    A fourth 'Model:' line is optional. Without it the skill runs on the model picked in the chat pane,
    or on an installed model chosen automatically.
    """
    skill = Skill(id=path.stem, agent_id=agent_id, path=path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        skill.errors.append(f"cannot read file: {exc}")
        return skill

    has_model = len(lines) > 3 and lines[3].startswith("Model:")
    fields = SKILL_FIELDS if has_model else SKILL_FIELDS[:3]
    values: list[str] = []
    for index, name in enumerate(fields):
        line = lines[index] if index < len(lines) else ""
        prefix = f"{name}:"
        if not line.startswith(prefix):
            skill.errors.append(f"line {index + 1} must start with '{prefix}'")
            values.append("")
            continue
        value = line[len(prefix):].strip()
        if not value:
            skill.errors.append(f"line {index + 1}: {name} is empty")
        values.append(value)

    skill.title, skill.description = values[0], values[1]
    skill.allowed_paths = [part.strip() for part in values[2].split(",") if part.strip()]
    skill.model = values[3] if has_model else ""
    skill.prompt = "\n".join(lines[len(fields):]).strip()

    if values[2] and not skill.allowed_paths:
        skill.errors.append("line 3: Allowed Paths has no usable path")
    if skill.model.endswith(("-cloud", ":cloud")):
        skill.errors.append("line 4: cloud models are not allowed; this workspace is local-only")
    if not skill.prompt:
        skill.errors.append(f"line {len(fields) + 1} onward: system instructions are missing")
    return skill


def parse_agent(directory: Path) -> Agent:
    agent = Agent(id=directory.name, name=directory.name)
    path = directory / "agent.md"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        agent.errors.append(f"cannot read agent.md: {exc}")
        return agent

    headers, body = _split_headers(lines)
    agent.name = headers.get("Name", directory.name)
    agent.description = headers.get("Description", "")
    agent.capabilities = _csv(headers.get("Capabilities", ""))
    agent.applications = [app.lstrip("#") for app in _csv(headers.get("Applications", ""))]
    agent.read_only = headers.get("Read Only", "").strip().lower() in {"yes", "true"}
    agent.prompt = body
    return agent


def parse_system(path: Path, warnings: list[str]) -> SystemConfig:
    config = SystemConfig()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        warnings.append(f"system.md: {exc}")
        return config

    headers, body = _split_headers(lines)
    config.name = headers.get("Name", config.name)
    config.default_skill = headers.get("Default Skill", "")
    try:
        config.max_iterations = max(1, min(50, int(headers.get("Max Tool Iterations", "12"))))
    except ValueError:
        warnings.append("system.md: Max Tool Iterations is not a number; using 12")

    in_agents = False
    kept: list[str] = []
    for line in body.splitlines():
        if line.startswith("## "):
            in_agents = line[3:].strip().lower() == "available agents"
            if in_agents:
                continue
        if in_agents:
            match = AGENT_LINE.match(line.strip())
            if match:
                config.listed_agents[match.group(1)] = match.group(2).strip()
            continue
        kept.append(line)
    # The agent list in the prompt is generated from what was really discovered.
    config.prompt = "\n".join(kept).strip()
    return config


def load_registry(arch_dir: Path) -> Registry:
    warnings: list[str] = []
    system = parse_system(arch_dir / "system.md", warnings)

    agents: dict[str, Agent] = {}
    skills_by_path: dict[Path, Skill] = {}
    agents_dir = arch_dir / "agents"
    if agents_dir.is_dir():
        for directory in sorted(p for p in agents_dir.iterdir() if p.is_dir()):
            agent = parse_agent(directory)
            for error in agent.errors:
                warnings.append(f"agent {directory.name}: {error}")
            skills_dir = directory / "skills"
            if skills_dir.is_dir():
                for file in sorted(skills_dir.glob("*.md")):
                    skill = parse_skill(file, agent.id)
                    agent.skills.append(skill)
                    skills_by_path[file.resolve()] = skill
                    for error in skill.errors:
                        warnings.append(f"skill {agent.id}/{file.name}: {error}")
            agents[agent.id] = agent

    for listed in system.listed_agents:
        if listed not in agents:
            warnings.append(f"system.md lists agent '{listed}', but arch/agents/{listed}/ does not exist")
    for found in agents:
        if system.listed_agents and found not in system.listed_agents:
            warnings.append(f"agent '{found}' is loaded but not listed in system.md")

    commands = _parse_commands(arch_dir, skills_by_path, warnings)
    if system.default_skill and system.default_skill not in commands:
        warnings.append(f"system.md: Default Skill {system.default_skill} is not in command.md")
    return Registry(system=system, agents=agents, commands=commands, warnings=warnings)


def _parse_commands(arch_dir: Path, skills: dict[Path, Skill], warnings: list[str]) -> dict[str, Skill]:
    path = arch_dir / "command.md"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        warnings.append(f"command.md: {exc}")
        return {}

    agents_root = (arch_dir / "agents").resolve()
    commands: dict[str, Skill] = {}
    for line in lines:
        match = COMMAND_LINE.match(line.strip())
        if not match:
            continue
        command, target = match.groups()
        resolved = (arch_dir / target).resolve()
        if not resolved.is_relative_to(agents_root):
            warnings.append(f"command.md: {command} points outside arch/agents; ignored")
            continue
        skill = skills.get(resolved)
        if skill is None:
            warnings.append(f"command.md: {command} points at {target}, which does not exist")
            continue
        skill.command = command
        commands[command] = skill
    return commands


def _split_headers(lines: list[str]) -> tuple[dict[str, str], str]:
    headers: dict[str, str] = {}
    index = 0
    for index, line in enumerate(lines):
        match = HEADER_LINE.match(line)
        if not match:
            break
        headers[match.group(1).strip()] = match.group(2).strip()
    else:
        index = len(lines)
    return headers, "\n".join(lines[index:]).strip()


def _csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]
