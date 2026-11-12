// The chat window's side of the Files window: which project and file are active there, and opening it.
import { api, emit, on, session, store } from "./util.js";
import { listen, send } from "./channel.js";
import { toast } from "./ui.js";

let project = store.get("workspace.project") || null;
let remoteFile = null; // The file open in the Files window's editor; the assistant sees it with the next message.
let filesOpen = false;
const pending = []; // Messages for a Files window that is still loading.

export const getActiveProject = () => project;
export const activeFile = () => remoteFile;

export function setActiveProject(path, { broadcast = true } = {}) {
  path = path || null;
  if (path === project) return;
  project = path;
  store.set("workspace.project", path || "");
  emit("project-changed", path);
  if (broadcast) send("project", { path });
}

// The server opens it (a native window in the desktop app, a new browser tab otherwise), so pop-up blockers never
// get in the way.
export async function openFilesWindow(path = null) {
  const desktop = Boolean(window.pywebview);
  if (filesOpen && !desktop) {
    // Already open in another tab: hand it the file instead of opening a second one.
    if (path) send("open-file", { path });
    send("focus-files");
    toast(path ? `Opened ${path.split("/").pop()} in the Files tab.` : "The Files window is already open in another tab.");
    return;
  }
  try {
    await api("/api/files-window", { json: { path } });
  } catch (error) {
    toast(`Could not open the Files window: ${error.message}`, "bad", 6000);
  }
}

function sendToFiles(type, data) {
  if (filesOpen) send(type, data);
  else pending.push([type, data]);
}

export function showDiff(path, content) {
  sendToFiles("show-diff", { path, content });
  openFilesWindow();
}

// Opens a generated file outside the app: dashboards in the browser, documents in their own app.
export async function openOutside(path, { reveal = false } = {}) {
  try {
    const result = await api("/api/files/open", { json: { session_id: session.id, path, reveal, base: project } });
    toast(reveal ? `Shown in Finder: ${result.path.split("/").pop()}` : `${result.path.split("/").pop()} ${result.opened}`);
  } catch (error) {
    toast(error.message, "bad", 6000);
  }
}

export function initWorkspaceLink() {
  listen("project", (m) => setActiveProject(m.path, { broadcast: false }));
  listen("active-file", (m) => { remoteFile = m.path || null; emit("active-file", remoteFile); });
  listen("ask-file", (m) => {
    emit("ask-file", m.path);
    window.focus();
  });
  listen("files-ready", (m) => {
    filesOpen = true;
    if (m.project === undefined || m.project === null) send("project", { path: project });
    while (pending.length) send(...pending.shift());
  });
  listen("files-closed", () => {
    filesOpen = false;
    remoteFile = null;
    emit("active-file", null);
  });
  on("use-folder", (path) => setActiveProject(path));
  on("files-changed", () => send("files-changed"));
  on("paths-changed", (paths) => send("paths-changed", { paths }));
  on("folders-changed", (path) => send("folders-changed", { path }));
  on("appearance-changed", () => send("appearance"));
  send("chat-ready"); // A Files window that is already open answers with files-ready.
}
