// Left pane: a VS Code-style explorer. The backend decides what is visible and checks every change.
//
// Mouse: click opens a file or toggles a folder; Cmd/Ctrl+click and Shift+click select several; right-click for the menu;
// drag items onto a folder to move them (hold Option/Alt to copy); drop files from Finder to upload.
// Keyboard (when the file list has focus): arrows move, Right/Left expand/collapse, Enter renames on macOS (opens elsewhere),
// F2 renames, Space or Cmd+Down opens, Cmd+Backspace or Delete moves to Trash, Cmd+C/X/V copy, cut and paste.
import { $, api, el, emit, on, qs, session, store } from "./util.js";
import { absolutePath, isDesktop, openNative, opensNatively, revealNative } from "./desktop.js";
import { closeFolder, openFolder } from "./folders.js";

const isMac = /Mac|iPhone|iPad/.test(navigator.platform);
const KEYS = isMac
  ? { cut: "⌘X", copy: "⌘C", paste: "⌘V", rename: "↵", del: "⌘⌫", copyPath: "⌥⌘C", copyRel: "⌥⇧⌘C" }
  : { cut: "Ctrl+X", copy: "Ctrl+C", paste: "Ctrl+V", rename: "F2", del: "Delete", copyPath: "Shift+Alt+C", copyRel: "Ctrl+Shift+Alt+C" };
const isDashboard = (path) => /(^|\/)(dashboards|presentations)\/[^/]+\.html$/i.test(path);
const DRAG_TYPE = "application/x-workspace-paths";
const INDENT = 12;

const ICONS = {
  py: ["py", "#3f7fbf"], js: ["js", "#b8960c"], ts: ["ts", "#3178c6"], html: ["<>", "#d9622b"], css: ["#", "#7a5fc7"],
  md: ["md", "#6f777f"], json: ["{}", "#a08a24"], rs: ["rs", "#b5653a"], c: ["c", "#55789c"], h: ["h", "#55789c"],
  cpp: ["c+", "#55789c"], sql: ["db", "#2c8f7f"], csv: [",", "#3a9460"], toml: ["tm", "#6f777f"], yaml: ["ym", "#6f777f"],
  yml: ["ym", "#6f777f"], txt: ["tx", "#6f777f"], pdf: ["pd", "#c0392f"], sh: ["$", "#3a9460"], pptx: ["pp", "#c35a2a"],
  docx: ["dc", "#2b5797"], xlsx: ["xl", "#1f7244"], png: ["im", "#7a5fc7"], jpg: ["im", "#7a5fc7"], svg: ["sv", "#d9622b"],
};

// ---- state ------------------------------------------------------------------

let activeProject = null; // The project the assistant works in, derived from what is selected or open.
let topEntries = []; // Entries shown at depth 0.
let rows = []; // Visible rows in display order: { entry, depth }.
let byPath = new Map(); // path -> entry, for visible rows.
let searchMode = false;
const children = new Map(); // folder path -> entries
const expanded = new Set(readJSON("workspace.expanded", []));
const selection = new Set();
let focused = null; // The keyboard cursor.
let anchor = null; // Where Shift+click / Shift+arrow ranges start.
let clipboard = null; // { mode: "copy" | "cut", paths }
let editing = null; // { kind: "rename", path } | { kind: "file" | "folder", parent }
let committing = false;
let dragPaths = null;
let uploadTarget = null;
let lastRevealed = null;

function readJSON(key, fallback) {
  try { return JSON.parse(store.get(key) || "null") ?? fallback; } catch { return fallback; }
}
const saveExpanded = () => store.set("workspace.expanded", JSON.stringify([...expanded].slice(-400)));
const parentOf = (path) => path.split("/").slice(0, -1).join("/");
const baseName = (path) => path.split("/").pop();
const isUnder = (path, dir) => path === dir || path.startsWith(`${dir}/`);
const notify = (level, message) => emit("notice", { level, message });
// The explorer always shows the whole workspace, like a VS Code workspace; there is no narrower root.
const rootDir = () => null;
const tree = () => $("tree");

// ---- loading ----------------------------------------------------------------

async function listDir(path) {
  const data = await api(`/api/files/tree?${qs({ session_id: session.id, path })}`);
  children.set(path, data.entries);
  return data.entries;
}

async function loadTop() {
  const data = await api(`/api/files/tree?${qs({ session_id: session.id })}`);
  topEntries = data.entries;
  if (!expanded.size && topEntries[0]) expanded.add(topEntries[0].path);
}

// Loads the children of every expanded folder that is visible, level by level.
async function loadExpanded(entries) {
  const folders = entries.filter((entry) => entry.type === "dir" && expanded.has(entry.path));
  await Promise.all(folders.map(async (folder) => {
    let list = children.get(folder.path);
    if (!list) {
      try { list = await listDir(folder.path); } catch { expanded.delete(folder.path); return; }
    }
    await loadExpanded(list);
  }));
}

export async function refreshTree() {
  const query = $("file-search").value.trim();
  searchMode = Boolean(query);
  try {
    if (searchMode) {
      const data = await api(`/api/files/search?${qs({ session_id: session.id, q: query })}`);
      topEntries = data.entries.map((entry) => ({ ...entry, name: entry.path }));
    } else {
      children.clear();
      await loadTop();
      await loadExpanded(topEntries);
    }
    render();
  } catch (error) {
    tree().replaceChildren(el("div", { class: "px-3 py-2 text-xs text-bad", text: error.message }));
  }
}

