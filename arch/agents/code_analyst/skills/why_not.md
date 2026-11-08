Title: Limitation Analysis
Description: Explain why something cannot be done, does not work or is not possible, by finding the blocking code, configuration, permission or missing feature.
Allowed Paths: ./workspace/projects

The user wants to know why something cannot be done, or why it fails. Find the real reason.

1. Restate the goal in one line, so it is clear what you are checking.
2. Check the assistant's own limits first: call describe_capabilities (with the application, if one is involved).
   Many "cannot" answers are a disabled application, an operation not in the allowed list, a folder outside the
   allowed paths, a file type or size limit, or a deliberate design rule.
3. If the question is about the user's project code, read it: list_directory, search_file_content for the feature,
   read_file on what you find. Look for what blocks it: a missing function or endpoint, a hard-coded limit, a
   condition that rejects the input, a missing dependency, a configuration value, or an error being swallowed.
   Use git_status or git_diff when recent changes may be the cause.
4. Reply in this shape:
   **Short answer:** possible or not, in one sentence.
   **Why:** the specific blocker(s), each with evidence (file:line, a quoted permission line, or a tool result).
   Say whether the limit is a bug, a missing feature, a configuration choice, or a deliberate safety rule.
   **What would make it possible:** the smallest concrete change: which file, setting or permission line to change,
   or what code to add. For permission.md and other arch/ files, say exactly what the user should edit,
   because only the user can change them.
   **Risks:** what the change would allow or break, if anything.
5. Never claim something is impossible without evidence. If you could not find the cause, say what you checked
   and what to look at next.

You cannot edit files. Leave changes to the user or to a development skill.
