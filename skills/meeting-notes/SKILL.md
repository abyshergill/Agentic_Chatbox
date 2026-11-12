---
name: meeting-notes
description: Turn a raw meeting transcript, voice memo text or messy notes into clean meeting notes with decisions and action items. Use when the user pastes or attaches notes from a meeting or call.
---

# Meeting notes

Produce notes someone who missed the meeting can act on.

## Steps

1. Read the whole input first. If it is an attachment or a file, read it with the file tools.
2. Identify the participants (as named in the text; never invent names).
3. Write the notes in this exact structure:

```markdown
# <Meeting topic> — <date if stated>

**Participants:** …

## Summary
Three to five sentences: why the meeting happened and what came out of it.

## Decisions
- …

## Action items
| Owner | Task | Due |
| --- | --- | --- |

## Open questions
- …
```

## Rules

- Only record decisions that were actually made; put proposals that were not agreed under Open questions.
- An action item needs an owner. If none was named, write "Unassigned".
- Keep the user's terminology and numbers exactly.
- If the user asks for a file, save it as Markdown next to the source (or in the selected project), named `meeting-notes-<yyyy-mm-dd>.md`.
