# Local AI Workspace

A local AI agent workspace: chat with a model, and it reads and edits your files, runs tests and programs, and opens
applications for you, within permissions you set. Models run on your machine through [Ollama](https://ollama.com) or
straight from a `.gguf` file, so nothing leaves your computer. You can also add a hosted model through an API; see
[Models](#models).

- **Projects and data analysis:** open a project from the left bar, ask about any file, and have the Data Analyst answer
  questions, clean and merge data, and build dashboards, Excel workbooks, and presentations from CSV, TSV, JSON, and
  Excel files. See [Working with projects and data](#working-with-projects-and-data).
- **Three panes:** file explorer, Monaco code editor, and AI chat with live tool activity.
- **Agents and skills in Markdown:** add one by adding files to `arch/`, with no Python changes.
- **Permissions enforced in Python:** `arch/permission.md` decides what the AI can touch, and the AI cannot edit it.
- **You approve actions:** changes wait for your approval, with a diff preview. Every request is logged.

## Requirements

- macOS or Linux (see [Security model](#security-model): command sandboxing is macOS only)
- Python 3.11 or newer
- A model: [Ollama](https://ollama.com), a `.gguf` file (see [Models](#models)), or an API key for a hosted model

## Setup

```bash
# 1. Download a model (any model with tool support works)
ollama pull qwen2.5-coder:3b

# 2. Install the application
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Start it as a desktop app (its own window; starts Ollama for you if it is installed)
python desktop.py
```

### Desktop app

`python desktop.py` opens the workspace in its own native window instead of a browser tab. On top of the browser
version, it:

- **Opens generated files in their own app:** click a `.pptx`, PDF, Word, Excel, or image file in the explorer (or
  its **Open** button) to open it in Keynote, PowerPoint, Preview, and so on. **Finder** shows any file in Finder.
- **Starts Ollama** if it is installed but not running, and stops it again on exit if it started it.
- Has a **Workspace** menu: open the workspace or logs folder, or reload the window.
- Keeps your layout between launches, and picks a free port, so it never clashes with a browser instance.

To start it from Launchpad, Spotlight, or the Dock, run once:

```bash
./scripts/make_mac_app.sh
```

This creates `~/Applications/Local AI Workspace.app`, a small launcher that runs `desktop.py` from this folder, so code
changes apply on the next launch. Run it again if you move the project. Output goes to `logs/desktop.log`.

### Browser version

`python main.py` serves the same app at <http://127.0.0.1:8000> for any browser. The server only listens on this machine.

Skills do not name a model. Pick one from the dropdown in the chat pane; on **Automatic**, the app uses the first
installed model that supports tool calls. Small models make more mistakes and sometimes describe an action instead of
taking it, so pull a larger model for real work.

## Using the chat

| You type | What happens |
| --- | --- |
| `Create a script that sums this CSV` | Plain language. The app picks a skill by matching words in your message, or uses the default skill. |
| `/db-migration Plan a move to PostgreSQL` | Activates that skill's agent and paths. It stays active until you type `/auto`. |
| `#terminal Run the test suite` | Limits this message to that application, plus read-only file access. |
| `#git status` | Names an operation directly, so it runs immediately without the model. |
| `Summarise @notes.md and @report.pdf` | `@` opens a file picker. Each file you pick is read and given to the assistant with that message (folders give their file list). |

Type `/`, `#`, or `@` to see what's available.

Two checkboxes above the message box change how the assistant works:

- **Allow all actions in \<project\>**: actions whose files are all inside the active project run without asking.
  That covers edits, new files, renames, moves to the Trash, data and document outputs, and commands. Each auto-approved
  action runs with its folders narrowed to the project, so a command's sandbox can write only there. Anything that touches
  a path outside the project, the browser, VS Code, or another skill still asks. Disabled applications stay disabled, and
  secret files and `arch/` stay blocked. Each auto-approved action is marked on its card and in the audit log
  (`auto_project`).
- **Plan mode**: the assistant may only look (read files, inspect data, git status and diff) and replies with a
  numbered plan of the changes it would make. Untick it and say "go ahead" to carry the plan out.

The **active project** is the top folder of whatever you last selected in the explorer or opened in the editor, shown at
the bottom of the explorer. Files directly in `workspace/projects/` belong to `projects` itself. Under each reply, a backend-generated line shows what really ran:
**Confirmed by the backend: 2 succeeded · 1 rejected**, or **No tools were run for this reply**. Trust that line over
the model's wording. Models can claim things they did not do.

Try it with the bundled project in `workspace/projects/sample_app`. Put your own projects in `workspace/projects/`.

## Working with projects and data

- **Projects:** each folder in `workspace/projects/` is a project. Create one with **New Folder** on `projects`. Click a
  file or folder to make its project active.
- **Files and folders:** the explorer works like VS Code's:

  | Action | Mouse | Keyboard (file list focused) |
  | --- | --- | --- |
  | Open a file / toggle a folder | Click | Space, or ⌘↓ (Enter on Windows/Linux) |
  | New file or folder | Header buttons, or right-click > New File… / New Folder… | |
  | Rename | Right-click > Rename… | Enter on macOS, F2 everywhere |
  | Delete (to the Trash) | Right-click > Delete | ⌘⌫ or Delete |
  | Cut, copy, paste, duplicate | Right-click menu | ⌘X, ⌘C, ⌘V |
  | Move | Drag onto a folder (hold ⌥ to copy) | Cut and paste |
  | Select several | ⌘-click, Shift-click | Shift+↑/↓, ⌘A |
  | Move around | | ↑ ↓, → expands, ← collapses or goes to the parent, Home, End |
  | Copy path | Right-click > Copy Relative Path (Copy Path in the desktop app) | ⌥⇧⌘C, ⌥⌘C |

  Names are checked as you type. A new file can include folders (`src/utils/helpers.py` creates `src/utils/`). Open
  editor tabs follow a rename or move, and tabs with unsaved edits are kept if their file is deleted. Folder state is
  remembered, and the file open in the editor is highlighted in the tree. Drag files or whole folders from Finder onto
  a folder to upload them; allowed types are set in `arch/permission.md` under `#uploads`. Rename, delete, and create
  follow the `#filesystem` operations in `arch/permission.md`, and every change is written to the audit log.
- **Ask about a file:** hover a file in the explorer and click **Ask**. A chip appears above the chat box and the
  assistant sees that file. For data files the box is pre-filled with `/analyze `.
- **Analyse data:** `/analyze Which region earns the most revenue?` uses the Data Analyst. It profiles the file, then
  computes every figure with `query_data` (group, filter, aggregate, histogram, correlation) instead of guessing.
- **Deeper analysis:** these skills use `analyze_data`, which computes quality, outlier, correlation, pivot, trend,
  group-comparison, and distribution reports:

  | Command | What it does |
  | --- | --- |
  | `/data-quality` | Missing values, duplicate rows, non-numeric values in numeric columns, spelling variants, outliers |
  | `/stats` | Percentiles, spread, skew, outliers, and a correlation matrix |
  | `/trends` | Change per day, week, month, quarter, or year, with direction, peaks, and dips |
  | `/segments` | Compares groups by count, total, share, mean, median, and spread, plus pivot tables |

- **Build a dashboard:** `/dashboard Build a dashboard for data/orders.csv` writes `dashboards/<name>.html` with summary
  numbers and charts, each with a one-line caption computed from the data. Chart kinds: bar, line, area, pie, donut,
  histogram, scatter, box plot, heatmap (two categories, or a correlation matrix), stacked bar, grouped bar, and treemap.
  You approve it first, then it opens in the editor pane. Ask for specific charts in plain words, for example "a box plot
  of revenue by region". Click any `.html` file and use **Preview** to view one you made earlier.
- **Build a presentation:** `/presentation Make a deck about data/orders.csv` writes `presentations/<name>.html` and
  `presentations/<name>.pptx`. The deck has a title slide, an overview, one slide per chart, key findings, and notes about
  the data. The HTML deck opens with **Preview** and prints to PDF one slide per page. The `.pptx` opens in PowerPoint,
  Keynote, or Google Slides. Its bar, line, area, pie, donut, scatter, stacked, and grouped charts are editable;
  box plots, heatmaps, and treemaps are drawn shapes or tables.

- **CSV and Excel:** every data tool reads `.csv`, `.tsv`, `.json`, `.jsonl`, and Excel `.xlsx` files. For a workbook
  with several sheets, name the sheet ("use the Q3 sheet"). These skills write a **new** file and never change the
  original:

  | Command | What it does |
  | --- | --- |
  | `/clean-data` | Runs a quality check, then trims spaces, merges spelling variants, fixes types, removes duplicates, and fills or drops missing values |
  | `/transform` | Filters, sorts, selects and renames columns, adds calculated columns (`total = units * unit_price`), extracts year, month, or quarter from dates, groups and summarises, or converts between CSV, TSV, JSON, and Excel |
  | `/merge-data` | Joins two files on a key column, like VLOOKUP (left, inner, right, or outer), or stacks files with the same columns |
  | `/excel` | Builds a formatted `.xlsx` report in `reports/`: a Summary sheet, the data as a filterable Excel table, pivot, group, trend, and quality sheets, native Excel charts, and optionally one sheet per region or customer. Totals and changes are Excel formulas, so they stay live |

  Calculated columns use a small formula language (numbers, column names, `+ - * / // % **`, and `round`, `abs`, `min`,
  `max`, `sqrt`, `log`). It is parsed, never run as Python.

Analysis runs inside the app, so no model-written code is executed. Dashboards are plain HTML and SVG with no scripts,
and the preview frame is sandboxed. Limits: files up to 25 MB and 200,000 rows.

## Documents

`read_file` reads PDF, Word (`.docx`, and `.doc`, `.rtf`, `.odt` on macOS), Excel, HTML, Markdown, CSV, and text, so
every skill can work with documents. You can also attach or upload them. Clicking a PDF or Word file shows its text
read-only in the editor; in the desktop app it opens in its own app.

| Command | What it does |
| --- | --- |
| `/write-doc` | Writes a Word document (report, letter, proposal, memo, guide, notes) from your request and any files you name |
| `/summarize` | Summarises a document or answers questions about it, citing pages; compares documents in a table |
| `/data-report` | Analyses a CSV or Excel file and writes the findings as a Word report with tables |

Documents are saved as `.docx` (in `<project>/documents/` unless you name a path) and open in Word, Pages, or Google
Docs. Headings, lists, tables, quotes, code, bold, italics, and page breaks are formatted.

## Testing

| Command | What it does |
| --- | --- |
| `/write-tests` | Reads the code and existing tests, writes tests for normal cases, edge cases, and errors, then runs them |
| `/fix-tests` | Runs the suite, decides whether the code or the test is wrong, makes the smallest fix, and reruns until it passes |

## Analysing code

The **Code Analyst** agent reads code and never changes it: it is marked `Read Only: yes`, so it is only offered tools
that look (list, read, search, git status and diff).

- `/explain-code How does the checkout flow work?` explains the structure and flow with `file:line` references, and
  points out risks.
- `/review` reviews your uncommitted changes (git diff) for bugs and security problems before you commit.
- `/why-not Why can't the assistant push to GitHub?` finds the real blocker and how to change it. It first calls
  `describe_capabilities`, which reports what `permission.md` allows right now, which tools exist, the active skill's
  folders, file-type and size limits, and which limits are deliberate. For a project, it reads the code to find the
  missing feature, hard-coded limit, or failing check, and cites the file and line.

## Models

Pick a model in the chat pane's **Model** menu, grouped by source. Manage sources with **Models** in the header.
**Automatic (local)** uses a model on your computer and never picks a remote one.

| Source | How to add it | Data leaves your computer? |
| --- | --- | --- |
| Ollama | `ollama pull <model>`. It appears automatically | No |
| `.gguf` file | **Models > Choose .gguf file…** (desktop app), paste its path, or drop it into `models/` | No |
| API on this computer (LM Studio, vLLM, llama.cpp server) | **Models > Add API provider**, address `http://127.0.0.1:…/v1` | No |
| Hosted API (OpenAI, Anthropic, Gemini, Groq, OpenRouter, Mistral, DeepSeek, any OpenAI-compatible service) | **Models > Add API provider**: pick a preset, paste your key, then **Find models** | **Yes**, marked *remote* |

**`.gguf` files** run with llama.cpp in a private server on `127.0.0.1` that the app starts when you first send a
message and stops when you pick another file, click **Unload**, or quit. It needs one llama.cpp runtime:

```bash
# Inside .venv. Builds llama.cpp with Apple GPU support (a few minutes):
CMAKE_ARGS="-DGGML_METAL=on" pip install -r requirements-gguf.txt
# Or install llama.cpp's own server, which the app prefers when found (native tool calling):
brew install llama.cpp
```

With `llama-cpp-python`, tools are described in the prompt rather than passed natively, which works but is less reliable
with small models. The server log is in `logs/gguf-server.log`. The context size follows `WORKSPACE_NUM_CTX`.

**Hosted models** receive everything in the conversation, including the contents of any file the assistant reads.
Choosing one shows a warning. Keys are saved in `data/models.json` (readable only by you, mode 600, git-ignored) and are
never sent back to the page or offered to the assistant. Remote addresses must use `https://`. If a model answers with
raw JSON instead of acting, edit the provider and turn off **native tool calling**.

## Configuration

Everything is in `arch/`. Files are re-read on each request, so edits apply without a restart. The explorer shows
`arch/` at the bottom (**arch · agents & skills**), so you can open and edit any agent or skill right in the app. The
assistant's own tools still cannot read or change it.

```
arch/
├── system.md        Identity, default skill, step limit, global rules
├── command.md       Maps /commands to skill files
├── permission.md    What the AI may use: the file that matters most
└── agents/
    └── backend_dev/
        ├── agent.md          Name, description, and the applications this agent may use
        └── skills/
            └── db_migration.md
```

### Adding a skill

Create the file. Its details are read from the **first 8 non-blank lines**, in any order, and a `# Heading` and blank
lines are allowed. The header ends at the first other line (such as `## Instructions`); everything after it is the
skill's instructions. A skill missing a title or description is listed as a warning and cannot be activated.

| Field | Required | Notes |
| --- | --- | --- |
| `Title:` | yes | `Name:` or the `# Heading` also work |
| `Description:` | yes | Also used to route plain messages to the skill |
| `Allowed Paths:` | no | Comma separated. Default: `./workspace/projects` |
| `Model:` | no | Pins a model; otherwise the one picked in the chat pane is used |

```
# Database Migration

Title: Database Migration

Description: Handles database schema changes and migrations.

Allowed Paths: ./workspace/projects, ./workspace/database

## Instructions

You change database schemas safely.
...
```

Then register it in `arch/command.md`:

```
/db-migration -> agents/backend_dev/skills/db_migration.md
```

### Adding an agent

Create `arch/agents/<name>/agent.md` and list it in `system.md`. `Applications:` is the most this agent can use, and
`permission.md` can still deny any of it.

```
Name: Ops Engineer
Description: Deployment and infrastructure.
Capabilities: Docker, CI
Applications: #filesystem, #terminal, #git

You are a careful operations engineer.
```

Add `Read Only: yes` under `Applications:` to offer the agent only tools that look (list, read, search, status, diff,
profile, query, analyze, describe), whatever `permission.md` allows.

### Your own folders and where generated files go

Use **Open Folder** (the folder icon at the top of the explorer) to add any folder on your computer. In the desktop
app this opens the system folder picker; in a browser, type the full path, and tick the box to create it if needed.
Opened folders are remembered, every skill can use them in addition to its `Allowed Paths`, and right-clicking one offers
**Remove Folder from Explorer** (nothing on disk is deleted).

The folder selected in the explorer is where the assistant saves what it generates. If you attach a file in the chat
with no folder selected, you are asked where to save: pick a folder, create a new one, open another folder, or choose
**Attach Only**. The attached file is copied there, so the data and document tools can work on it.

### File formats

| Kind | Formats | Used by |
| --- | --- | --- |
| Data tables | `.csv` (comma, semicolon, tab or pipe), `.tsv`, `.tab`, `.psv`, `.txt`/`.dat` tables, `.json`, `.jsonl`/`.ndjson`, Excel `.xlsx`/`.xlsm`/`.xls`, OpenDocument `.ods`, `.parquet` | `#data` tools: profile, query, analyze, dashboards, presentations, workbooks |
| Documents | PDF, Word `.docx`/`.doc`/`.rtf`, OpenDocument `.odt`/`.odp`, PowerPoint `.pptx`, Jupyter `.ipynb`, HTML | `read_file`, attachments, the editor (read-only) |
| Text | Markdown, code, logs, XML, YAML, TOML, INI... | Everything |

Text is read in any common encoding: UTF-8, UTF-16 (Excel's "Unicode Text"), and Windows-1252 (Excel's "CSV" on
Windows), so accented letters come through intact. A data file attached to the chat can be analyzed directly by its
name, and relative paths such as `orders.csv` or `data/orders.csv` are looked up in the selected project first. `.xls`,
`.ods`/`.odp` and `.parquet` need `xlrd`, `odfpy` and `pyarrow` (in `requirements.txt`).

### Chat history

Conversations are saved to SQLite in `data/workspace.db` after every reply. **History** in the chat pane lists them:
click one to continue it, or rename or delete it. **New chat** starts a fresh conversation and keeps the old one in
History. The app reopens your last conversation when it starts.

### Changing permissions

Edit `arch/permission.md`. For example, to turn on the browser, set `Access: enabled` under `#browser`.

```
#git
Name: Git
Executable: git
Access: enabled
Operations: status, diff, add, commit
Approval: required
Approval Exempt: status, diff
```

| Field | Meaning |
| --- | --- |
| `Access` | `enabled` or `disabled`. Anything else counts as disabled. |
| `Operations` | The only operations allowed. |
| `Approval` | `required` or `not_required`. Anything else counts as required. |
| `Approval Exempt` | Operations that skip approval. Deleting always asks. |
| `Allowed Commands` | `#terminal` only: the programs it may run. |
| `Extra Paths` | `#terminal` only: tool folders outside the system PATH, such as `~/.cargo/bin`. |
| `Timeout Seconds` | Time limit for a process. |
| `Roots` | `#filesystem` only: no skill can reach beyond these folders. |
| `Extensions`, `Max Size MB` | `#uploads` only: what can be attached. |

If you installed Node with nvm, `node` and `npm` won't be found until you add its folder to `#terminal`:
`Extra Paths: ~/.nvm/versions/node/<version>/bin`.

### Adding an application integration

Add a module to `core/adapters/` that defines `adapter = Adapter(app_id=..., tools=[...])`, and a matching `#block` in
`permission.md`. It is discovered automatically. See `core/adapters/git.py` for a short example.

## Available tools

| Application | Tools |
| --- | --- |
| `#filesystem` | `list_directory`, `read_file`, `write_file`, `create_file`, `create_directory`, `delete_file`, `rename_file`, `search_files`, `search_file_content`, `read_attachment` |
| `#terminal` | `execute_command`, `run_project_tests` |
| `#data` | `profile_data`, `query_data`, `analyze_data`, `create_dashboard`, `create_presentation`, `transform_data`, `merge_data`, `create_excel` |
| `#introspect` | `describe_capabilities`: reports what the assistant may do and why (read-only) |
| `#documents` | `create_document`: writes Word `.docx` files |
| `#python` | `execute_python` |
| `#git` | `git_status`, `git_diff`, `git_add`, `git_commit` |
| `#vscode` | `open_project`, `open_file` (offered when you use `#vscode` or mention VS Code) |
| `#browser` | `open_url` (disabled by default; offered when you use `#browser` or mention a browser) |
| `#delegate` | `delegate_task`: hands work to another skill, under that skill's permissions |

`#system` (shutdown, restart, install package) is defined in `permission.md` but disabled and has no integration on
purpose. Enabling it does nothing until someone writes an adapter.

## Security model

These checks run in Python on every action, whatever the model says:

- **Paths:** symlinks are resolved first, then the path must be inside the skill's `Allowed Paths` (clamped to the
  global `Roots`) or a folder you opened in the explorer. App internals (`arch/`, `logs/`, `core/`, and so on), `.git`, and secret-looking files
  (`.env`, keys, `credentials*`) are always blocked.
- **Terminal:** no shell. Commands run directly from an allowlist, so pipes and redirects do nothing. Shells, `sudo`, and
  network tools are refused even if you add them to the list. The environment is scrubbed and processes have a timeout.
- **Sandbox (macOS):** commands, Python scripts, and git run under `sandbox-exec`, with no network, writes only inside the
  skill's roots, and no reading your home folder or secret files.
- **Approval:** the action is re-checked after you approve it, so revoking a permission while a request waits blocks it.
- **Secrets:** credential-shaped text is redacted from anything sent to the model or written to the log.
- **Browser attacks:** requests from other websites and foreign `Host` headers are refused, so a page you visit can't
  call this API or approve actions.
- **Uploads:** extension, content, and size are checked. Files are stored outside the workspace, without execute
  permission.
- **Models:** the `.gguf` server listens only on `127.0.0.1`. API keys stay in `data/models.json`, outside every folder
  the assistant can reach. A remote model is used only when you choose it.

### Known limits

- **Linux has no OS sandbox.** The other checks still apply, but an approved Python script or test run has your user's
  full rights. The header shows **No sandbox**. On Linux, read scripts carefully before approving them.
- **Approved code is real code.** On macOS the sandbox contains it, but it can still change or delete anything inside the
  skill's folders. Keep your projects in version control.
- **Secret detection works on patterns.** Unusual file names or credential formats can get through, so don't keep
  production secrets in the workspace.
- **`#vscode` and `#browser` run outside the sandbox,** because desktop apps can't start inside it. They only receive a
  validated path or `http(s)` URL.
- **The redactor can show `[REDACTED]` in text the model reads.** It only affects what the model sees, not your files.
- **Conversations are kept in memory** and are lost when the server restarts. Uploaded files and the audit log remain.
- **A hosted API model sees what the assistant sees.** Redaction removes credential-shaped text, but file contents and
  your messages go to that provider. Use a local model for confidential data.

## Audit log

Every tool request is appended to `logs/audit.jsonl`, whether it ran or not, with the time, session, agent, skill, model,
operation, target, permission decision, approval status, outcome, and result. The **Audit history** tab shows the current
conversation.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Tests use a scripted model and a temporary copy of `arch/`, so they need neither Ollama nor your real configuration.

### Rebuilding styles

Monaco and the compiled CSS are committed, so the app needs no internet or Node to run. To change styles, edit
`static/css/input.css` or use new Tailwind classes, then rebuild:

```bash
npx tailwindcss@3.4.17 -c tailwind.config.js -i static/css/input.css -o static/css/app.css --minify
```

## Project layout

```
main.py              FastAPI app and routes (browser version)
desktop.py           Native desktop window around the same app
index.html           The interface
core/
├── agent.py         Agent loop; the only path to running a tool
├── arch_loader.py   Parses agents, skills, and commands
├── permissions.py   Parses permission.md and makes decisions
├── dataviz.py       Data loading, statistics, and SVG charts and dashboards
├── analysis.py      Quality, outlier, correlation, pivot, trend, and comparison reports
├── presentation.py  HTML slide decks and PowerPoint export
├── fs_guard.py      Path validation
├── process.py       Sandboxed process runner
├── ollama_client.py Ollama streaming and tool-call parsing
├── openai_client.py OpenAI-compatible APIs (hosted providers and the .gguf server)
├── models.py        Model router: Ollama, .gguf files (llama.cpp), API providers
├── datatools.py     Clean, transform, merge, and convert data; safe formulas
├── excel.py         Excel workbooks: exports and formatted reports
├── documents.py     Reads PDF, Word, Excel, HTML as text; writes Word documents
├── routing.py       /skill, #app, and plain-language routing
├── tools.py         Tool model and registry
├── uploads.py       Attachment validation and storage
├── redact.py        Secret redaction
├── audit.py         Audit log
└── adapters/        One module per integration
static/              JavaScript, CSS, and bundled Monaco
arch/                Your configuration
workspace/projects/  Where your projects live
tests/               Test suite
```

## License

Apache 2.0. See [license.txt](license.txt). Monaco Editor is MIT licensed: see `static/vendor/monaco/LICENSE.txt`.
