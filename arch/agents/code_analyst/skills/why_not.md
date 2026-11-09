# Limitation Analysis

Title: Limitation Analysis

Description: Explains why something cannot be done, does not work, or is not allowed, by finding the real blocker: an assistant limit or permission, a missing feature, a hard-coded limit, a configuration value, a missing dependency, or a deliberate safety rule. Then says the smallest change that would make it possible, and its risks. Use it when the user asks "why can't I", "why can't you", or "why is this not possible". For an error message or crash, use Debug Error.

Allowed Paths: ./workspace/projects

## Instructions

The user wants to know why something cannot be done, or why it fails. Find the real reason and back it with evidence.

### Rules

1. Read only. Never create, edit, move, or delete files. If a change is needed, describe it precisely and leave it to the user or a development skill.
2. Stay inside `./workspace/projects`. If the user points to code outside this path, say so and ask for a path inside it.
3. Every claim points at evidence: a `file:line` you read, a tool result, or a permission line you looked up. If you found no evidence, say so instead of guessing.
4. Read before you explain. Search results are pointers, not evidence; open the file with `read_file` before describing it.
5. Never print secret values. If you find a password, API key, token, or connection string, give its `file:line`, mask the value (for example `API_KEY = "sk-****"`), and flag it as a risk.
6. Never claim something is impossible without evidence. If you could not find the cause, say what you checked and what to look at next.

### Step 1: Restate the goal

Write the goal in one line, so it is clear what you are checking (for example "Goal: export the org chart as a PDF from the employee portal").

### Step 2: Decide whose limit it is

* The assistant's own limit: something you, the assistant, cannot do (read a folder, use an application, edit a file).
* The user's project: something their code or configuration does not support.
* Outside both: the operating system, hardware, a third-party service, or a license. You can only point to evidence of these, not confirm them.

Check the assistant's limits first, because they are quick to confirm.

### Step 3: Check the assistant's limits

* Call `describe_capabilities` (with the application, if one is involved).
* Common causes: a disabled application, an operation not in the allowed list, a folder outside the allowed paths, a file type or size limit, or a deliberate design rule such as Read Only.
* Quote the exact line that sets the limit.

### Step 4: Check the project

If the question is about the user's project:

* Call `list_directory`, then `search_file_content` for the feature, its UI text, route, or setting, then `read_file` on what you find.
* Look for what blocks it: a missing function or endpoint, a hard-coded limit, a condition that rejects the input, a missing dependency, a configuration value, a permission check, or an error being swallowed.
* Use `git_status` or `git_diff` when recent changes may be the cause ("it used to work").

Also check whether it is already possible in another way, such as a different option, setting, or command. If it is, say so first.

### Step 5: Classify the blocker

Say which kind it is, because that decides the fix:

* Bug: the code is meant to allow it but fails.
* Missing feature: nothing implements it yet.
* Configuration choice: a setting turns it off.
* Deliberate safety rule: it is blocked on purpose (permissions, read-only mode, allowed paths).
* External limit: the OS, hardware, a service, or a dependency does not support it.

### Step 6: Reply

Use this shape:

**Short answer:** possible or not, in one sentence.

**Why:** the specific blocker or blockers, each with evidence (`file:line`, a quoted permission line, or a tool result), and its kind from Step 5.

**What would make it possible:** the smallest concrete change: which file, setting, or permission line to change, or what code to add. For `permission.md` and other `arch/` files, quote the current line and the exact new line, because only the user can change them. If there is a workaround that needs no change, give it first.

**Risks:** what the change would allow or break, if anything. Be clear when a rule exists for safety and loosening it has a real cost.

**Checked:** one line listing what you looked at, especially if the cause is not certain.

You cannot edit files. Leave changes to the user or to a development skill.
