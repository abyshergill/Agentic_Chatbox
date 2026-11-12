"""#skills: lets the assistant load an Agent Skill's instructions and its extra files. Read-only."""

from __future__ import annotations

from ..agent_skills import SkillLibrary
from ..tools import Adapter, Tool, ToolContext, ToolError, ToolResult


def library(ctx: ToolContext) -> SkillLibrary:
    return SkillLibrary(ctx.settings.skills_dir, ctx.settings.data_dir / "skills.json")


async def use_skill(ctx: ToolContext, args: dict) -> ToolResult:
    skills = library(ctx)
    skill = skills.get(args["name"].strip().lower())
    if skill is None or not skill.enabled or skill.errors:
        names = ", ".join(s.name for s in skills.active()) or "none"
        raise ToolError(f"no enabled skill called '{args['name']}'. Enabled skills: {names}")
    files = [f for f in skill.files() if f != "SKILL.md"]
    listing = ("\n\nOther files in this skill (read them with read_skill_file when the instructions refer to them):\n"
               + "\n".join(f"- {f}" for f in files)) if files else ""
    return ToolResult(True, f"# Skill: {skill.name}\n\n{skill.body()}{listing}")


async def read_skill_file(ctx: ToolContext, args: dict) -> ToolResult:
    try:
        return ToolResult(True, library(ctx).read_file(args["name"].strip().lower(), args["path"]))
    except (FileNotFoundError, PermissionError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


adapter = Adapter(
    app_id="skills",
    tools=[
        Tool("use_skill", "skills", "read",
             "Load the full instructions of an Agent Skill listed under 'Agent Skills'. Call it before doing a task "
             "that matches the skill's description.",
             {"name": {"type": "string", "description": "The skill's name, e.g. weekly-report"}},
             use_skill, required=["name"]),
        Tool("read_skill_file", "skills", "read",
             "Read an extra file that belongs to an Agent Skill (a reference, template or example it mentions).",
             {"name": {"type": "string", "description": "The skill's name"},
              "path": {"type": "string", "description": "The file's path inside the skill folder, e.g. reference.md"}},
             read_skill_file, required=["name", "path"]),
    ],
)
