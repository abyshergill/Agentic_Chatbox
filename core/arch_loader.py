"""Discovers agents, skills, and commands from the Markdown files in arch/.

Nothing here is hardcoded: adding a folder under arch/agents/ and a line to
command.md is enough to add a skill. Files are re-read on each request, so edits
apply without a restart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

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
    name: str = "Agentic Chatbox"
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

    def find_skill(self, name: str) -> Skill | None:
        """Finds a skill from a loosely written name, as models write them: '/dashboard', 'dashboard',
        '/create_dashboard', 'build_dashboard', 'Build Dashboard'. Exact matches win; then the skill whose command,
        file name or title is contained in the name."""
        def norm(text: str) -> str:
            return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")

        wanted = norm(name or "")
        if not wanted:
            return None
        valid = [s for s in self.commands.values() if s.valid]
        for skill in valid:
            if wanted in {norm(skill.command or ""), norm(skill.id), norm(skill.title)}:
                return skill
        contained = [s for s in valid if norm(s.command or "") and norm(s.command or "") in wanted]
        contained += [s for s in valid if norm(s.id) in wanted and s not in contained]
        if contained:
            return max(contained, key=lambda s: len(norm(s.command or s.id)))  # The most specific match.
        return None

    @property
    def default_skill(self) -> Skill | None:
        return self.commands.get(self.system.default_skill)

    def valid_skills(self) -> list[Skill]:
        return [skill for skill in self.commands.values() if skill.valid]


HEADER_SCAN_LINES = 8  # Skill and agent details live in the first 8 lines of the file.
DEFAULT_ALLOWED_PATHS = ["./workspace/projects"]


def read_header(lines: list[str], limit: int = HEADER_SCAN_LINES) -> tuple[dict[str, str], str, str]:
    """Reads 'Field: value' lines from the top of a file.

    Looks at the first `limit` non-blank lines only. Blank lines and an optional '# Heading' first line are allowed,
    so both of these work:

        Title: X                # X
        Description: Y
        Allowed Paths: Z        Title: X

                                Description: Y

    The header ends at the first other line (such as '## Instructions'). Returns (fields, heading, body).
    """
    fields: dict[str, str] = {}
    heading = ""
    body_start = 0
    seen = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if seen >= limit:
            break
        seen += 1
        if stripped.startswith("# ") and not heading and not fields:
            heading = stripped[2:].strip()
        else:
            match = HEADER_LINE.match(stripped)
            if not match:
                break
            fields[match.group(1).strip().title()] = match.group(2).strip()
        body_start = index + 1
    return fields, heading, "\n".join(lines[body_start:]).strip()


def parse_skill(path: Path, agent_id: str) -> Skill:
    """Parses a skill file. Its details are read from the first 8 lines:

        Title: (or Name:, or a '# Heading')   required
        Description:                           required
        Allowed Paths:                         optional, comma separated; defaults to ./workspace/projects
        Model:                                 optional; without it the model picked in the chat is used

    Everything after those lines is the skill's instructions.
    """
    skill = Skill(id=path.stem, agent_id=agent_id, path=path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        skill.errors.append(f"cannot read file: {exc}")
        return skill

    fields, heading, body = read_header(lines)
    skill.title = fields.get("Title") or fields.get("Name") or heading
    skill.description = fields.get("Description", "")
    paths = fields.get("Allowed Paths")
    skill.allowed_paths = [p.strip() for p in paths.split(",") if p.strip()] if paths else list(DEFAULT_ALLOWED_PATHS)
    skill.model = fields.get("Model", "")
    skill.prompt = body

    where = f"in the first {HEADER_SCAN_LINES} lines"
    if not skill.title:
        skill.errors.append(f"no 'Title:' line (or '# Heading') {where}")
    if not skill.description:
        skill.errors.append(f"no 'Description:' line {where}")
    if paths is not None and not skill.allowed_paths:
        skill.errors.append("'Allowed Paths:' has no usable path")
    if skill.model.endswith(("-cloud", ":cloud")):
        skill.errors.append("'Model:' cloud models are not allowed; this workspace is local-only")
    if not skill.prompt:
        skill.errors.append("the instructions after the header are missing")
    return skill


def parse_agent(directory: Path) -> Agent:
    agent = Agent(id=directory.name, name=directory.name)
    path = directory / "agent.md"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        agent.errors.append(f"cannot read agent.md: {exc}")
        return agent

    headers, heading, body = read_header(lines)
    agent.name = headers.get("Name") or heading or directory.name
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

    headers, heading, body = read_header(lines, limit=12)
    config.name = headers.get("Name") or heading or config.name
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


def _csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]
