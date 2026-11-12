// The Files window: explorer and editor in their own window, beside the chat.
import { $, emit, on, store } from "./util.js";
import { applyEditorTheme, initEditor, showDiff } from "./editor.js";
import { getActiveProject, initTree, setActiveProject } from "./tree.js";
import { initDashboard } from "./dashboard.js";
import { initDesktop } from "./desktop.js";
import { hydrateIcons } from "./icons.js";
import { listen, send } from "./channel.js";
import { toast } from "./ui.js";

function applyAppearance() {
  const theme = store.get("workspace.theme") || "system";
  if (theme === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  applyEditorTheme();
}

hydrateIcons();
applyAppearance();
initDesktop();
initEditor();
initTree();
initDashboard();

// This window -> chat
on("active-file", (path) => send("active-file", { path }));
on("project-changed", (path) => send("project", { path }));
on("ask-file", (path) => {
  send("ask-file", { path });
  window.pywebview?.api?.focus_chat?.();
  toast("Ask about it in the chat window.");
});
on("notice", ({ level, message }) => toast(message, level === "info" ? "info" : level));

// Chat -> this window
listen("open-file", (m) => emit("open-file", m.path));
listen("show-diff", (m) => showDiff(m.path, m.content));
listen("files-changed", () => emit("files-changed"));
listen("paths-changed", (m) => emit("paths-changed", m.paths || []));
listen("folders-changed", (m) => emit("folders-changed", m.path));
listen("project", (m) => { if (m.path !== getActiveProject()) setActiveProject(m.path || null); });
listen("appearance", () => applyAppearance());
listen("focus-files", () => window.focus());
listen("chat-ready", () => send("files-ready", { project: getActiveProject() }));

$("back-to-chat").addEventListener("click", () => {
  if (window.pywebview?.api?.focus_chat) window.pywebview.api.focus_chat();
  else window.opener?.focus();
});

const opened = new URLSearchParams(location.search).get("open");
if (opened) setTimeout(() => emit("open-file", opened), 300);
send("files-ready", { project: getActiveProject() });
window.addEventListener("pagehide", () => send("files-closed"));
