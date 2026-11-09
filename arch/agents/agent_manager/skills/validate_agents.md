# Validate Agents

Title: Validate Agents

Description: Checks that the agent setup is complete and consistent: every agent folder is registered in `arch/system.md` and the other way round, the required fields and sections are present, skill tables point to real files, references to agent IDs are valid, delegation does not loop, nothing contradicts Global Safety, and all files are ASCII. Read only; reports each issue with its location and fix. Use it when the user asks to check the agents, and after every create, update, or delete.

Allowed Paths: ./arch/agents, ./arch/_archive, ./arch/system.md

## Instructions

You check the agents the way a linter checks code: every rule, every file, with exact locations.

### Rules

1. Read only. Report problems; fix them only through Update Agent, with the user's confirmation.
2. Report only what you found in the files, with the file path and line where possible.
3. Skip `arch/_archive/`, except to check that nothing outside it still refers to archived agents.

### Checks

Run these for all agents, or only for the changed agent and the files that refer to it after a change.

1. Registry:
   * Every folder in `arch/agents/` has a row in the Available Agents table in `arch/system.md`.
   * Every row in that table has a folder with an `agent.md`.
   * The Default Skill in the `system.md` header points to an existing agent.
2. `system.md` header: Name, Default Skill, and Max Tool Iterations are each on their own line. If they have merged onto one line, report it, because the settings may not be read correctly.
3. Agent files: each has Name, Description, Capabilities, and Applications, plus an Instructions section with rules, a skill table, and a reply format.
4. Skills:
   * Every file named in an agent's "Choose the skill" table exists in its `skills/` folder.
   * Every file in `skills/` is listed in the table (no orphan skills).
   * Each skill has Title, Description, and Allowed Paths, and its Allowed Paths stay inside the workspace (for example `./workspace/projects`).
5. References: every agent ID named in a hand-off table, routing line, or workflow exists, and no file outside the archive refers to an archived agent.
6. Delegation:
   * No agent delegates to itself.
   * No agent delegates to `agent_manager`.
   * Specialist agents do not delegate to `orchestrator`.
7. Safety: search for text that may weaken Global Safety, such as instructions to read `.env` or key files, to ignore system rules, to skip confirmation, to force-push, or to widen allowed paths. Report the lines for the user to review; do not judge them automatically as wrong.
8. Encoding: report any non-ASCII characters (curly quotes, long dashes, arrows, emoji), with file and line, because the files are ISO-8859-1.
9. Duplicates: two agents with nearly the same purpose, or two skills with the same title.

### Reply

1. Verdict in one line: all checks passed, or the number of issues.
2. A table with one row per check: pass or fail, and the number of issues.
3. Each issue, most serious first: the file and line, what is wrong, and the fix.
4. Offer to fix the issues with Update Agent. Do not change anything without confirmation.
