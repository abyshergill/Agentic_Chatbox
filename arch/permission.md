# APPLICATION PERMISSIONS

The AI can only use what this file allows. It is enforced by the Python backend
and re-read before every action, so edits apply immediately. Only you can change
it: the assistant's tools cannot reach this directory.

Fields
  Access:          enabled | disabled (anything else counts as disabled)
  Operations:      the only operations this application may perform
  Approval:        required | not_required (anything else counts as required)
  Approval Exempt: operations that skip approval (destructive ones never can)

The terminal also supports
  Extra Paths:     folders of tools outside the system PATH, such as ~/.cargo/bin

## Development Applications

#vscode
Name: Visual Studio Code
Executable: code
Access: enabled
Operations: open_project, open_file
Approval: required

#terminal
Name: Terminal
Access: enabled
Operations: execute_command, run_project_tests
Approval: required
Allowed Commands: ls, pwd, cat, head, tail, wc, grep, find, sort, uniq, diff, echo, date, mkdir, touch, cp, mv, node, npm, make, cargo, go, pytest
Timeout Seconds: 120

#python
Name: Python
Executable: python3
Access: enabled
Operations: execute_script
Approval: required
Timeout Seconds: 120

#git
Name: Git
Executable: git
Access: enabled
Operations: status, diff, add, commit
Approval: required
Approval Exempt: status, diff

#browser
Name: Browser
Executable: firefox
Access: disabled
Operations: open_url
Approval: required

## Data Analysis

#data
Name: Data Analysis
Access: enabled
Operations: profile, query, analyze, dashboard, presentation, transform, excel
Approval: required
Approval Exempt: profile, query, analyze

## File System

#filesystem
Name: File System
Access: enabled
Operations: list, read, search, write, create, rename, delete
Approval: required
Approval Exempt: list, read, search
Roots: ./workspace

#uploads
Name: File Uploads
Access: enabled
Operations: upload
Approval: not_required
Extensions: py, js, ts, md, txt, json, jsonl, csv, tsv, pdf, html, css, yaml, yml, toml, rs, c, h, cpp, sql, log, xlsx, docx, doc, rtf, odt, htm
Max Size MB: 25

## Documents

#documents
Name: Word Documents
Access: enabled
Operations: create
Approval: required

## Self Knowledge

#introspect
Name: Capabilities
Access: enabled
Operations: describe
Approval: not_required

## Agent Orchestration

#delegate
Name: Agent Delegation
Access: enabled
Operations: delegate
Approval: not_required

## System Operations

#system
Name: System
Access: disabled
Operations: shutdown, restart, install_package
Approval: required
