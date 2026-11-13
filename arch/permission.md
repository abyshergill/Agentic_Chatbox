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

MCP tools (#mcp) are added in Settings > Connectors. Each call needs approval unless you chose
"Always allow" for that tool. Tools a server marks read-only use the 'read' operation, others 'call'.

The web scraper also supports
  Allowed Domains: the only sites it may contact; a domain also allows its subdomains
  Render Browser:  chrome | edge, used by render_page and screenshot
  Respect Robots:  yes | no (anything else counts as yes)
  Max Pages Per Request, Max Requests Per Minute, Timeout Seconds, Max Size MB: limits

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

#chrome
Name: Google Chrome
Executable: chrome
Access: disabled
Operations: open_url
Approval: required

#edge
Name: Microsoft Edge
Executable: msedge
Access: disabled
Operations: open_url
Approval: required

## Web Access

#web
Name: Web Scraper
Access: disabled
Operations: fetch_page, render_page, extract, screenshot, crawl
Approval: required
Allowed Domains: localhost, 127.0.0.1
Render Browser: chrome
Respect Robots: yes
Max Pages Per Request: 50
Max Requests Per Minute: 20
Timeout Seconds: 30
Max Size MB: 10

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
Extensions: csv, tsv, tab, psv, dat, txt, json, jsonl, ndjson, xml, yaml, yml, toml, ini, cfg, conf, log, md, rst, html, htm, css, scss, svg, xlsx, xlsm, xls, ods, parquet, pdf, docx, doc, rtf, odt, pptx, odp, ipynb, py, js, jsx, ts, tsx, vue, java, kt, go, rs, c, h, cpp, hpp, cs, swift, php, rb, sql, r
Max Size MB: 25

## Documents

#documents
Name: Word Documents
Access: enabled
Operations: create
Approval: required

## Images and Video

#media
Name: Images and Video
Access: enabled
Operations: image, video
Approval: required

## Self Knowledge

#introspect
Name: Capabilities
Access: enabled
Operations: describe
Approval: not_required

## Connectors and Skills

#mcp
Name: MCP Servers (Connectors)
Access: enabled
Operations: read, call
Approval: required

#skills
Name: Agent Skills
Access: enabled
Operations: read
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
