"""#introspect: lets the assistant see its own limits, so it can explain why something cannot be done.

Read-only. It reports what arch/permission.md allows right now, the active skill's folders, which tools exist for
each operation, file-type and size limits, and the model in use. It never shows file contents of arch/, secrets,
or API keys, and it cannot change anything.
"""

from __future__ import annotations

from .. import dataviz, datatools
from ..arch_loader import load_registry
from ..tools import Adapter, Tool, ToolContext, ToolResult

# Limits that are deliberate design decisions, so the assistant can say "by design" rather than guess.
BY_DESIGN = [
    "The assistant cannot read or change arch/ (permissions, skills, commands); only you can edit those files.",
    "Files outside the active skill's Allowed Paths (and outside the workspace roots) cannot be read or written.",
    "Files that look like secrets (.env, keys, credentials) are refused, and credential-shaped text is redacted.",
    "Every change (write, delete, run a command) waits for your approval unless permission.md exempts it.",
    "Commands run in an OS sandbox on macOS: no network, and writes only inside permitted folders.",
    "Data tools never run code written by the model; formulas are limited to arithmetic on columns.",
    "Source data files are never modified; transformed or merged data is written to a new file.",
    "Remote API models are never chosen automatically; you pick them in the Model menu.",
]


def _tools_by_app(ctx: ToolContext) -> dict[str, list[str]]:
    run = ctx.run
    tools = run.ctx.tools.values() if run is not None else []
    grouped: dict[str, list[str]] = {}
    for tool in tools:
        grouped.setdefault(tool.app, []).append(f"{tool.name} ({tool.operation})")
    return grouped


async def describe_capabilities(ctx: ToolContext, args: dict) -> ToolResult:
    focus = (args.get("application") or "").lstrip("#").strip().lower()
    perms = ctx.permissions
    perms.reload()
    activation = ctx.activation
    lines: list[str] = []

    if activation is not None and not focus:
        agent, skill = activation.agent, activation.skill
        lines += [
            "ACTIVE SKILL",
            f"- {skill.title} ({skill.command or skill.id}) of agent {agent.name}",
            f"- applications this agent may use: {', '.join('#' + a for a in agent.applications) or 'none'}"
            + (" (read-only agent: only tools that look, never change)" if agent.read_only else ""),
            f"- folders it may access: {', '.join(ctx.guard.root_labels()) or 'none'}",
            f"- model: {activation.model or 'chosen automatically'}",
            "",
        ]

    grouped = _tools_by_app(ctx)
    lines.append("APPLICATIONS (from permission.md, enforced by the app on every call)")
    for app_id, app in sorted(perms.apps.items()):
        if focus and app_id != focus:
            continue
        if not app.enabled:
            lines.append(f"- #{app_id}: DISABLED. Nothing in it can run until you set Access: enabled.")
            continue
        exempt = [op for op in app.operations if op in app.approval_exempt]
        approval = "every operation needs your approval" if app.approval_required else "no approval needed"
        if app.approval_required and exempt:
            approval = f"needs approval except: {', '.join(exempt)}"
        lines.append(f"- #{app_id}: allowed operations: {', '.join(app.operations) or 'none'}; {approval}")
        tools = grouped.get(app_id, [])
        missing = [op for op in app.operations if not any(t.endswith(f"({op})") for t in tools)]
        if tools:
            lines.append(f"    tools: {', '.join(sorted(tools))}")
        if missing and grouped:
            lines.append(f"    allowed but no tool implements: {', '.join(missing)}")
        if app_id == "uploads":
            lines.append(f"    file types: {app.fields.get('Extensions', '')}; max size {app.fields.get('Max Size MB', '?')} MB")
    if focus and focus not in perms.apps:
        lines.append(f"- #{focus} is not defined in permission.md, so nothing can use it.")
    defined = set(perms.apps)
    unlisted = sorted(app for app in grouped if app not in defined)
    if unlisted and not focus:
        lines.append(f"- tools exist for {', '.join('#' + a for a in unlisted)} but permission.md does not define them, so they are blocked")

    if not focus:
        registry = load_registry(ctx.settings.arch_dir)
        lines += ["", "SKILLS (slash commands)"]
        lines += [f"- {cmd}: {skill.title}" + ("" if skill.valid else " (malformed, cannot run)") for cmd, skill in sorted(registry.commands.items())]
        lines += [
            "",
            "LIMITS",
            f"- data files: reads {', '.join(sorted(dataviz.SUPPORTED))}; writes {', '.join(sorted(datatools.WRITABLE))}; "
            f"up to {dataviz.MAX_BYTES // 1024 // 1024} MB and {dataviz.MAX_ROWS:,} rows",
            f"- reading a file: first {ctx.settings.max_read_bytes:,} bytes; tool output shown to the model: {ctx.settings.max_tool_output_chars:,} characters",
            f"- steps per request: {registry.system.max_iterations} tool calls (Max Tool Iterations in system.md)",
            f"- model context window: {ctx.settings.num_ctx:,} tokens (WORKSPACE_NUM_CTX)",
            "",
            "BY DESIGN",
            *[f"- {item}" for item in BY_DESIGN],
        ]
    return ToolResult(True, "\n".join(lines))


adapter = Adapter(
    app_id="introspect",
    tools=[
        Tool("describe_capabilities", "introspect", "describe",
             "Describe what this assistant is and is not allowed to do right now: enabled applications and operations, "
             "approval rules, the active skill's folders, which tools exist, file-type and size limits, and limits that "
             "are deliberate. Use it to explain why a request cannot be done. Optionally focus on one application.",
             {"application": {"type": "string", "description": "Optional, e.g. git, terminal, data, filesystem"}},
             describe_capabilities),
    ],
)
