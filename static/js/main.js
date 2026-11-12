// Chat window shell: sidebar and chat list, top bar, theme, status, keyboard shortcuts.
// Files and the editor live in their own window (workspace.html), opened with openFilesWindow().
import { $, api, el, emit, on, qs, session, store } from "./util.js";
import { initChat, isRunning, newChat, switchChat } from "./chat.js";
import { initDesktop } from "./desktop.js";
import { initWorkspaceLink, openFilesWindow } from "./workspace-link.js";
import { initSettings, openSettings } from "./settings.js";
import { hydrateIcons, icon } from "./icons.js";
import { toast } from "./ui.js";
import { refreshHealth, refreshPreferences, state } from "./state.js";

// ---- theme and font ----------------------------------------------------------------

export function applyAppearance() {
  const theme = store.get("workspace.theme") || "system";
  if (theme === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
  document.documentElement.dataset.font = store.get("workspace.font") || "serif";
}

function toggleTheme() {
  const dark = getComputedStyle(document.documentElement).colorScheme.includes("dark");
  store.set("workspace.theme", dark ? "light" : "dark");
  applyAppearance();
  emit("appearance-changed");
}

// ---- sidebar ----------------------------------------------------------------------------

function setSidebar(open) {
  $("app").classList.toggle("sidebar-collapsed", !open);
  $("show-sidebar").classList.toggle("hidden", open);
  store.set("workspace.sidebar", open ? "1" : "0");
}

let chats = [];

function groupOf(seconds) {
  const date = new Date(seconds * 1000);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const days = (today - date) / 86_400_000;
  if (date >= today) return "Today";
  if (days < 1) return "Yesterday";
  if (days < 7) return "Previous 7 days";
  if (days < 30) return "Previous 30 days";
  return "Older";
}

async function renderChats() {
  const list = $("chat-list");
  const q = $("chat-search").value.trim();
  try {
    chats = (await api(`/api/chats?${qs({ q, limit: 300 })}`)).chats;
  } catch (error) {
    list.replaceChildren(el("li", { class: "chat-empty", text: error.message }));
    return;
  }
  renderTitle();
  if (!chats.length) {
    list.replaceChildren(el("li", { class: "chat-empty", text: q ? "No chats match." : "Your chats appear here." }));
    return;
  }
  const nodes = [];
  let group = null;
  for (const chat of chats) {
    const name = groupOf(chat.updated);
    if (name !== group) {
      group = name;
      nodes.push(el("li", { class: "chat-group", text: name }));
    }
    nodes.push(chatRow(chat));
  }
  list.replaceChildren(...nodes);
}

function chatRow(chat) {
  const current = chat.id === session.id;
  const row = el("li", { class: `chat-row${current ? " current" : ""}` });
  const open = el("button", {
    class: "chat-open", text: chat.title, title: chat.title,
    onclick: async () => { if (await switchChat(chat.id)) closeSidebarOnMobile(); },
  });
  const rename = el("button", { title: "Rename", "aria-label": `Rename ${chat.title}` }, icon("edit", "i i-sm"));
  rename.addEventListener("click", () => startRename(row, chat));
  const remove = el("button", { title: "Delete", "aria-label": `Delete ${chat.title}` }, icon("trash", "i i-sm"));
  remove.addEventListener("click", async () => {
    if (!remove.classList.contains("armed")) {
      remove.classList.add("armed");
      remove.replaceChildren("Delete?");
      setTimeout(() => { remove.classList.remove("armed"); remove.replaceChildren(icon("trash", "i i-sm")); }, 3000);
      return;
    }
    try {
      await api(`/api/chats/${chat.id}`, { method: "DELETE" });
      if (current) await newChat();
      renderChats();
    } catch (error) {
      toast(error.message, "bad");
    }
  });
  row.append(open, el("span", { class: "chat-actions" }, rename, remove));
  return row;
}

function startRename(row, chat, onDone) {
  const field = el("input", { class: "chat-rename", value: chat.title, "aria-label": "Chat name" });
  let done = false;
  const save = async () => {
    if (done) return;
    done = true;
    const value = field.value.trim();
    if (value && value !== chat.title) {
      try { await api(`/api/chats/${chat.id}/rename`, { json: { title: value } }); } catch (error) { toast(error.message, "bad"); }
    }
    renderChats();
    onDone?.();
  };
  field.addEventListener("keydown", (event) => {
    event.stopPropagation();
    if (event.key === "Enter") { event.preventDefault(); save(); }
    if (event.key === "Escape") { event.preventDefault(); done = true; renderChats(); onDone?.(); }
  });
  field.addEventListener("blur", save);
  row.replaceChildren(field);
  field.focus();
  field.select();
}

function renderTitle() {
  const chat = chats.find((c) => c.id === session.id);
  $("chat-title").textContent = chat ? chat.title : "New chat";
  $("chat-title").disabled = !chat;
  document.title = chat ? `${chat.title} · Agentic Chatbox` : "Agentic Chatbox";
}

function renameCurrent() {
  const chat = chats.find((c) => c.id === session.id);
  if (!chat) return;
  const button = $("chat-title");
  const field = el("input", { class: "chat-title-input", value: chat.title, "aria-label": "Chat name" });
  let done = false;
  const finish = async (saveIt) => {
    if (done) return;
    done = true;
    const value = field.value.trim();
    if (saveIt && value && value !== chat.title) {
      try { await api(`/api/chats/${chat.id}/rename`, { json: { title: value } }); } catch (error) { toast(error.message, "bad"); }
    }
    field.replaceWith(button);
    renderChats();
  };
  field.addEventListener("keydown", (event) => {
    if (event.key === "Enter") finish(true);
    if (event.key === "Escape") finish(false);
  });
  field.addEventListener("blur", () => finish(true));
  button.replaceWith(field);
  field.focus();
  field.select();
}

function closeSidebarOnMobile() {
  if (window.innerWidth <= 760) setSidebar(false);
}

// ---- status ---------------------------------------------------------------------------------

function renderStatus(health) {
  const dot = $("status-dot");
  const text = $("status-text");
  if (!health) {
    dot.className = "dot bad";
    text.textContent = "Server unreachable";
    return;
  }
  const models = state.catalog.length;
  const connected = state.connectors.filter((c) => c.status === "connected").length;
  dot.className = `dot ${isRunning() ? "busy" : models ? "good" : "bad"}`;
  text.textContent = models ? `${models} model${models === 1 ? "" : "s"}${connected ? ` · ${connected} connector${connected === 1 ? "" : "s"}` : ""}` : "No models found";
  $("nav-models-count").textContent = models || "";
  $("nav-connectors-count").textContent = state.connectors.length ? `${connected}/${state.connectors.length}` : "";
  $("nav-skills-count").textContent = health.skills || "";
  const warnings = health.warnings?.length ? `\n${health.warnings.length} configuration warning(s): see Settings › Agents & permissions` : "";
  $("open-settings").title = `Settings\n${health.isolation !== "none" ? "Commands run sandboxed" : "No command sandbox on this platform"}${warnings}`;
}

function renderMe() {
  const name = state.preferences.display_name || "You";
  $("me-name").textContent = name;
  $("me-avatar").textContent = name.trim().charAt(0).toUpperCase() || "Y";
}

function showActivation(event) {
  const badge = $("skill-badge");
  if (event.cleared) {
    badge.classList.add("hidden");
    state.skillDefaultModel = "";
    emit("model-changed");
    return;
  }
  const label = event.hash_app ? `#${event.hash_app}` : `${event.skill}${event.command ? ` · ${event.command}` : ""}`;
  $("chat-skill").textContent = label;
  badge.title = `${event.agent} · ${event.pinned ? "stays active until you clear it" : "this message only"}`;
  $("clear-skill").classList.toggle("hidden", !event.pinned);
  badge.classList.toggle("hidden", !event.pinned && !event.hash_app);
  state.skillDefaultModel = event.model || "";
  emit("model-changed");
}

// ---- start ------------------------------------------------------------------------------------

hydrateIcons();
applyAppearance();
initDesktop();
initWorkspaceLink();
initSettings();
initChat();

setSidebar(store.get("workspace.sidebar") !== "0" && window.innerWidth > 760);

$("collapse-sidebar").addEventListener("click", () => setSidebar(false));
$("show-sidebar").addEventListener("click", () => setSidebar(true));
$("new-chat").addEventListener("click", async () => { await newChat(); closeSidebarOnMobile(); });
$("toggle-theme").addEventListener("click", toggleTheme);
$("toggle-files").addEventListener("click", () => openFilesWindow());
$("nav-files").addEventListener("click", () => { openFilesWindow(); closeSidebarOnMobile(); });
$("nav-connectors").addEventListener("click", () => openSettings("connectors"));
$("nav-skills").addEventListener("click", () => openSettings("skills"));
$("nav-models").addEventListener("click", () => openSettings("models"));
$("open-settings").addEventListener("click", () => openSettings("general"));
$("open-activity").addEventListener("click", () => openSettings("activity"));
$("chat-title").addEventListener("click", renameCurrent);
let searchTimer;
$("chat-search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(renderChats, 150);
});