async function reloadAll() {
  await refreshTree();
  if (activeProject && !(await folderExists(activeProject))) setActiveProject(null);
}

async function folderExists(path) {
  try { await api(`/api/files/tree?${qs({ session_id: session.id, path })}`); return true; } catch { return false; }
}

// ---- rendering --------------------------------------------------------------

function buildRows() {
  rows = [];
  byPath = new Map();
  const walk = (entries, depth) => {
    for (const entry of entries) {
      rows.push({ entry, depth });
      byPath.set(entry.path, entry);
      if (!searchMode && entry.type === "dir" && expanded.has(entry.path)) walk(children.get(entry.path) || [], depth + 1);
    }
  };
  walk(topEntries, 0);
}

function fileIcon(name) {
  const [label, color] = ICONS[name.split(".").pop().toLowerCase()] || ["··", "#868b90"];
  return el("span", { class: "w-4 shrink-0 text-center font-mono text-[9px] font-bold", style: `color:${color}`, "aria-hidden": "true", text: label });
}

function chevron(isOpen) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 12 12");
  svg.setAttribute("width", "12");
  svg.setAttribute("height", "12");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", isOpen ? "M3 4.5 6 7.5 9 4.5" : "M4.5 3 7.5 6 4.5 9");
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "currentColor");
  path.setAttribute("stroke-width", "1.5");
  svg.append(path);
  return svg;
}

function guides(depth) {
  return Array.from({ length: depth }, (_, i) => el("span", { class: "ex-guide", style: `left:${8 + i * INDENT + 7}px` }));
}

function rowNode(entry, depth, index) {
  const path = entry.path;
  const isDir = entry.type === "dir";
  const classes = ["ex-row"];
  if (selection.has(path)) classes.push("selected");
  if (focused === path) classes.push("focused");
  if (clipboard?.mode === "cut" && clipboard.paths.includes(path)) classes.push("cut");
  const node = el("div", {
    id: `ex-row-${index}`, class: classes.join(" "), role: "treeitem", "aria-level": String(depth + 1),
    "aria-selected": String(selection.has(path)), "aria-expanded": isDir ? String(expanded.has(path)) : null,
    "data-path": path, "data-type": entry.type, draggable: "true", title: entry.title || path,
    "data-root": entry.root || null,
    style: `padding-left:${8 + depth * INDENT}px`,
  }, guides(depth), el("span", { class: "ex-twisty" }, isDir ? chevron(expanded.has(path)) : null));
  if (!isDir) node.append(fileIcon(entry.name));
  node.append(el("span", { class: "ex-name", text: entry.name }));
  if (entry.root === "folder") node.append(el("span", { class: "ex-badge", title: "A folder you opened", text: "opened" }));
  if (!isDir) {
    node.append(el("span", { class: "ex-actions" },
      opensNatively(path) ? el("button", { class: "ask-btn", "data-action": "native", title: "Open in its default app", text: "Open" }) : null,
      el("button", { class: "ask-btn", "data-action": "ask", title: "Ask the assistant about this file", text: "Ask" })));
  }
  return node;
}

function editRow(edit, depth, entry) {
  const isDir = edit.kind === "folder" || entry?.type === "dir";
  const input = el("input", {
    class: "ex-input", type: "text", spellcheck: "false", autocomplete: "off",
    value: edit.kind === "rename" ? entry.name : "",
    "aria-label": edit.kind === "rename" ? `Rename ${entry.name}` : `Name for the new ${edit.kind}`,
  });
  const node = el("div", { class: "ex-row editing", style: `padding-left:${8 + depth * INDENT}px;--ex-indent:${24 + depth * INDENT}px` },
    guides(depth), el("span", { class: "ex-twisty" }, isDir ? chevron(false) : null), isDir ? null : fileIcon(entry?.name || ""), input);
  const showProblem = () => {
    const problem = input.value ? validateName(edit, input.value) : null;
    input.classList.toggle("invalid", Boolean(problem));
    node.querySelector(".ex-error")?.remove();
    if (problem) node.append(el("div", { class: "ex-error", role: "alert", text: problem }));
  };
  input.addEventListener("input", showProblem);
  input.addEventListener("keydown", (event) => {
    event.stopPropagation();
    if (event.key === "Enter") { event.preventDefault(); commitEdit(input.value, false); }
    if (event.key === "Escape") { event.preventDefault(); cancelEdit(); }
  });
  input.addEventListener("blur", () => commitEdit(input.value, true));
  return node;
}

