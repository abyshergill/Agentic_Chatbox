// Folders the user chooses: "Open Folder…" in the explorer, and "Where should files be saved?" when attaching files
// with no folder selected. Any folder on this computer can be opened; the backend refuses app internals and secrets.
import { api, el, emit, qs, session } from "./util.js";
import { chooseFolder, isDesktop } from "./desktop.js";

// A modal built on <dialog>. `build(close)` returns the body nodes; resolves with whatever close() is given.
function modal(title, detail, build) {
  return new Promise((resolve) => {
    const box = el("dialog", { class: "ex-dialog fd-dialog", "aria-label": title });
    const close = (value) => { box.close(); box.remove(); resolve(value); };
    box.addEventListener("cancel", (event) => { event.preventDefault(); close(null); });
    box.append(el("h2", { text: title }), detail ? el("p", { text: detail }) : null, ...build(close));
    document.body.append(box);
    box.showModal();
    box.querySelector("input, .fd-option, button.primary")?.focus();
  });
}

// Asks for a folder path (browser) or shows the system picker (desktop app), then adds it to the explorer.
// Resolves to the opened folder's path, or null.
export async function openFolder() {
  let path = null;
  let create = false;
  if (isDesktop()) {
    path = await chooseFolder();
  } else {
    const answer = await modal("Open Folder", "Type the full path of a folder on this computer. It appears in the explorer, "
      + "and the assistant can read and save files there.", (close) => {
      const field = el("input", { class: "field fd-input", placeholder: "/Users/you/Documents/reports", "aria-label": "Folder path" });
      const make = el("input", { type: "checkbox" });
      const ok = el("button", { class: "primary", text: "Open" });
      ok.addEventListener("click", () => field.value.trim() && close({ path: field.value.trim(), create: make.checked }));
      field.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); ok.click(); } });
      return [field, el("label", { class: "fd-check" }, make, " Create the folder if it does not exist"),
        el("div", { class: "ex-buttons" }, el("button", { text: "Cancel", onclick: () => close(null) }), ok)];
    });
    if (answer) ({ path, create } = answer);
  }
  if (!path) return null;
  try {
    const result = await api("/api/folders", { json: { path, create } });
    emit("folders-changed", result.path);
    emit("notice", { level: "info", message: `Opened ${result.path}. Generated files will be saved there; pick another folder in the explorer to change that.` });
    return result.path;
  } catch (error) {
    emit("notice", { level: "bad", message: `Could not open that folder: ${error.message}` });
    return null;
  }
}

export async function closeFolder(path) {
  await api("/api/folders/remove", { json: { path } });
  emit("folders-changed", null);
}

// Folders offered as save locations: the explorer's top folders (not arch/) and the project folders inside them.
async function candidates() {
  const top = (await api(`/api/files/tree?${qs({ session_id: session.id })}`)).entries.filter((e) => e.root !== "config");
  const projects = (await api(`/api/projects?${qs({ session_id: session.id })}`).catch(() => ({ projects: [] }))).projects;
  const seen = new Set();
  const list = [];
  for (const entry of top) {
    list.push({ path: entry.path, label: entry.name, depth: 0 });
    seen.add(entry.path);
    for (const project of projects.filter((p) => p.path.startsWith(`${entry.path}/`) && !p.path.slice(entry.path.length + 1).includes("/"))) {
      if (!seen.has(project.path)) list.push({ path: project.path, label: project.name, depth: 1 });
      seen.add(project.path);
    }
  }
  return list;
}

// "Where should generated files be saved?" Resolves to a folder path, "" for "attach only", or null if cancelled.
export async function chooseSaveFolder(fileNames = []) {
  let folders = [];
  try { folders = await candidates(); } catch { /* Offer the other options anyway. */ }
  const what = fileNames.length ? `${fileNames.join(", ")} will be copied there, and files the assistant creates from it are saved there too.` : "";
  return modal("Where should files be saved?", `No folder is selected in the explorer. ${what}`, (close) => {
    const list = el("div", { class: "fd-list", role: "listbox", "aria-label": "Folders" },
      folders.length ? folders.map((f) => el("button", {
        class: "fd-option", role: "option", title: f.path, style: `padding-left:${10 + f.depth * 16}px`,
        onclick: () => close(f.path),
      }, el("span", { class: "fd-icon", "aria-hidden": "true", text: f.depth ? "▸" : "▣" }), el("span", { text: f.label })))
        : el("div", { class: "fd-empty", text: "No folders yet. Create one or open a folder from your computer." }));

    const name = el("input", { class: "field fd-input", placeholder: "New folder name, e.g. sales-report", "aria-label": "New folder name" });
    const parent = el("select", { class: "field fd-select", "aria-label": "Create inside" },
      folders.filter((f) => f.depth === 0).map((f) => el("option", { value: f.path, text: f.label })));
    const create = el("button", { text: "Create" });
    create.addEventListener("click", async () => {
      const value = name.value.trim();
      if (!value || !parent.value) return;
      try {
        const made = await api("/api/files/mkdir", { json: { session_id: session.id, path: `${parent.value}/${value}` } });
        emit("files-changed");
        close(made.path);
      } catch (error) {
        emit("notice", { level: "bad", message: error.message });
      }
    });
    name.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); create.click(); } });

    const browse = el("button", { text: isDesktop() ? "Choose Another Folder…" : "Open Another Folder…" });
    browse.addEventListener("click", async () => {
      const box = browse.closest("dialog");
      box.close(); // The folder prompt is its own modal.
      const opened = await openFolder();
      if (opened) close(opened);
      else box.showModal();
    });

    return [
      list,
      parent.options.length ? el("div", { class: "fd-row" }, name, parent, create) : null,
      el("div", { class: "ex-buttons fd-buttons" },
        browse,
        el("button", { text: "Attach Only", title: "Attach to this chat without saving a copy", onclick: () => close("") }),
        el("button", { text: "Cancel", onclick: () => close(null) })),
    ];
  });
}
