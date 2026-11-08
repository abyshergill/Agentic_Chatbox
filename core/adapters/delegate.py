"""#delegate: hands a sub-task to another skill, which runs under its own permissions."""

from __future__ import annotations

from ..tools import Adapter, Tool, ToolContext, ToolError, ToolResult


async def delegate_task(ctx: ToolContext, args: dict) -> ToolResult:
    if ctx.run is None:
        raise ToolError("delegation is only available during a chat run")
    return await ctx.run.delegate(args["command"], args["task"])


adapter = Adapter(
    app_id="delegate",
    tools=[
        Tool("delegate_task", "delegate", "delegate",
             "Give a separate piece of work to another skill and get back its result.",
             {"command": {"type": "string", "description": "Slash command of the skill, e.g. /db-migration"},
              "task": {"type": "string", "description": "Exactly what that skill should do"}},
             delegate_task, required=["command", "task"]),
    ],
)
