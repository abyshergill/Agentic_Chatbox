---
name: skill-creator
description: Help the user design and write a new Agent Skill (a SKILL.md with name, description and instructions). Use when the user wants to create, improve or package a skill.
---

# Skill creator

A skill is a folder with a `SKILL.md`: YAML front matter plus Markdown instructions. Only the name and the
description are always visible to the assistant, so the description decides when the skill is used.

## Steps

1. Ask what task the skill is for, with one or two real examples of requests that should trigger it — unless the user
   already said.
2. Draft the skill:
   - **name**: lowercase words joined by dashes, e.g. `weekly-status-report`.
   - **description**: one or two sentences: what it does AND when to use it (the trigger phrases a user would say).
   - **instructions**: numbered steps, the exact output format (a template), and rules/edge cases. Keep it under
     ~300 lines; put long reference material in extra files.
3. Show the complete `SKILL.md` in a single fenced block.
4. Tell the user how to add it: **Settings → Skills → New skill**, paste the description and the instructions
   (everything after the front matter), and save. Extra files can be added by importing a .zip of the folder.

You cannot write into the skills folder yourself; it is protected. Never claim the skill was installed.