function render() {
  buildRows();
  if (focused && !byPath.has(focused)) focused = null;
  const nodes = [];
  if (editing && editing.kind !== "rename" && (editing.parent === rootDir() || (!rootDir() && !byPath.has(editing.parent)))) {
    nodes.push(editRow(editing, 0));
  }
  rows.forEach(({ entry, depth }, index) => {
    nodes.push(editing?.kind === "rename" && editing.path === entry.path ? editRow(editing, depth, entry) : rowNode(entry, depth, index));
    if (editing && editing.kind !== "rename" && entry.type === "dir" && editing.parent === entry.path) nodes.push(editRow(editing, depth + 1));
  });
  if (!nodes.length) {
    nodes.push(el("div", { class: "px-3 py-2 text-xs text-soft", text: searchMode ? "No matching files." : "This folder is empty. Right-click to add a file." }));
  }
  const container = tree();
  container.replaceChildren(...nodes);
  const index = rows.findIndex((row) => row.entry.path === focused);
  if (index >= 0) container.setAttribute("aria-activedescendant", `ex-row-${index}`);
  else container.removeAttribute("aria-activedescendant");

  const input = container.querySelector(".ex-input");
  if (input) {
    input.focus();
    const dot = input.value.lastIndexOf(".");
    const isFile = editing.kind === "rename" && byPath.get(editing.path)?.type === "file";
    input.setSelectionRange(0, isFile && dot > 0 ? dot : input.value.length);
  }
}

function scrollToFocused() {
  const index = rows.findIndex((row) => row.entry.path === focused);
  if (index >= 0) $(`ex-row-${index}`)?.scrollIntoView({ block: "nearest" });
}

function focusTree() {
  tree().focus({ preventScroll: true });
}

// ---- selection and opening --------------------------------------------------

function setFocus(path, { extend = false, toggle = false } = {}) {
  if (toggle) {
    if (selection.has(path) && selection.size > 1) selection.delete(path);
    else selection.add(path);
    anchor = path;
  } else if (extend && anchor && byPath.has(anchor)) {
    const a = rows.findIndex((row) => row.entry.path === anchor);
    const b = rows.findIndex((row) => row.entry.path === path);
    selection.clear();
    for (let i = Math.min(a, b); i <= Math.max(a, b); i += 1) selection.add(rows[i].entry.path);
  } else {
    selection.clear();
    selection.add(path);
    anchor = path;
  }
  focused = path;
  const entry = byPath.get(path);
  if (entry) {
    selectedFolderChanged(entry.type === "dir" ? path : parentOf(path));
  }
  render();
  scrollToFocused();
}

function selectedFolderChanged(folder) {
  const project = projectOf(folder);
  if (project) setActiveProject(project);
}

// The project a path belongs to: a folder directly under a workspace root (projects/acme). A file sitting directly
// in the root belongs to the root itself (projects), so loose files still have a project to work in.
export function projectOf(path) {
  if (!path) return null;
  const top = topEntries.find((entry) => isUnder(path, entry.path));
  // arch/ holds agents and skills: you can edit them, but it is never where the assistant saves its work.
  if (!top || top.root === "config") return null;
  const rest = path.slice(top.path.length + 1);
  if (!rest) return top.path;
  const first = `${top.path}/${rest.split("/")[0]}`;
  return rest.includes("/") || byPath.get(first)?.type === "dir" ? first : top.path;
}

// The items an action applies to: the selection if the focused row is part of it, else the focused row.
function targets() {
  if (focused && selection.has(focused)) return [...selection];
  return focused ? [focused] : [];
}

// Drops paths that sit inside another selected folder, so a folder and its contents are not handled twice.
function topmost(paths) {
  return paths.filter((path) => !paths.some((other) => other !== path && isUnder(path, other)));
}

// The folder that new items, pastes and uploads go into for a given row.
function folderFor(path) {
  if (!path) return rootDir() || topEntries.find((entry) => entry.type === "dir")?.path || null;
  const entry = byPath.get(path);
  return entry?.type === "dir" ? path : parentOf(path) || rootDir();
}

function openEntry(entry) {
  if (entry.type === "dir") return toggleDir(entry.path);
  if (opensNatively(entry.path)) return openNative(entry.path);
  if (isDashboard(entry.path)) emit("preview-dashboard", entry.path);
  else emit("open-file", { path: entry.path, focus: false });
}

async function toggleDir(path, force) {
  const open = force ?? !expanded.has(path);
  if (open) {
    expanded.add(path);
    try {
      if (!children.has(path)) await listDir(path);
      await loadExpanded(children.get(path) || []);
    } catch (error) {
      expanded.delete(path);
      notify("bad", `Could not open ${path}: ${error.message}`);
    }
  } else {
    expanded.delete(path);
  }
  saveExpanded();
  render();
}

function collapseAll() {
  expanded.clear();
  saveExpanded();
  render();
}

// When the editor switches file, show it in the tree like VS Code does.
async function revealPath(path) {
  if (!path || searchMode || path === lastRevealed) return;
  lastRevealed = path;
  const project = projectOf(path);
  if (project) setActiveProject(project);
  const root = rootDir();
  if (root && !isUnder(path, root)) return;
  const parts = path.split("/");
  for (let i = 1; i < parts.length; i += 1) {
    const folder = parts.slice(0, i).join("/");
    if (!root || folder.length > root.length) expanded.add(folder);
  }
  await loadExpanded(topEntries);
  selection.clear();
  selection.add(path);
  focused = anchor = path;
  render();
  scrollToFocused();
}

// ---- folders you opened -------------------------------------------------------

