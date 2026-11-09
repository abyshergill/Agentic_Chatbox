# Build Component

Title: Build Component

Description: Builds a new UI component or page, such as a form, modal dialog, table, card, navigation menu, tabs, dropdown, or full page, with semantic HTML, CSS that matches the project, and JavaScript as progressive enhancement. Covers every state, keyboard and screen reader support, and narrow screens. Use it when the user asks to create or add something new to the interface.

Allowed Paths: ./workspace/projects

## Instructions

You build new interface pieces that look native to the project, work with keyboard and screen readers, and handle real data.

### Rules

1. Stay inside `./workspace/projects`. If the user points to files outside this path, say so and ask for a path inside it.
2. Read before you change. Open every file you edit, and check `git_status` first so you never overwrite the user's uncommitted work.
3. Make focused, minimal changes. Edit only the lines that need to change and keep the rest of the file as it is. Do not reformat, reorder, or rewrite whole files unless the user asks.
4. Follow the project's existing conventions: framework, file layout, naming, code style, and lint or formatter settings.
5. Do not add a dependency without asking. Explain what it would save and its cost (size, maintenance) first.
6. Never commit, push, or switch branches unless the user asks. Never run destructive commands (deleting files or folders, `git reset --hard`, force push).
7. Reuse before you build. If the project already has a similar component, extend or reuse it instead of creating a second version.

### Step 1: Pin down the requirements

Confirm, or state your assumptions about:

* Where it goes: the page, template, or component file, and where in the layout.
* The data: what it shows, where the data comes from (template context, an API, static content), and realistic sizes (empty, one item, hundreds of items, very long text).
* Interactions: what the user can do and what happens next.
* States: loading, empty, error, success, and disabled.

If a missing answer would change the build a lot, ask one short question first.

### Step 2: Look for what exists

* Use `search_file_content` for similar components, class names, and templates.
* Note the conventions: file locations, naming, how templates are included (for example Django `{% include %}` or template tags), how scripts and styles are loaded, and the design tokens.

### Step 3: Build it in layers

1. HTML first: semantic and complete without JavaScript where practical. Use native elements: `button`, `dialog`, `details` and `summary`, `form` with built-in validation (`required`, `type`, `pattern`), and `table` with `th` and `scope`.
2. CSS: use the project's tokens and conventions, mobile first, and style every state (hover, `:focus-visible`, active, disabled, invalid, loading, empty).
3. JavaScript: add behavior as progressive enhancement, scoped to the component, with no global variables. Follow the JavaScript skill's rules for events, fetch, and security.

### Step 4: Accessibility patterns

* Dialogs: use the `dialog` element with `showModal()`. Move focus into the dialog when it opens, close it with Esc, and return focus to the trigger when it closes.
* Menus, dropdowns, and disclosures: a `button` with `aria-expanded` that is kept up to date.
* Tabs: follow the ARIA tabs pattern (`role="tablist"`, `role="tab"`, `role="tabpanel"`), with arrow key movement between tabs.
* Forms: a visible `label` for every field, errors linked with `aria-describedby`, and focus moved to the first error on submit.
* Dynamic updates: announce results such as "Saved" or "3 results" with an `aria-live` region.
* Use ARIA only when no native element does the job. Wrong ARIA is worse than none.

### Step 5: Integrate and verify

* Wire it into the page or template, and add any static files using the project's existing method.
* Open it with #browser. Test with real-looking data, every state, keyboard only, narrow screens (320 px), and 200% zoom. Check that the console has no new errors.
* Run the project's lint, tests, or build with #terminal if they exist.

### Step 6: Reply

1. What you built, in one or two sentences.
2. Files created and files changed, as two lists of exact paths, with a note on each.
3. How to use it: the include tag, import, or markup snippet, plus any data it expects.
4. Checks: what you tested and the results, and what you could not test.
5. Git status: the changes are uncommitted unless the user asked you to commit.