on("show-workspace", () => openFilesWindow());
on("activation", showActivation);
on("health", (health) => renderStatus(health));
on("running", () => renderStatus(state.health));
on("chats-changed", renderChats);
on("session-changed", renderChats);
on("preferences-changed", renderMe);
on("open-settings", (tab) => openSettings(tab));
on("appearance-changed", applyAppearance);
on("attention", () => { if (document.hidden) document.title = "● Approval needed · Agentic Chatbox"; });
document.addEventListener("visibilitychange", () => { if (!document.hidden) renderTitle(); });

document.addEventListener("keydown", (event) => {
  const mod = event.metaKey || event.ctrlKey;
  if (!mod || event.altKey) return;
  const key = event.key.toLowerCase();
  if (key === "k" && !event.shiftKey) { event.preventDefault(); newChat(); }
  else if (key === "b") { event.preventDefault(); setSidebar($("app").classList.contains("sidebar-collapsed")); }
  else if (key === "e" && !event.shiftKey) { event.preventDefault(); openFilesWindow(); }
  else if (key === ",") { event.preventDefault(); openSettings("general"); }
  else if (key === "/" ) { event.preventDefault(); emit("focus-input"); }
});

refreshPreferences().then(renderMe);
renderChats();
refreshHealth();
setInterval(refreshHealth, 15000);