// Makes a folder the active project: the assistant works in it and saves generated files there.
async function useAsOutput(path, quiet = false) {
  const changed = path !== activeProject;
  setActiveProject(path);
  lastRevealed = null;
  await revealPath(path);
  if (changed && !quiet) notify("info", `Generated files will be saved in ${path}`);
}

async function removeOpened(path) {
  const ok = await dialog({ title: `Remove '${baseName(path)}' from the explorer?`, detail: "Nothing on disk is deleted. You can open it again with Open Folder.", confirm: "Remove" });
  if (!ok) return;
  try {
    await closeFolder(path);
    if (activeProject && isUnder(activeProject, path)) setActiveProject(null);
  } catch (error) {
    notify("bad", error.message);
  }
}

// ---- creating, renaming and the inline name box -------------------------------

function siblingsOf(folder) {
  return folder === rootDir() ? topEntries : children.get(folder) || [];
}

function validateName(edit, value) {
  if (edit.kind === "rename" && value.includes("/")) return "A name cannot contain '/'.";
  const parts = value.split("/").filter(Boolean);
  if (!parts.length) return "A file or folder name must be provided.";
  for (const part of parts) {
    if (part === "." || part === "..") return `'${part}' is not a valid name.`;
    if (/[\\:*?"<>|\u0000-\u001f]/.test(part)) return `The name '${part}' is not valid. Avoid \\ : * ? " < > |`;
    if (part !== part.trim()) return "Leading or trailing whitespace detected in file or folder name.";
  }
  const folder = edit.kind === "rename" ? parentOf(edit.path) : edit.parent;
  const clash = siblingsOf(folder).find((entry) => entry.name.toLowerCase() === parts[0].toLowerCase() && entry.path !== edit.path);
  // 'src/new.js' is fine when src is an existing folder: the file goes inside it.
  if (clash && !(parts.length > 1 && clash.type === "dir")) {
    return `A file or folder '${parts[0]}' already exists at this location. Please choose a different name.`;
  }
  return null;
}

async function startCreate(kind, near = focused) {
  const parent = folderFor(near);
  if (!parent) {
    notify("warn", "Select a folder in the explorer first.");
    return;
  }
  if (parent !== rootDir() && !expanded.has(parent)) await toggleDir(parent, true);
  editing = { kind, parent };
  render();
}

function startRename(path = focused) {
  if (!path || !byPath.has(path)) return;
  if (!rootDir() && topEntries.some((entry) => entry.path === path)) {
    notify("warn", "Top-level workspace folders cannot be renamed.");
    return;
  }
  editing = { kind: "rename", path };
  render();
}

function cancelEdit() {
  if (!editing) return;
  editing = null;
  render();
  focusTree();
}

async function commitEdit(value, fromBlur) {
  if (!editing || committing) return;
  const edit = editing;
  if (!value || (edit.kind === "rename" && value === baseName(edit.path))) return cancelEdit();
  const problem = validateName(edit, value);
  if (problem) {
    if (fromBlur) cancelEdit(); // Like VS Code: clicking away from an invalid name abandons it.
    return;
  }
  committing = true;
  try {
    let made;
    if (edit.kind === "rename") {
      made = (await api("/api/files/rename", { json: { session_id: session.id, path: edit.path, new_path: `${parentOf(edit.path)}/${value}` } })).path;
      movedPath(edit.path, made);
    } else {
      const target = `${edit.parent}/${value.replace(/\/+$/, "")}`;
      const endpoint = edit.kind === "folder" ? "/api/files/mkdir" : "/api/files/create";
      made = (await api(endpoint, { json: { session_id: session.id, path: target } })).path;
      expanded.add(edit.parent);
      for (let folder = parentOf(made); folder.length > edit.parent.length; folder = parentOf(folder)) expanded.add(folder);
      if (edit.kind === "folder") expanded.add(made);
      saveExpanded();
    }
    editing = null;
    await reloadAll();
    focusTree();
    lastRevealed = null;
    setFocus(made);
    if (edit.kind === "file") emit("open-file", made); // VS Code opens a new file straight away.
  } catch (error) {
    notify("bad", `${edit.kind === "rename" ? "Could not rename" : `Could not create the ${edit.kind}`}: ${error.message}`);
    editing = null;
    render();
    focusTree();
  } finally {
    committing = false;
  }
}

// Keeps expanded folders, the selection, the clipboard and open editor tabs pointing at a moved path.
function movedPath(from, to) {
  const swap = (path) => (isUnder(path, from) ? to + path.slice(from.length) : path);
  for (const path of [...expanded]) if (isUnder(path, from)) { expanded.delete(path); expanded.add(swap(path)); }
  saveExpanded();
  for (const path of [...selection]) if (isUnder(path, from)) { selection.delete(path); selection.add(swap(path)); }
  if (focused) focused = swap(focused);
  if (clipboard) clipboard.paths = clipboard.paths.map(swap);
  emit("path-renamed", { from, to });
}

// ---- delete, copy, cut, paste, move -------------------------------------------

async function deletePaths(paths) {
  paths = topmost(paths).filter((path) => !(!rootDir() && topEntries.some((entry) => entry.path === path)));
  if (!paths.length) return;
  const one = paths.length === 1;
  const isDir = one && byPath.get(paths[0])?.type === "dir";
  const ok = await dialog({
    title: one
      ? `Are you sure you want to delete '${baseName(paths[0])}'${isDir ? " and its contents" : ""}?`
      : `Are you sure you want to delete the following ${paths.length} items?`,
    detail: (one ? "" : `${paths.map(baseName).slice(0, 10).join("\n")}${paths.length > 10 ? "\n…" : ""}\n\n`)
      + "You can restore from the Trash.",
    confirm: "Move to Trash", danger: true,
  });
  if (!ok) return focusTree();
  const index = rows.findIndex((row) => row.entry.path === focused);
  for (const path of paths) {
    try {
      await api("/api/files/delete", { json: { session_id: session.id, path } });
      emit("path-deleted", path);
      for (const item of [...expanded]) if (isUnder(item, path)) expanded.delete(item);
      selection.delete(path);
    } catch (error) {
      notify("bad", `Could not delete ${path}: ${error.message}`);
    }
  }
  saveExpanded();
  await reloadAll();
  const next = rows[Math.min(index, rows.length - 1)]?.entry.path; // Keep the cursor near where it was.
  focusTree();
  if (next) setFocus(next);
}

function setClipboard(mode) {
  const paths = topmost(targets());
  if (!paths.length) return;
  clipboard = { mode, paths };
  render();
}

async function paste(near = focused) {
  if (!clipboard) return;
  const dest = folderFor(near);
  if (!dest) return notify("warn", "Pick a folder to paste into.");
  const done = await transfer(clipboard.paths, dest, clipboard.mode === "copy");
  if (clipboard.mode === "cut") clipboard = null;
  await finishTransfer(dest, done);
}

async function duplicate(paths = targets()) {
  const done = [];
  for (const path of topmost(paths)) done.push(...await transfer([path], parentOf(path), true));
  await finishTransfer(null, done);
}

// Moves or copies paths into dest. Returns the new paths.
async function transfer(paths, dest, copy) {
  const made = [];
  for (const path of topmost(paths)) {
    if (!copy && parentOf(path) === dest) continue; // Already there.
    if (isUnder(dest, path)) {
      notify("warn", `Cannot ${copy ? "copy" : "move"} '${baseName(path)}' into itself.`);
      continue;
    }
    try {
      if (copy) {
        made.push((await api("/api/files/copy", { json: { session_id: session.id, path, dest_dir: dest } })).path);
      } else {
        const to = (await api("/api/files/rename", { json: { session_id: session.id, path, new_path: `${dest}/${baseName(path)}` } })).path;
        movedPath(path, to);
        made.push(to);
      }
    } catch (error) {
      notify("bad", `Could not ${copy ? "copy" : "move"} ${baseName(path)}: ${error.message}`);
    }
  }
  return made;
}

async function finishTransfer(dest, made) {
  if (dest && dest !== rootDir()) expanded.add(dest);
  saveExpanded();
  await reloadAll();
  if (made.length) {
    selection.clear();
    for (const path of made) selection.add(path);
    focused = anchor = made[made.length - 1];
    render();
    scrollToFocused();
  }
  focusTree();
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const area = el("textarea", { style: "position:fixed;opacity:0" });
    area.value = text;
    document.body.append(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
}

async function copyPaths(absolute) {
  const paths = targets();
  if (!paths.length) return;
  const lines = absolute && isDesktop() ? await Promise.all(paths.map(absolutePath)) : paths;
  await copyText(lines.filter(Boolean).join("\n"));
}

function uploadInto(folder) {
  uploadTarget = folder;
  $("data-input").click();
}

// ---- dialogs ------------------------------------------------------------------

// A small modal used for delete confirmations and the project name. Resolves to true/false, or the typed text.
function dialog({ title, detail = "", confirm = "OK", danger = false, input = null }) {
  return new Promise((resolve) => {
    const field = input === null ? null : el("input", { class: "field mb-3 h-8 w-full px-2 text-sm", value: input, "aria-label": title });
    const ok = el("button", { class: danger ? "danger" : "primary", text: confirm });
    const cancel = el("button", { text: "Cancel" });
    const box = el("dialog", { class: "ex-dialog", "aria-label": title },
      el("h2", { text: title }), detail ? el("p", { style: "white-space:pre-line", text: detail }) : null, field,
      el("div", { class: "ex-buttons" }, cancel, ok));
    const finish = (value) => { box.close(); box.remove(); resolve(value); };
    ok.addEventListener("click", () => finish(field ? field.value.trim() : true));
    cancel.addEventListener("click", () => finish(field ? null : false));
    box.addEventListener("cancel", (event) => { event.preventDefault(); finish(field ? null : false); });
    field?.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); ok.click(); } });
    document.body.append(box);
    box.showModal();
    (field || ok).focus();
  });
}

// ---- context menu -------------------------------------------------------------

let menu = null;

function closeMenu() {
  menu?.remove();
  menu = null;
}

function menuItems(path) {
  const entry = path ? byPath.get(path) : null;
  const many = targets().length > 1;
  const isTop = entry && !rootDir() && topEntries.includes(entry);
  const pasteItem = ["Paste", KEYS.paste, () => paste(path), Boolean(clipboard)];
  const desktop = isDesktop();
  if (!entry) {
    return [
      ["New File…", "", () => startCreate("file", null)], ["New Folder…", "", () => startCreate("folder", null)],
      ["Open Folder…", "", openFolder],
      ["Upload Files…", "", () => uploadInto(folderFor(null))], "-",
      pasteItem, "-",
      ["Refresh", "", refreshTree], ["Collapse Folders", "", collapseAll],
    ];
  }
  const common = [
    "-",
    ["Cut", KEYS.cut, () => setClipboard("cut"), !isTop], ["Copy", KEYS.copy, () => setClipboard("copy")], pasteItem,
    ...(entry.type === "file" ? [["Duplicate", "", () => duplicate()]] : []),
    "-",
    ...(desktop ? [["Copy Path", KEYS.copyPath, () => copyPaths(true)]] : []),
    ["Copy Relative Path", KEYS.copyRel, () => copyPaths(false)],
    "-",
    ["Rename…", KEYS.rename, () => startRename(path), !many && !isTop],
    ["Delete", KEYS.del, () => deletePaths(targets()), !isTop],
  ];
  if (entry.type === "dir") {
    return [
      ["New File…", "", () => startCreate("file", path)], ["New Folder…", "", () => startCreate("folder", path)],
      ["Upload Files…", "", () => uploadInto(path)],
      ...(projectOf(path) ? [["Save Generated Files Here", "", () => useAsOutput(path)]] : []),
      ["Reveal in Finder", "", () => revealNative(path)],
      ...(isTop && entry.root === "folder" ? ["-", ["Remove Folder from Explorer", "", () => removeOpened(path)]] : []),
      ...common,
    ];
  }
  return [
    ["Open", "", () => openEntry(entry), !many],
    ...(opensNatively(path) ? [["Open in Default App", "", () => openNative(path)]] : []),
    ...(isDashboard(path) ? [["Open in Editor", "", () => emit("open-file", path)]] : []),
    ["Reveal in Finder", "", () => revealNative(path)],
    ["Ask Assistant About This File", "", () => emit("ask-file", path), !many],
    ...common,
  ];
}

function openMenu(x, y, path) {
  closeMenu();
  menu = el("div", { class: "ex-menu", role: "menu", "aria-label": "File actions" });
  for (const item of menuItems(path)) {
    if (item === "-") {
      if (menu.lastChild && menu.lastChild.tagName !== "HR") menu.append(el("hr"));
      continue;
    }
    const [label, keys, action, enabled = true] = item;
    menu.append(el("button", {
      role: "menuitem", disabled: !enabled,
      onclick: () => { closeMenu(); action(); },
    }, el("span", { text: label }), keys ? el("span", { class: "ex-key", text: keys }) : null));
  }
  if (menu.lastChild?.tagName === "HR") menu.lastChild.remove();
  menu.addEventListener("keydown", (event) => {
    const items = [...menu.querySelectorAll("button:not(:disabled)")];
    const at = items.indexOf(document.activeElement);
    if (event.key === "ArrowDown") { event.preventDefault(); items[(at + 1) % items.length]?.focus(); }
    if (event.key === "ArrowUp") { event.preventDefault(); items[(at - 1 + items.length) % items.length]?.focus(); }
    if (event.key === "Escape" || event.key === "Tab") { event.preventDefault(); closeMenu(); focusTree(); }
  });
  document.body.append(menu);
  const box = menu.getBoundingClientRect();
  menu.style.left = `${Math.max(4, Math.min(x, window.innerWidth - box.width - 4))}px`;
  menu.style.top = `${Math.max(4, Math.min(y, window.innerHeight - box.height - 4))}px`;
  menu.querySelector("button:not(:disabled)")?.focus();
}

// ---- event wiring -------------------------------------------------------------

function rowOf(event) {
  const row = event.target.closest(".ex-row");
  return row && !row.classList.contains("editing") ? row : null;
}

function onClick(event) {
  const row = rowOf(event);
  if (!row) {
    if (!event.target.closest(".ex-row")) { selection.clear(); focused = null; render(); focusTree(); }
    return;
  }
  const path = row.dataset.path;
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (action === "ask") { event.stopPropagation(); emit("ask-file", path); return; }
  if (action === "native") { event.stopPropagation(); openNative(path); return; }
  focusTree();
  if (event.metaKey || event.ctrlKey) return setFocus(path, { toggle: true });
  if (event.shiftKey) return setFocus(path, { extend: true });
  setFocus(path);
  const entry = byPath.get(path);
  if (entry) openEntry(entry);
}

function onContextMenu(event) {
  event.preventDefault();
  const row = rowOf(event);
  const path = row?.dataset.path || null;
  if (path && !selection.has(path)) setFocus(path);
  if (!path) { selection.clear(); focused = null; render(); }
  openMenu(event.clientX, event.clientY, path);
}

function onKeyDown(event) {
  if (event.target !== tree() || editing) return;
  const mod = isMac ? event.metaKey : event.ctrlKey;
  const index = rows.findIndex((row) => row.entry.path === focused);
  const entry = rows[index]?.entry;
  const move = (to) => {
    const next = rows[Math.max(0, Math.min(rows.length - 1, to))];
    if (next) setFocus(next.entry.path, { extend: event.shiftKey });
  };
  const handled = () => { event.preventDefault(); event.stopPropagation(); };

  if (event.key === "ArrowDown" && mod) { handled(); if (entry) openEntry(entry); return; }
  switch (event.key) {
    case "ArrowDown": handled(); return move(index < 0 ? 0 : index + 1);
    case "ArrowUp": handled(); return move(index < 0 ? 0 : index - 1);
    case "Home": handled(); return move(0);
    case "End": handled(); return move(rows.length - 1);
    case "PageDown": handled(); return move(index + 15);
    case "PageUp": handled(); return move(index - 15);
    case "ArrowRight":
      handled();
      if (entry?.type === "dir") {
        if (!expanded.has(entry.path)) return toggleDir(entry.path, true);
        const child = rows[index + 1];
        if (child && child.depth > rows[index].depth) setFocus(child.entry.path);
      }
      return;
    case "ArrowLeft":
      handled();
      if (entry?.type === "dir" && expanded.has(entry.path)) return toggleDir(entry.path, false);
      if (entry && byPath.has(parentOf(entry.path))) setFocus(parentOf(entry.path));
      return;
    case "Enter":
      handled();
      if (!entry) return;
      return isMac ? startRename(entry.path) : openEntry(entry);
    case " ": handled(); if (entry) openEntry(entry); return;
    case "F2": handled(); return startRename(entry?.path);
    case "Delete": handled(); return deletePaths(targets());
    case "Backspace": if (mod) { handled(); deletePaths(targets()); } return;
    case "Escape":
      handled();
      if (clipboard?.mode === "cut") clipboard = null;
      if (focused) { selection.clear(); selection.add(focused); }
      return render();
    case "ContextMenu": {
      handled();
      const row = $(`ex-row-${index}`)?.getBoundingClientRect();
      return openMenu(row ? row.left + 24 : 40, row ? row.bottom : 40, focused);
    }
    default:
  }
  if (event.key === "F10" && event.shiftKey) {
    handled();
    const row = $(`ex-row-${index}`)?.getBoundingClientRect();
    return openMenu(row ? row.left + 24 : 40, row ? row.bottom : 40, focused);
  }
  if (!mod) return;
  const key = event.key.toLowerCase();
  if (key === "c" && event.altKey) { handled(); return copyPaths(!event.shiftKey); }
  if (key === "c") { handled(); return setClipboard("copy"); }
  if (key === "x") { handled(); return setClipboard("cut"); }
  if (key === "v") { handled(); return paste(); }
  if (key === "a") { handled(); selection.clear(); for (const row of rows) selection.add(row.entry.path); return render(); }
}

// Drag and drop: rows within the tree move (Option/Alt copies); files from the desktop upload.
let hoverTimer = null;
let hoverPath = null;

function clearDropMarks() {
  for (const node of tree().querySelectorAll(".drop-inside")) node.classList.remove("drop-inside");
  tree().classList.remove("drop-target");
  clearTimeout(hoverTimer);
  hoverPath = null;
}

function dropDestination(event) {
  const row = rowOf(event);
  return row ? folderFor(row.dataset.path) : folderFor(null);
}

function canMoveInto(dest, copy, paths = dragPaths) {
  if (!dest || !paths) return false;
  return paths.every((path) => !isUnder(dest, path)) && (copy || paths.some((path) => parentOf(path) !== dest));
}

function onDragStart(event) {
  const row = rowOf(event);
  if (!row) return;
  const path = row.dataset.path;
  // Do not re-render here: replacing the dragged element would cancel the drag in WebKit.
  dragPaths = topmost(selection.has(path) ? [...selection] : [path]);
  event.dataTransfer.setData(DRAG_TYPE, JSON.stringify(dragPaths));
  event.dataTransfer.setData("text/plain", dragPaths.join("\n"));
  event.dataTransfer.effectAllowed = "copyMove";
}

function onDragOver(event) {
  const types = [...(event.dataTransfer?.types || [])];
  const internal = types.includes(DRAG_TYPE) && dragPaths;
  if (!internal && !types.includes("Files")) return;
  const dest = dropDestination(event);
  if (internal && !canMoveInto(dest, event.altKey)) {
    clearDropMarks();
    event.dataTransfer.dropEffect = "none";
    return;
  }
  event.preventDefault();
  event.dataTransfer.dropEffect = internal ? (event.altKey ? "copy" : "move") : "copy";
  for (const node of tree().querySelectorAll(".drop-inside")) if (node.dataset.path !== dest) node.classList.remove("drop-inside");
  const target = dest && tree().querySelector(`.ex-row[data-path="${CSS.escape(dest)}"]`);
  if (target) target.classList.add("drop-inside");
  else tree().classList.add("drop-target");
  // Hovering over a closed folder opens it after a moment, like VS Code.
  const row = rowOf(event);
  const over = row?.dataset.type === "dir" && !expanded.has(row.dataset.path) ? row.dataset.path : null;
  if (over !== hoverPath) {
    clearTimeout(hoverTimer);
    hoverPath = over;
    if (over) hoverTimer = setTimeout(() => toggleDir(over, true), 700);
  }
}

async function onDrop(event) {
  const types = [...(event.dataTransfer?.types || [])];
  const internal = types.includes(DRAG_TYPE) && dragPaths;
  if (!internal && !types.includes("Files")) return;
  event.preventDefault();
  event.stopPropagation();
  const dest = dropDestination(event);
  clearDropMarks();
  if (internal) {
    const paths = dragPaths;
    dragPaths = null;
    if (!canMoveInto(dest, event.altKey, paths)) return;
    await finishTransfer(dest, await transfer(paths, dest, event.altKey));
    return;
  }
  if (!dest) return notify("warn", "Drop the files onto a folder.");
  const items = await collectDropped(event.dataTransfer);
  if (items.length) await uploadFiles(items, dest);
}

// ---- projects -----------------------------------------------------------------

export function setActiveProject(path) {
  if (path === activeProject) return;
  activeProject = path;
  store.set("workspace.project", path || "");
  $("active-project").textContent = path ? `Project: ${path}` : "No project: select a folder or file";
  emit("project-changed", path);
}

export const getActiveProject = () => activeProject;

// ---- uploads ------------------------------------------------------------------

async function uploadFiles(items, target) {
  let added = 0;
  const made = new Set();
  for (const { file, dir } of items) {
    const folder = dir ? `${target}/${dir}` : target;
    try {
      if (dir && !made.has(folder)) { await api("/api/files/mkdir", { json: { session_id: session.id, path: folder } }); made.add(folder); }
      const form = new FormData();
      form.append("session_id", session.id);
      form.append("file", file);
      form.append("target_dir", folder);
      form.append("attach", "false");
      await api("/api/files/upload", { method: "POST", body: form });
      added += 1;
    } catch (error) {
      notify("bad", `${file.name}: ${error.message}`);
    }
  }
  if (added) {
    if (target !== rootDir()) expanded.add(target);
    saveExpanded();
    notify("info", `Added ${added} file${added === 1 ? "" : "s"} to ${target}`);
  }
  await reloadAll();
}

// Reads dropped files and whole folders (the browser exposes folders only through webkitGetAsEntry).
async function collectDropped(dataTransfer) {
  const items = [];
  const readBatch = (reader) => new Promise((resolve, reject) => reader.readEntries(resolve, reject));
  const walk = async (entry, dir) => {
    if (entry.isFile) {
      const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
      items.push({ file, dir });
    } else if (entry.isDirectory) {
      const reader = entry.createReader();
      const list = [];
      for (let batch = await readBatch(reader); batch.length; batch = await readBatch(reader)) list.push(...batch);
      const sub = dir ? `${dir}/${entry.name}` : entry.name;
      for (const child of list) await walk(child, sub);
    }
  };
  const entries = [...dataTransfer.items].filter((i) => i.kind === "file").map((i) => i.webkitGetAsEntry?.());
  if (entries.length && entries.every(Boolean)) {
    for (const entry of entries) await walk(entry, "");
  } else {
    for (const file of dataTransfer.files) items.push({ file, dir: "" });
  }
  return items;
}

// ---- start ------------------------------------------------------------------

export function initTree() {
  const container = tree();
  container.addEventListener("click", onClick);
  container.addEventListener("contextmenu", onContextMenu);
  container.addEventListener("keydown", onKeyDown);
  container.addEventListener("dragstart", onDragStart);
  container.addEventListener("dragover", onDragOver);
  container.addEventListener("dragleave", (event) => { if (!container.contains(event.relatedTarget)) clearDropMarks(); });
  container.addEventListener("drop", onDrop);
  container.addEventListener("dragend", () => { dragPaths = null; clearDropMarks(); });
  container.addEventListener("scroll", closeMenu);
  document.addEventListener("mousedown", (event) => { if (menu && !menu.contains(event.target)) closeMenu(); }, true);
  window.addEventListener("blur", closeMenu);
  window.addEventListener("resize", closeMenu);

  $("open-folder").addEventListener("click", openFolder);
  on("folders-changed", async (path) => {
    if (path) expanded.add(path);
    saveExpanded();
    await reloadAll();
    if (path) await useAsOutput(path, true); // Opening the folder already said so.
  });
  on("use-folder", (path) => useAsOutput(path));
  $("new-file").addEventListener("click", () => startCreate("file"));
  $("new-folder").addEventListener("click", () => startCreate("folder"));
  $("collapse-tree").addEventListener("click", collapseAll);
  $("refresh-tree").addEventListener("click", reloadAll);
  $("data-input").removeAttribute("accept"); // The server checks file types against permission.md.
  $("data-input").addEventListener("change", (event) => {
    const files = [...event.target.files];
    event.target.value = "";
    const target = uploadTarget || folderFor(focused);
    uploadTarget = null;
    if (!files.length) return;
    if (!target) return notify("warn", "Select a folder in the explorer first, then upload.");
    uploadFiles(files.map((file) => ({ file, dir: "" })), target);
  });

  let timer;
  $("file-search").addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(refreshTree, 150);
  });
  on("files-changed", reloadAll);
  on("desktop-ready", render);
  on("active-file", (path) => revealPath(path));
  on("scope-changed", () => refreshTree());

  const remembered = store.get("workspace.project") || null;
  refreshTree().then(async () => {
    setActiveProject(remembered && (await folderExists(remembered)) ? remembered : null);
    if (!activeProject) $("active-project").textContent = "No project: select a folder or file";
  });
}
