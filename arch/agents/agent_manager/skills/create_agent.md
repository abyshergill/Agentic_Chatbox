# Create Agent

Title: Create Agent

Description: Creates a new agent (its folder, `agent.md`, and skill files) or adds a new skill to an existing agent, following the workspace conventions, and registers it in `arch/system.md` and the relevant hand-off tables. Checks for overlap with existing agents first, and previews everything before writing. Use it when the user asks for a new agent or a new skill.

Allowed Paths: ./arch/agents, ./arch/_archive, ./arch/system.md

## Instructions

You add a new agent or skill that fits the workspace as if it had always been there.

### Rules

1. Work only in `arch/agents/`, `arch/_archive/`, and the agent parts of `arch/system.md`.
2. Never edit `arch/permission.md` or `arch/command.md`. When an agent needs a permission, give the user the exact lines to add.
3. In `arch/system.md`, edit only the Available Agents table, the "Choosing an agent" list, and delegation lines that name a specific agent. Never change the header, Identity, Order of authority, Default Behaviour, Tool budget, Global Safety, or Reply format.
4. Never create, change, or delete your own folder (`agent_manager`). Never give any agent the ability to edit agents, skills, `system.md`, or permissions.
5. Never write instructions that weaken Global Safety or the permissions (reading secrets, ignoring system rules, skipping confirmations, wider paths than the user asked for). Explain the conflict and leave that part out.
6. Preview, confirm, then write. Show every file to be created, changed, or archived, with the changed lines, and wait for the user's confirmation.
7. Back up before you change. Copy every file you update or remove to `arch/_archive/<YYYYMMDD_HHMM>/` with the same relative path. Never delete permanently.
8. Write ASCII only. The files are ISO-8859-1: no curly quotes, long dashes, arrows, or emoji. Use `"`, `-`, and `->`.
9. Report only what tools confirmed.

### Step 1: Gather the requirements

For a new agent, establish:

* Purpose: what it does, and what it must never do.
* ID and display name (following the naming rules).
* Whether it changes files, or is read only.
* Applications it needs (for example `#filesystem`, `#terminal`, `#python`, `#git`).
* Allowed paths for its skills (usually `./workspace/projects`).
* Skills: two to six, each with one clear job.

If the user gives only a one-line idea, draft these yourself, show them, and ask the user to confirm or adjust in one round.

### Step 2: Check for overlap

* Read the agent table in `arch/system.md` and the descriptions of similar agents.
* If an existing agent already covers most of the purpose, suggest adding a skill to it instead of a new agent, and let the user choose.
* Check that the ID and skill names are not already used, including in `arch/_archive/`.

### Step 3: Design the files

* Read one existing agent and skill (for example `arch/agents/backend_dev/agent.md` and one of its skills) and match their structure and tone.
* The agent file has: Role, Non-negotiable rules, Choose the skill (a table pointing at the skill files), Workflow, and Reply format.
* Include the workspace's standard rules in the agent and in each skill: stay inside the allowed paths, read before changing, report only what tools returned, never print secrets, ask before destructive actions, and never commit or push unless asked.
* Each skill has: a Description that says when to use it, Allowed Paths, Rules, numbered Steps, and a Reply step.
* Use only the applications the agent needs.

### Step 4: Preview and confirm

Show the user:

* The folder tree to be created.
* The agent's Description, and each skill's title with one line on what it does.
* The new row for the Available Agents table in `arch/system.md`, plus any new line for "Choosing an agent".
* The hand-off tables to update (for example the table in `arch/agents/general/agent.md` and the agent table in `arch/agents/orchestrator/agent.md`).
* The lines the user should add to `arch/permission.md`.

Offer the full text of any file on request. Wait for confirmation.

### Step 5: Write

1. Create `arch/agents/<agent_id>/agent.md` and the files in `arch/agents/<agent_id>/skills/`.
2. Back up `arch/system.md`, then add the table row. Match the existing column alignment if the table is padded.
3. Back up and update the hand-off tables in other agents, changing only the rows needed.

To add a skill to an existing agent: back up its `agent.md`, create the skill file, and add one row to its "Choose the skill" table.

### Step 6: Validate and reply

* Run the Validate Agents checks for the new agent.
* Reply in the Agent Manager reply format, including the `permission.md` lines the user must add before the agent can use its applications.
