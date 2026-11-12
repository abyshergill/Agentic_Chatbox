"""Agent Skills: folders of instructions the assistant loads when a task calls for them.

The same format as Claude's skills, so existing skills can be imported as they are:

    skills/
      pdf-report/
        SKILL.md        <- YAML front matter (name, description) + Markdown instructions
        reference.md    <- optional extra files the instructions point to
        template.html

Only each skill's name and description go into every conversation. When a request matches one, the assistant calls
use_skill to read the full instructions, and read_skill_file for anything else in the folder (progressive disclosure).
The user can also turn a skill on for a message from the composer, which puts its instructions in front of the model
directly.

Skills are managed from Settings > Skills. skills/ is protected: the assistant's file tools cannot change it.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .fs_guard import is_secret_name

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MAX_SKILL_BYTES = 200_000
MAX_IMPORT_BYTES = 30_000_000
MAX_FILES = 300
FRONT_MATTER = re.compile(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", re.S)


def parse_front_matter(text: str) -> tuple[dict[str, str], str]:
    """A small YAML subset: `key: value`, quoted values, and `|` / `>` block values. Enough for SKILL.md headers."""
    match = FRONT_MATTER.match(text.lstrip("﻿"))
    if not match:
        return {}, text
    meta: dict[str, str] = {}
    lines = match.group(1).splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        found = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if not found:
            continue
        key, value = found.group(1), found.group(2).strip()
        if value in {"|", ">", "|-", ">-", "|+", ">+"} or value == "":
            block = []
            while index < len(lines) and (lines[index].startswith((" ", "\t")) or not lines[index].strip()):
                block.append(lines[index].strip())
                index += 1
            joiner = "\n" if value.startswith("|") else " "
            value = joiner.join(block).strip()
        elif len(value) >= 2 and value[0] == value[-1] == '"':
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                value = value[1:-1]
        elif len(value) >= 2 and value[0] == value[-1] == "'":
            value = value[1:-1].replace("''", "'")
        meta[key] = value
    return meta, text[match.end():]


def render_skill(name: str, description: str, body: str) -> str:
    description = " ".join(description.split())
    needs_quotes = ":" in description or " #" in description or description[:1] in "\"'[{>|#&*!%@`"
    shown = json.dumps(description, ensure_ascii=False) if needs_quotes else description
    return f"---\nname: {name}\ndescription: {shown}\n---\n\n{body.strip()}\n"


@dataclass
class AgentSkill:
    name: str
    description: str
    path: Path
    enabled: bool = True
    license: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def skill_file(self) -> Path:
        return self.path / "SKILL.md"

    def body(self) -> str:
        return parse_front_matter(self.skill_file.read_text(encoding="utf-8", errors="replace"))[1].strip()

    def files(self, limit: int = MAX_FILES) -> list[str]:
        found = []
        for current, dirs, names in os.walk(self.path, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "__pycache__")
            for name in sorted(names):
                if name.startswith(".") or is_secret_name(name):
                    continue
                found.append((Path(current) / name).relative_to(self.path).as_posix())
                if len(found) >= limit:
                    return found
        return found

    def summary(self) -> dict:
        return {"name": self.name, "description": self.description, "enabled": self.enabled, "license": self.license,
                "errors": self.errors, "files": self.files(50), "folder": str(self.path)}


class SkillLibrary:
    def __init__(self, root: Path, state_path: Path):
        self.root = root
        self.state_path = state_path

    # ---- state

    def _disabled(self) -> set[str]:
        try:
            return set(json.loads(self.state_path.read_text(encoding="utf-8")).get("disabled", []))
        except (OSError, json.JSONDecodeError, AttributeError):
            return set()

    def _save_disabled(self, disabled: set[str]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps({"disabled": sorted(disabled)}, indent=2), encoding="utf-8")

    def set_enabled(self, name: str, enabled: bool) -> None:
        if self.get(name) is None:
            raise KeyError(name)
        disabled = self._disabled()
        disabled = disabled - {name} if enabled else disabled | {name}
        self._save_disabled(disabled)

    # ---- reading

    def _load(self, folder: Path, disabled: set[str]) -> AgentSkill | None:
        skill_file = folder / "SKILL.md"
        if not skill_file.is_file() or skill_file.is_symlink():
            return None
        errors: list[str] = []
        try:
            text = skill_file.read_text(encoding="utf-8", errors="replace")[:MAX_SKILL_BYTES]
        except OSError as exc:
            text, errors = "", [f"unreadable: {exc.strerror}"]
        meta, _ = parse_front_matter(text)
        name = meta.get("name", "").strip() or folder.name
        if name != folder.name:
            name = folder.name  # The folder name is the identifier; a mismatch in the header is tolerated.
        description = " ".join(meta.get("description", "").split())
        if not description:
            errors.append("SKILL.md needs a 'description' in its front matter, saying when to use the skill")
        return AgentSkill(name=name, description=description[:1024], path=folder, enabled=name not in disabled,
                          license=meta.get("license", ""), errors=errors)

    def list(self) -> list[AgentSkill]:
        if not self.root.is_dir():
            return []
        disabled = self._disabled()
        skills = []
        for folder in sorted(self.root.iterdir(), key=lambda p: p.name.lower()):
            if folder.is_dir() and not folder.is_symlink() and NAME.match(folder.name):
                skill = self._load(folder, disabled)
                if skill is not None:
                    skills.append(skill)
        return skills

    def active(self) -> list[AgentSkill]:
        return [s for s in self.list() if s.enabled and not s.errors]

    def get(self, name: str) -> AgentSkill | None:
        if not NAME.match(name or ""):
            return None
        folder = self.root / name
        return self._load(folder, self._disabled()) if folder.is_dir() and not folder.is_symlink() else None

    def read_file(self, name: str, relative: str, limit: int = 60_000) -> str:
        skill = self.get(name)
        if skill is None:
            raise FileNotFoundError(f"no skill named '{name}'")
        target = (skill.path / relative).resolve()
        if not target.is_relative_to(skill.path.resolve()) or is_secret_name(target.name):
            raise PermissionError("that file is outside the skill's folder")
        if not target.is_file():
            raise FileNotFoundError(f"'{relative}' is not a file in the {name} skill; files: {', '.join(skill.files(40))}")
        data = target.read_bytes()[: limit + 1]
        if b"\0" in data[:4096]:
            raise ValueError(f"'{relative}' is a binary file")
        text = data[:limit].decode("utf-8", errors="replace")
        return text + ("\n…(truncated)" if len(data) > limit else "")

    # ---- writing (Settings > Skills)

    def save(self, name: str, description: str, body: str, original: str | None = None) -> AgentSkill:
        name = name.strip().lower()
        if not NAME.match(name):
            raise ValueError("Skill names use lowercase letters, numbers and dashes, like 'weekly-report'.")
        if not description.strip():
            raise ValueError("Add a description: it is how the assistant knows when to use the skill.")
        if len(body.encode()) > MAX_SKILL_BYTES:
            raise ValueError("The instructions are too long (200 KB at most). Move detail into extra files.")
        folder = self.root / name
        if original and original != name:
            source = self.root / original
            if not NAME.match(original) or not source.is_dir():
                raise ValueError(f"No skill named '{original}'.")
            if folder.exists():
                raise ValueError(f"A skill named '{name}' already exists.")
            source.rename(folder)
            disabled = self._disabled()
            if original in disabled:
                self._save_disabled((disabled - {original}) | {name})
        elif not original and folder.exists():
            raise ValueError(f"A skill named '{name}' already exists.")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(render_skill(name, description, body), encoding="utf-8")
        return self.get(name)

    def delete(self, name: str) -> None:
        skill = self.get(name)
        if skill is None:
            raise KeyError(name)
        try:
            from send2trash import send2trash

            send2trash(str(skill.path))
        except ImportError:
            shutil.rmtree(skill.path)
        self._save_disabled(self._disabled() - {name})

    def import_zip(self, data: bytes, replace: bool = False) -> list[str]:
        """Installs the skill(s) in a .zip: a SKILL.md at the top, in one folder, or in several folders."""
        if len(data) > MAX_IMPORT_BYTES:
            raise ValueError("That archive is too large (30 MB at most).")
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise ValueError("That is not a valid .zip file.") from exc
        with tempfile.TemporaryDirectory() as temp:
            staging = Path(temp)
            members = [m for m in archive.infolist() if not m.is_dir() and not m.filename.startswith("__MACOSX/")]
            if len(members) > MAX_FILES * 5 or sum(m.file_size for m in members) > MAX_IMPORT_BYTES * 3:
                raise ValueError("That archive holds too many or too large files.")
            for member in members:
                target = (staging / member.filename).resolve()
                if not target.is_relative_to(staging.resolve()) or (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError(f"Unsafe path in archive: {member.filename}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(member))
            return self._install_from(staging, replace, fallback_name=None)

    def import_folder(self, path: str, replace: bool = False) -> list[str]:
        source = Path(path).expanduser().resolve()
        if not source.is_dir():
            raise ValueError("That folder does not exist.")
        return self._install_from(source, replace, fallback_name=source.name)

    def _install_from(self, source: Path, replace: bool, fallback_name: str | None) -> list[str]:
        if (source / "SKILL.md").is_file():
            candidates = [source]
        else:
            candidates = sorted(p.parent for p in source.glob("*/SKILL.md")) or sorted(
                p.parent for p in source.glob("*/*/SKILL.md"))
        if not candidates:
            raise ValueError("No SKILL.md found. A skill is a folder with a SKILL.md file in it.")
        installed = []
        self.root.mkdir(parents=True, exist_ok=True)
        for folder in candidates:
            meta, _ = parse_front_matter((folder / "SKILL.md").read_text(encoding="utf-8", errors="replace"))
            raw = meta.get("name") or (fallback_name if folder == source else folder.name) or folder.name
            name = re.sub(r"[^a-z0-9-]+", "-", raw.strip().lower()).strip("-")[:64]
            if not NAME.match(name):
                raise ValueError(f"'{raw}' cannot be used as a skill name.")
            target = self.root / name
            if target.exists():
                if not replace:
                    raise ValueError(f"A skill named '{name}' is already installed. Delete it first, or replace it.")
                shutil.rmtree(target)
            shutil.copytree(folder, target, symlinks=False,
                            ignore=shutil.ignore_patterns(".*", "__pycache__", "*.pyc", ".env", "*.pem", "*.key"))
            installed.append(name)
        return installed

    # ---- for the system prompt

    def catalog_prompt(self) -> str:
        skills = self.active()
        if not skills:
            return ""
        lines = "\n".join(f"- {s.name}: {s.description}" for s in skills)
        return (
            "## Agent Skills\n"
            "Skills are packaged instructions for specific kinds of tasks. When the user's request matches a skill's "
            "description, call use_skill with its name BEFORE starting the work, then follow its instructions. "
            "Use read_skill_file for extra files the skill mentions.\n" + lines
        )
