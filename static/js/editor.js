// Middle pane: tabs, Monaco (bundled locally), saving, and diff previews.
import { $, api, el, emit, on, qs, session } from "./util.js";

const tabs = new Map(); // path -> { path, model, savedValue, mtime, truncated }
let active = null;
let editor = null;
let diffEditor = null;
let monacoReady = null;

const LANGUAGES = {
  py: "python", js: "javascript", ts: "typescript", html: "html", css: "css", md: "markdown", json: "json",
  rs: "rust", c: "c", h: "c", cpp: "cpp", sql: "sql", toml: "ini", yaml: "yaml", yml: "yaml", sh: "shell",
  docx: "markdown", doc: "markdown", rtf: "markdown", odt: "markdown",
};
const languageOf = (path) => LANGUAGES[path.split(".").pop().toLowerCase()] || "plaintext";

export const isDark = () => getComputedStyle(document.documentElement).colorScheme.includes("dark");

export function applyEditorTheme() {
  if (window.monaco) window.monaco.editor.setTheme(isDark() ? "workspace-dark" : "workspace-light");
}

function loadMonaco() {
  if (monacoReady) return monacoReady;
  monacoReady = new Promise((resolve) => {
    if (!window.require) return resolve(false);
    window.require.config({ paths: { vs: "/static/vendor/monaco/vs" } });
    window.require(["vs/editor/editor.main"], () => {
      const rules = [];
      window.monaco.editor.defineTheme("workspace-dark", {
        base: "vs-dark", inherit: true, rules,
        colors: { "editor.background": "#1b1f23", "editorGutter.background": "#1b1f23", "editor.lineHighlightBackground": "#22272c" },
      });
      window.monaco.editor.defineTheme("workspace-light", {
        base: "vs", inherit: true, rules,
        colors: { "editor.background": "#ffffff", "editor.lineHighlightBackground": "#f4f4f1" },
      });
      const options = {
        automaticLayout: true, minimap: { enabled: false }, fontSize: 12.5, scrollBeyondLastLine: false,
        fontFamily: "SF Mono, ui-monospace, Menlo, Consolas, monospace", padding: { top: 8 },
        theme: isDark() ? "workspace-dark" : "workspace-light",
      };
      editor = window.monaco.editor.create($("editor-host"), { ...options, model: null });
      diffEditor = window.monaco.editor.createDiffEditor($("diff-host"), { ...options, readOnly: true, renderSideBySide: false });
      editor.onDidChangeModelContent(() => updateChrome());
      editor.addCommand(window.monaco.KeyMod.CtrlCmd | window.monaco.KeyCode.KeyS, save);
      resolve(true);
    }, () => resolve(false));
  });
  return monacoReady;
}

const valueOf = (tab) => (tab.model ? tab.model.getValue() : tab.text);
const isDirty = (tab) => valueOf(tab) !== tab.savedValue;

function showSurface(name) {
  for (const [id, key] of [["editor-host", "editor"], ["diff-host", "diff"], ["editor-fallback", "fallback"], ["editor-empty", "empty"]]) {
    $(id).classList.toggle("hidden", key !== name);
  }
  $("diff-close").classList.toggle("hidden", name !== "diff");
}

function updateChrome() {
  const tab = tabs.get(active);
  $("editor-path").textContent = tab ? tab.path : "";
  $("save-file").disabled = !tab || !isDirty(tab) || tab.truncated;
  $("editor-state").textContent = !tab ? "" : tab.readonly ? "Read-only: text extracted from this document"
    : tab.truncated ? "Too large to edit: showing the start only" : isDirty(tab) ? "● Unsaved" : "Saved";
  renderTabs();
  emit("active-file", tab ? tab.path : null);
}

function renderTabs() {
  $("tabs").replaceChildren(...[...tabs.values()].map((tab) => {
    const name = tab.path.split("/").pop();
    return el("div", {
      class: `group flex shrink-0 cursor-pointer items-center gap-1.5 border-r border-line pl-3 pr-1.5 text-xs ${tab.path === active ? "tab-active" : "text-soft hover:bg-hover"}`,
      role: "tab", "aria-selected": String(tab.path === active), title: tab.path, tabindex: "0",
      onclick: () => activate(tab.path),
    },
      el("span", { text: name }),
      isDirty(tab) ? el("span", { class: "text-accent", "aria-label": "unsaved", text: "●" }) : null,
      el("button", {
        class: "flex h-4 w-4 items-center justify-center rounded text-faint hover:bg-hover hover:text-ink",
        "aria-label": `Close ${name}`, text: "✕",
        onclick: (event) => { event.stopPropagation(); close(tab.path); },
      }));
  }));
}

function activate(path, focus = true) {
  active = path;
  const tab = tabs.get(path);
  if (!tab) {
    showSurface("empty");
  } else if (tab.model) {
    showSurface("editor");
    editor.setModel(tab.model);
    editor.updateOptions({ readOnly: tab.truncated });
    if (focus) editor.focus();
  } else {
    showSurface("fallback");
    $("editor-fallback").value = tab.text;
    $("editor-fallback").readOnly = tab.truncated;
  }
  updateChrome();
}

function close(path) {
  const tab = tabs.get(path);
  if (tab && isDirty(tab) && !confirm(`Discard unsaved changes to ${path}?`)) return;
  tab?.model?.dispose();
  tabs.delete(path);
  activate(active === path ? [...tabs.keys()].pop() ?? null : active);
}

