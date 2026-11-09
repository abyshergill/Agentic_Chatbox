# List Agents

Title: List Agents

Description: Lists the workspace's agents and skills, shows the details of one agent (purpose, rules, applications, skills, and who delegates to it), and finds which agent handles a given kind of request. Flags agents that are registered without a folder, or that have a folder without being registered. Read only. Use it when the user asks what agents exist, what an agent can do, or which agent to use.

Allowed Paths: ./arch/agents, ./arch/_archive, ./arch/system.md

## Instructions

You show the user an accurate picture of the agents, taken from the files as they are now.

### Rules

1. Read only. Never create, change, or move files.
2. Describe only what you read in the files. Never describe an agent or skill from memory.
3. Write ASCII only in replies that the user may paste into the files.

### List all agents

1. Read the Available Agents table in `arch/system.md`, and call `list_directory` on `arch/agents/`.
2. For each agent folder, count the files in `skills/`, and read the Name and Read Only lines from `agent.md`.
3. Reply with a table: ID, display name, purpose (from `system.md`), number of skills, and read only or not.
4. Below the table, flag any mismatch: a folder that is not in the table, a table row without a folder or `agent.md`, or an agent file without skills.

### Show one agent

1. Read `arch/agents/<agent_id>/agent.md` and the files in its `skills/` folder.
2. Use `search_file_content` for the agent ID across `arch/` to find who delegates to it.
3. Reply with: the description, capabilities, applications, whether it is read only, a summary of its key rules (three to five lines), its skills (title and one line each), and which agents hand work to it.

### Find the right agent

1. Compare the request with the Available Agents table and the "Choosing an agent" list in `arch/system.md`.
2. Reply with the best agent and skill, one line on why, and a runner-up if two are close.
3. If no agent fits, say so and suggest creating a skill or agent with Create Agent.

### Show the archive

If the user asks what was removed or backed up, list the folders in `arch/_archive/` with their dates and contents.
