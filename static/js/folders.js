// Folders the user chooses: "Open Folder…", and "Where should these files go?" when attaching files with no project
// selected. Any folder on this computer can be opened; the backend refuses app internals and secrets.
import { api, el, emit, qs, session } from "./util.js";
import { chooseFolder, isDesktop } from "./desktop.js";
import { icon } from "./icons.js";

// A modal built on <dialog>. `build(close)` returns { body, footer } node lists; resolves with what close() is given.
function modal({ title, subtitle, iconName = "folder", wide = false }, build) {
  return new Promise((resolve) => {
    const box = el("dialog", { class: `dlg${wide ? " wide" : ""}`, "aria-label": title });
    const close = (value) => { box.close(); box.remove(); resolve(value); };
    box.addEventListener("cancel", (event) => { event.preventDefault(); close(null); });
    box.addEventListener("mousedown", (event) => { if (event.target === box) close(null); });
    const { body, footer } = build(close);
    box.append(
      el("div", { class: "dlg-head" },
        el("span", { class: "dlg-icon" }, icon(iconName)),
        el("div", { class: "dlg-titles" }, el("h2", { text: title }), subtitle ? el("p", { text: subtitle }) : null),
        el("button", { class: "ghost sm dlg-x", "aria-label": "Close", title: "Close (Esc)", onclick: () => close(null) }, icon("x", "i i-sm"))),
      el("div", { class: "dlg-body" }, ...body.filter(Boolean)),
      el("div", { class: "dlg-foot" }, ...footer.filter(Boolean)));
    document.body.append(box);
    box.showModal();
    (box.querySelector("[data-autofocus]") || box.querySelector(".dlg-body input, .dlg-option, .btn.primary"))?.focus();
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
    const answer = await modal({ title: "Open a folder", subtitle: "Any folder on this computer. The assistant can then read it and save files there." }, (close) => {
      const field = el("input", { class: "input mono", placeholder: "/Users/you/Documents/reports", "aria-label": "Folder path", "data-autofocus": true });
      const make = el("input", { type: "checkbox" });
      const ok = el("button", { class: "btn primary", text: "Open folder", onclick: () => field.value.trim() && close({ path: field.value.trim(), create: make.checked }) });
      field.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); ok.click(); } });
      return {
        body: [
          el("label", { class: "field-label", text: "Full path" }), field,
          el("label", { class: "dlg-check" }, make, "Create the folder if it does not exist"),
        ],
        footer: [el("span", { class: "spacer" }), el("button", { class: "btn", text: "Cancel", onclick: () => close(null) }), ok],
      };
    });
    if (answer) ({ path, create } = answer);
  }
  if (!path) return null;
  try {
    const result = await api("/api/folders", { json: { path, create } });
    emit("folders-changed", result.path);
    emit("notice", { level: "info", message: `Opened ${result.path.split("/").pop()}. Files the assistant creates are saved there.` });
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

// Places to save into, grouped: each top folder of the explorer (not arch/) with the projects inside it.
async function candidates() {
  const top = (await api(`/api/files/tree?${qs({ session_id: session.id })}`)).entries.filter((e) => e.root !== "config");
  const projects = (await api(`/api/projects?${qs({ session_id: session.id })}`).catch(() => ({ projects: [] }))).projects;
  return top.map((entry) => ({
    path: entry.path,
    name: entry.root === "folder" ? entry.name : "Projects",
    opened: entry.root === "folder",
    projects: projects
      .filter((p) => p.path.startsWith(`${entry.path}/`) && !p.path.slice(entry.path.length + 1).includes("/"))
      .map((p) => ({ path: p.path, name: p.name, datasets: p.datasets })),
  }));
}

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

// "Where should these files go?" Resolves to a folder path, "" for "just attach", or null if cancelled.
export async function chooseSaveFolder(fileNames = []) {
  let groups = [];
  try { groups = await candidates(); } catch { /* Offer the other options anyway. */ }
  const subtitle = fileNames.length === 1
    ? `${fileNames[0]} is saved into a project, so the assistant can work on it and save what it creates beside it.`
    : `${plural(fileNames.length, "file")} are saved into a project, so the assistant can work on them and save what it creates beside them.`;

  return modal({ title: fileNames.length === 1 ? "Where should this file go?" : "Where should these files go?", subtitle, iconName: "upload", wide: true }, (close) => {
    let chosen = null;
    const save = el("button", { class: "btn primary", text: "Save here", disabled: true, onclick: () => chosen && close(chosen) });
    const options = [];
    const option = (path, name, sub, iconName) => {
      const node = el("button", { class: "dlg-option", role: "radio", "aria-checked": "false", title: path },
        el("span", { class: "dlg-option-icon" }, icon(iconName, "i i-sm")),
        el("span", { class: "dlg-option-text" }, el("span", { class: "dlg-option-name", text: name }), el("span", { class: "dlg-option-sub", text: sub })),
        el("span", { class: "dlg-option-check" }, icon("check", "i i-sm")));
      node.addEventListener("click", () => {
        chosen = path;
        for (const other of options) other.setAttribute("aria-checked", String(other === node));
        save.disabled = false;
        save.textContent = `Save to ${name}`;
      });
      node.addEventListener("dblclick", () => close(path));
      node.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); close(path); } });
      options.push(node);
      return node;
    };

    const lists = groups.map((group) => el("div", { class: "dlg-group" },
      el("div", { class: "dlg-group-title", text: group.opened ? `Opened folder · ${group.path}` : group.name }),
      el("div", { class: "dlg-options", role: "radiogroup" },
        group.opened ? option(group.path, group.name, "The folder itself", "folder") : null,
        ...group.projects.map((p) => option(p.path, p.name, p.datasets ? plural(p.datasets, "data file") : p.path, "folder")),
        !group.opened && !group.projects.length ? el("div", { class: "dlg-empty", text: "No projects yet — create one below." }) : null)));

    // New project, inside the first top folder (normally workspace/projects).
    const parents = groups.map((g) => g.path);
    const name = el("input", { class: "input", placeholder: "New project name, e.g. sales-report", "aria-label": "New project name" });
    const parent = parents.length > 1
      ? el("select", { class: "select dlg-parent", "aria-label": "Create inside" }, groups.map((g) => el("option", { value: g.path, text: `in ${g.opened ? g.name : "Projects"}` })))
      : null;
    const create = el("button", { class: "btn", onclick: async () => {
      const value = name.value.trim();
      const where = parent ? parent.value : parents[0];
      if (!value || !where) { name.focus(); return; }
      try {
        const made = await api("/api/files/mkdir", { json: { session_id: session.id, path: `${where}/${value}` } });
        emit("files-changed");
        close(made.path);
      } catch (error) {
        emit("notice", { level: "bad", message: error.message });
      }
    } }, icon("plus", "i i-sm"), "Create & save");
    name.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); create.click(); } });

    const browse = el("button", { class: "btn link", onclick: async (event) => {
      const box = event.currentTarget.closest("dialog");
      box.close(); // The folder prompt is its own modal.
      const opened = await openFolder();
      if (opened) close(opened);
      else box.showModal();
    } }, icon("folder", "i i-sm"), isDesktop() ? "Choose another folder…" : "Open another folder…");

    return {
      body: [
        fileNames.length ? el("div", { class: "dlg-files" }, fileNames.slice(0, 6).map((f) => el("span", { class: "chip" }, icon("paperclip"), el("span", { text: f }))),
          fileNames.length > 6 ? el("span", { class: "dlg-more", text: `+${fileNames.length - 6} more` }) : null) : null,
        ...lists,
        parents.length ? el("div", { class: "dlg-new" }, name, parent, create) : null,
      ],
      footer: [
        browse,
        el("span", { class: "spacer" }),
        el("button", { class: "btn", text: "Just attach", title: "Give the files to this chat without saving a copy in a project", onclick: () => close("") }),
        save,
      ],
    };
  });
}