async function fetchFile(path) {
  return api(`/api/files/read?${qs({ session_id: session.id, path })}`);
}

export async function openFile(path, { focus = true } = {}) {
  if (tabs.has(path)) return activate(path, focus);
  let data;
  try {
    data = await fetchFile(path);
  } catch (error) {
    emit("notice", { level: "bad", message: `Could not open ${path}: ${error.message}` });
    return;
  }
  const hasMonaco = await loadMonaco();
  const tab = { path: data.path, savedValue: data.content, mtime: data.mtime, truncated: data.truncated, readonly: Boolean(data.readonly), text: data.content, model: null };
  if (hasMonaco) tab.model = window.monaco.editor.createModel(data.content, languageOf(data.path));
  tabs.set(data.path, tab);
  activate(data.path, focus);
}

async function save() {
  const tab = tabs.get(active);
  if (!tab || tab.truncated) return;
  const content = valueOf(tab);
  try {
    const result = await api("/api/files/write", {
      json: { session_id: session.id, path: tab.path, content, expected_mtime: tab.mtime },
    });
    tab.savedValue = content;
    tab.mtime = result.mtime;
    updateChrome();
    emit("files-changed");
  } catch (error) {
    if (error.status === 409 && !confirm("This file changed on disk after you opened it. Overwrite those changes?")) return;
    if (error.status === 409) {
      tab.mtime = null;
      return save();
    }
    emit("notice", { level: "bad", message: `Could not save ${tab.path}: ${error.message}` });
  }
}

// Reload tabs the assistant changed. Your unsaved edits are never overwritten.
async function reloadChanged(paths) {
  for (const path of paths) {
    const tab = tabs.get(path);
    if (!tab) continue;
    if (isDirty(tab)) {
      emit("notice", { level: "warn", message: `${path} changed on disk, but your unsaved edits were kept.` });
      continue;
    }
    try {
      const data = await fetchFile(path);
      tab.savedValue = data.content;
      tab.mtime = data.mtime;
      tab.text = data.content;
      tab.model?.setValue(data.content);
      if (path === active && !tab.model) $("editor-fallback").value = data.content;
    } catch {
      tabs.delete(path); // Deleted or moved.
      if (active === path) activate([...tabs.keys()].pop() ?? null);
    }
  }
  updateChrome();
}

export async function showDiff(path, proposed) {
  const hasMonaco = await loadMonaco();
  if (!hasMonaco) return false;
  let original = "";
  try { original = (await fetchFile(path)).content; } catch { /* A new file has no original. */ }
  const language = languageOf(path);
  const previous = diffEditor.getModel();
  diffEditor.setModel({
    original: window.monaco.editor.createModel(original, language),
    modified: window.monaco.editor.createModel(proposed, language),
  });
  previous?.original.dispose();
  previous?.modified.dispose();
  showSurface("diff");
  $("editor-path").textContent = `Proposed change: ${path}`;
  emit("show-pane", "pane-editor");
  return true;
}

export const activeFile = () => active;

// The explorer renamed or moved a file or folder: open tabs follow it, unsaved edits included.
function renamedOnDisk({ from, to }) {
  const moved = [...tabs.values()].filter((tab) => tab.path === from || tab.path.startsWith(`${from}/`));
  if (!moved.length) return;
  for (const tab of moved) {
    tabs.delete(tab.path);
    const wasActive = tab.path === active;
    tab.path = to + tab.path.slice(from.length);
    if (tab.model) window.monaco.editor.setModelLanguage(tab.model, languageOf(tab.path));
    tabs.set(tab.path, tab);
    if (wasActive) active = tab.path;
  }
  updateChrome();
}

// The explorer moved a file to the Trash. Tabs without edits close; tabs with unsaved edits stay so nothing is lost.
function deletedOnDisk(path) {
  for (const tab of [...tabs.values()]) {
    if (tab.path !== path && !tab.path.startsWith(`${path}/`)) continue;
    if (isDirty(tab)) {
      tab.mtime = null; // Saving recreates the file.
      emit("notice", { level: "warn", message: `${tab.path} was deleted, but its tab has unsaved edits. Save to recreate it, or close the tab.` });
      continue;
    }
    tab.model?.dispose();
    tabs.delete(tab.path);
    if (active === tab.path) active = [...tabs.keys()].pop() ?? null;
  }
  activate(active, false);
}

export function currentSelection() {
  if (!editor || !active || !editor.getModel()) return null;
  const range = editor.getSelection();
  if (!range || range.isEmpty()) return null;
  return editor.getModel().getValueInRange(range);
}

export function initEditor() {
  showSurface("empty");
  $("save-file").addEventListener("click", save);
  $("diff-close").addEventListener("click", () => activate(active));
  $("editor-fallback").addEventListener("input", () => {
    const tab = tabs.get(active);
    if (tab) { tab.text = $("editor-fallback").value; updateChrome(); }
  });
  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "s") { event.preventDefault(); save(); }
  });
  window.addEventListener("beforeunload", (event) => {
    if ([...tabs.values()].some(isDirty)) event.preventDefault();
  });
  // The explorer sends { path, focus: false } so a single click keeps keyboard focus in the file list.
  on("open-file", (detail) => {
    const { path, focus = true } = typeof detail === "string" ? { path: detail } : detail;
    openFile(path, { focus });
    emit("show-pane", "pane-editor");
  });
  on("path-renamed", renamedOnDisk);
  on("path-deleted", deletedOnDisk);
  on("paths-changed", reloadChanged);
  loadMonaco();
}
