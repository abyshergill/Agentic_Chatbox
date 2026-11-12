---
name: code-review
description: Review code or a change for bugs, security problems and maintainability, with concrete fixes. Use when the user asks to review, audit or check code, a file, a pull request or a git diff.
---

# Code review

Review like a senior engineer who has to maintain the code afterwards.

## Steps

1. Find out what changed: if the project is a git repository, run `git diff` (or read the files the user named).
2. Read enough surrounding code to understand each change. Do not review what you have not read.
3. Check, in this order:
   - **Correctness**: logic errors, off-by-one, null/None handling, error paths, concurrency.
   - **Security**: injection, path traversal, secrets in code, unsafe deserialization, missing auth checks.
   - **Data loss**: destructive operations without confirmation or backup.
   - **Tests**: is the new behaviour covered? Would the tests catch the bug you found?
   - **Clarity**: names, dead code, duplication — only when it matters.

## Output

Start with a one-line verdict (Ready / Ready with small fixes / Needs changes).
Then list findings, most severe first:

```
[severity] path/to/file.py:42 — what is wrong
  Why it matters: …
  Fix: … (a short code snippet when useful)
```

Severity is one of: blocker, major, minor, nit. Skip nits when there are blockers.
Do not change any files unless the user asks you to apply the fixes.
