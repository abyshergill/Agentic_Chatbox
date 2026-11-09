// Right pane: conversation, streaming events, tool cards, approvals, command palette.
import { $, api, el, emit, newSessionId, on, qs, session, setSessionId, store } from "./util.js";
import { renderMarkdown } from "./markdown.js";
import { activeFile, currentSelection, showDiff } from "./editor.js";
import { getActiveProject } from "./tree.js";
import { chooseSaveFolder } from "./folders.js";

const STATUS = {
  success: ["Done", "text-good"], failed: ["Failed", "text-bad"], denied: ["Denied", "text-bad"],
  rejected: ["Rejected", "text-warn"], running: ["Running", "text-soft"], waiting: ["Needs approval", "text-accent"], stopped: ["Stopped", "text-soft"],
};

let running = false;
let controller = null;
let commands = [];
let apps = [];
let attachments = [];
let includeFile = true;
let askedFile = null; // A file chosen with "Ask" in the explorer; it overrides the open editor tab.
const mentions = new Set(); // Files added with @ in the message box; sent as context with the next message.
let mentionTimer = null;
const DATA_FILE = /\.(csv|tsv|jsonl?)$/i;
let turn = null;
let paletteItems = [];
let paletteIndex = 0;

const messages = () => $("messages");

function scrollDown() {
  const box = messages();
  if (box.scrollHeight - box.scrollTop - box.clientHeight < 160) box.scrollTop = box.scrollHeight;
}

function setRunning(value) {
  running = value;
  $("send").classList.toggle("hidden", value);
  $("stop").classList.toggle("hidden", !value);
  $("stop").classList.toggle("inline-flex", value);
}

// ---- messages -------------------------------------------------------------

function userBubble(text) {
  messages().append(el("div", { class: "flex justify-end" },
    el("div", { class: "max-w-[88%] whitespace-pre-wrap rounded-lg bg-accent-wash px-3 py-2 text-sm [overflow-wrap:anywhere]", text })));
  messages().scrollTop = messages().scrollHeight;
}

function assistantBlock(markdown = "") {
  const body = el("div", { class: "prose-chat text-sm" });
  body.innerHTML = renderMarkdown(markdown);
  messages().append(body);
  return body;
}

function notice(level, text) {
  const tone = { bad: "border-bad bg-bad-wash", warn: "border-warn bg-warn-wash", info: "border-line bg-raised" }[level];
  messages().append(el("div", { class: `whitespace-pre-wrap rounded border-l-2 px-3 py-2 text-xs [overflow-wrap:anywhere] ${tone}`, role: level === "bad" ? "alert" : null, text }));
  scrollDown();
}

function emptyState() {
  const examples = [
    "List the files in projects/sample_app",
    "#git status",
    "#terminal Run the tests in projects/sample_app",
    "/db-migration Find the database settings in projects/sample_app and plan a move to PostgreSQL",
  ];
  messages().replaceChildren(el("div", { id: "empty-chat", class: "pt-6" },
    el("p", { class: "text-base font-semibold", text: "What should I work on?" }),
    el("p", { class: "mt-1 text-xs text-soft", text: "I work on your files and run tools here, on this machine. Anything that changes something waits for your approval." }),
    el("div", { class: "mt-4 space-y-1.5" }, examples.map((text) =>
      el("button", {
        class: "block w-full rounded border border-line bg-raised px-3 py-2 text-left font-mono text-2xs text-soft hover:border-accent hover:text-ink",
        text,
        onclick: () => { $("chat-input").value = text; $("chat-input").focus(); updatePalette(); },
      })))));
}

// ---- turn state -----------------------------------------------------------

function newTurn() {
  return { body: null, text: "", cards: new Map(), frame: 0 };
}

function renderText() {
  if (!turn.body) turn.body = assistantBlock();
  turn.body.innerHTML = renderMarkdown(turn.text);
  scrollDown();
}

function queueRender() {
  if (turn.frame) return;
  turn.frame = requestAnimationFrame(() => {
    if (!turn) return;
    turn.frame = 0;
    renderText();
    turn.body.classList.add("caret");
  });
}

function closeText(text) {
  cancelAnimationFrame(turn.frame);
  turn.frame = 0;
  turn.text = text;
  if (text) {
    renderText();
    turn.body.classList.remove("caret");
  } else if (turn.body) {
    turn.body.remove();
  }
  turn.body = null;
  turn.text = "";
}

function toolCard(event) {
  const status = el("span", { class: "ml-auto flex shrink-0 items-center gap-1.5 text-2xs" });
  const details = el("div", { class: "hidden border-t border-line" });
  const toggle = el("button", {
    class: "flex w-full items-center gap-2 px-2.5 py-1.5 text-left",
    "aria-expanded": "false",
    onclick: () => {
      const hidden = details.classList.toggle("hidden");
      toggle.setAttribute("aria-expanded", String(!hidden));
    },
  },
    el("span", { class: "shrink-0 font-mono text-2xs font-semibold", text: event.tool }),
    el("span", { class: "min-w-0 truncate font-mono text-2xs text-soft", text: event.target === "(unknown)" ? "" : event.target }),
    status);
  const actions = el("div");
  const root = el("div", { class: "overflow-hidden rounded border border-line bg-raised" }, toggle, actions, details);
  messages().append(root);
  const card = { root, status, details, actions, toggle };
  setCardStatus(card, "running");
  scrollDown();
  return card;
}

function setCardStatus(card, key) {
  const [label, color] = STATUS[key] || [key, "text-soft"];
  card.status.className = `ml-auto flex shrink-0 items-center gap-1.5 text-2xs font-medium ${color}`;
  card.status.replaceChildren(...(key === "running" ? [el("span", { class: "spinner" })] : []), label);
}

function previewNode(event) {
  if (!event.preview) return null;
  const wrap = event.is_diff ? "" : "whitespace-pre-wrap [overflow-wrap:anywhere]";
  const pre = el("pre", { class: `max-h-56 overflow-auto bg-canvas px-2.5 py-2 font-mono text-2xs leading-5 ${wrap}` });
  if (event.is_diff) {
    for (const line of event.preview.split("\n")) {
      const cls = line.startsWith("@@") || line.startsWith("---") || line.startsWith("+++") ? "diff-hunk"
        : line.startsWith("+") ? "diff-add" : line.startsWith("-") ? "diff-del" : "";
      pre.append(el("div", { class: cls, text: line || " " }));
    }
  } else {
    pre.textContent = event.preview;
  }
  return pre;
}

function approvalPanel(card, event) {
  setCardStatus(card, "waiting");
  card.root.classList.replace("border-line", event.destructive ? "border-bad" : "border-accent");

  const decide = async (approved) => {
    for (const button of panel.querySelectorAll("button")) button.disabled = true;
    try {
      await api(approved ? "/api/tools/approve" : "/api/tools/reject", {
        json: { session_id: session.id, approval_id: event.approval_id },
      });
    } catch (error) {
      notice("bad", error.message);
    }
  };

  const panel = el("div", { class: "border-t border-line", "data-approval": event.approval_id },
    el("p", { class: `px-2.5 py-1.5 text-xs ${event.destructive ? "font-medium text-bad" : "text-soft"}`,
      text: event.destructive ? "This moves it to the Trash (you can restore it from there)." : `#${event.app} wants to perform ${event.operation}.` }),
    previewNode(event),
    el("div", { class: "flex items-center gap-1.5 border-t border-line p-1.5" },
      el("button", {
        class: `h-7 rounded px-3 text-xs font-semibold ${event.destructive ? "bg-bad text-white" : "bg-accent text-accent-ink"} hover:opacity-90 disabled:opacity-40`,
        text: "Approve", onclick: () => decide(true),
      }),
      el("button", { class: "h-7 rounded border border-line px-3 text-xs hover:bg-hover disabled:opacity-40", text: "Reject", onclick: () => decide(false) }),
      event.is_diff && event.content !== null ? el("button", {
        class: "text-btn ml-auto", text: "Open diff in editor",
        onclick: () => showDiff(event.target, event.content),
      }) : null));
  card.actions.replaceChildren(panel);
  scrollDown();
}

// ---- stream events --------------------------------------------------------

function handleEvent(event) {
  switch (event.type) {
    case "activation":
      emit("activation", event);
      break;
    case "log":
      emit("log", event);
      break;
    case "token":
      turn.text += event.text;
      queueRender();
      break;
    case "assistant_step":
    case "assistant_final":
      closeText(event.text);
      break;
    case "tool_call":
      closeText(turn.text);
      turn.cards.set(event.id, toolCard(event));
      break;
    case "auto_approved": {
      const card = turn.cards.get(event.id);
      card?.actions.replaceChildren(el("p", { class: "chat-auto", text: `Allowed automatically: inside ${event.scope}` }));
      break;
    }
    case "approval_required": {
      const card = turn.cards.get(event.id);
      if (card) {
        approvalPanel(card, event);
        emit("show-pane", "pane-chat");
      }
      break;
    }
    case "approval_resolved": {
      const panel = messages().querySelector(`[data-approval="${CSS.escape(event.approval_id)}"]`);
      const card = [...turn.cards.values()].find((item) => item.actions.contains(panel));
      panel?.remove();
      if (card && event.approved) setCardStatus(card, "running");
      break;
    }
    case "tool_result": {
      const card = turn.cards.get(event.id);
      if (card) {
        card.finished = true;
        setCardStatus(card, event.status);
        card.root.classList.remove("border-accent", "border-bad");
        card.root.classList.add("border-line");
        card.details.replaceChildren(el("pre", {
          class: "max-h-64 overflow-auto whitespace-pre-wrap bg-canvas px-2.5 py-2 font-mono text-2xs leading-5 [overflow-wrap:anywhere]",
          text: event.output || "(no output)",
        }));
        if (event.status !== "success") {
          card.details.classList.remove("hidden");
          card.toggle.setAttribute("aria-expanded", "true");
        }
      }
      if (event.changed_paths?.length) {
        emit("paths-changed", event.changed_paths);
        emit("files-changed");
      }
      break;
    }
    case "refused":
      closeText(turn.text);
      notice("warn", event.message);
      break;
    case "error":
      closeText(turn.text);
      notice("bad", event.message);
      emit("log", { time: new Date().toTimeString().slice(0, 8), message: event.message, level: "bad" });
      break;
    case "stopped":
      closeText(turn.text);
      notice("info", event.message);
      break;
    case "summary":
      renderSummary(event.actions, event.model_replied);
      break;
  }
}

// A stopped or dropped run leaves nothing pending: remove its now-dead buttons.
function settleOpenCards() {
  for (const card of turn.cards.values()) {
    if (card.finished) continue;
    card.actions.replaceChildren();
    card.root.classList.remove("border-accent", "border-bad");
    card.root.classList.add("border-line");
    setCardStatus(card, "stopped");
  }
}

function renderSummary(actions, modelReplied) {
  if (!actions.length) {
    // Makes it obvious when a reply talks about work that never happened.
    if (modelReplied) messages().append(el("div", { class: "text-2xs text-faint", text: "No tools were run for this reply." }));
    scrollDown();
    return;
  }
  const counts = {};
  for (const action of actions) counts[action.status] = (counts[action.status] || 0) + 1;
  const words = { success: "succeeded", failed: "failed", denied: "denied", rejected: "rejected" };
  const text = Object.entries(counts).map(([key, count]) => `${count} ${words[key] || key}`).join(" · ");
  messages().append(el("div", { class: "text-2xs text-faint", text: `Confirmed by the backend: ${text}` }));
  scrollDown();
}

// ---- sending --------------------------------------------------------------

async function send(textOverride) {
  const input = $("chat-input");
  const text = (textOverride ?? input.value).trim();
  if (!text || running) return;
  $("empty-chat")?.remove();
  input.value = "";
  autosize();
  hidePalette();
  userBubble(text);
  emit("log-turn", text);
  const contextFiles = [...mentions]; // Sent with this message only.
  mentions.clear();
  renderChips();
  turn = newTurn();
  setRunning(true);
  controller = new AbortController();

  const file = askedFile || (includeFile ? activeFile() : null);
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      body: JSON.stringify({
        session_id: session.id,
        message: text,
        active_path: getActiveProject(),
        selected_file: file,
        selection: file && file === activeFile() ? currentSelection() : null,
        model: $("model-select").value || null,
        auto_approve: $("auto-approve").checked && getActiveProject() ? getActiveProject() : null,
        plan_only: $("plan-mode").checked,
        context_files: contextFiles,
      }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || response.statusText);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let index;
      while ((index = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, index);
        buffer = buffer.slice(index + 2);
        if (frame.startsWith("data: ")) handleEvent(JSON.parse(frame.slice(6)));
      }
    }
  } catch (error) {
    if (error.name !== "AbortError") notice("bad", `The connection to the server failed: ${error.message}`);
  } finally {
    if (turn) {
      closeText(turn.text);
      settleOpenCards();
    }
    turn = null;
    controller = null;
    setRunning(false);
    input.focus();
  }
}

// ---- command palette ------------------------------------------------------

function paletteOptions(value) {
  const match = value.match(/^([/#])([\w-]*)$/);
  if (!match) return [];
  const [, prefix, partial] = match;
  const needle = partial.toLowerCase();
  if (prefix === "/") {
    const auto = { value: "/auto", label: "/auto", hint: "Clear the skill and choose automatically", disabled: false };
    return [...commands.map((c) => ({
      value: c.command, label: c.command, hint: c.valid ? c.description : "Malformed skill file: cannot be activated", disabled: !c.valid,
    })), auto].filter((item) => item.label.slice(1).toLowerCase().includes(needle));
  }
  return apps.filter((a) => a.chat_command).map((a) => ({
    value: `#${a.id}`, label: `#${a.id}`, hint: a.enabled ? `${a.name}: ${a.operations.join(", ")}` : `${a.name}: disabled in permission.md`, disabled: !a.enabled,
  })).filter((item) => item.label.slice(1).toLowerCase().includes(needle));
}

function hidePalette() {
  paletteItems = [];
  $("palette").classList.add("hidden");
}

function renderPalette() {
  $("palette").replaceChildren(...paletteItems.map((item, index) => el("button", {
    class: `flex w-full items-baseline gap-2 px-2.5 py-1 text-left ${index === paletteIndex ? "bg-hover" : ""} ${item.disabled ? "opacity-50" : ""}`,
    role: "option", "aria-selected": String(index === paletteIndex),
    onmousedown: (event) => { event.preventDefault(); choosePalette(item); },
  },
    el("span", { class: "shrink-0 font-mono text-xs font-medium", text: item.label }),
    el("span", { class: "truncate text-2xs text-soft", text: item.hint }))));
  $("palette").classList.toggle("hidden", !paletteItems.length);
}

// '@name' at the cursor: the text being typed after the last @.
function mentionAtCursor() {
  const input = $("chat-input");
  const before = input.value.slice(0, input.selectionStart ?? input.value.length);
  const match = before.match(/(^|\s)@([^\s@]*)$/);
  return match ? { query: match[2], start: before.length - match[2].length - 1 } : null;
}

async function mentionOptions(query) {
  const project = getActiveProject();
  let entries = [];
  if (query) {
    entries = (await api(`/api/files/search?${qs({ session_id: session.id, q: query })}`)).entries;
    if (project) entries.sort((a, b) => Number(!a.path.startsWith(project)) - Number(!b.path.startsWith(project)));
  } else if (project) {
    entries = (await api(`/api/files/tree?${qs({ session_id: session.id, path: project })}`)).entries;
  }
  return entries.slice(0, 30).map((entry) => ({
    value: entry.path, label: `@${entry.path.split("/").pop()}${entry.type === "dir" ? "/" : ""}`, hint: entry.path, mention: true, disabled: false,
  }));
}

function updatePalette() {
  const mention = mentionAtCursor();
  if (mention) {
    clearTimeout(mentionTimer);
    mentionTimer = setTimeout(async () => {
      const current = mentionAtCursor();
      if (!current || current.query !== mention.query) return;
      try { paletteItems = await mentionOptions(mention.query); } catch { paletteItems = []; }
      paletteIndex = 0;
      renderPalette();
    }, 120);
    return;
  }
  paletteItems = paletteOptions($("chat-input").value);
  paletteIndex = 0;
  renderPalette();
}

function choosePalette(item) {
  const input = $("chat-input");
  if (item.mention) {
    const mention = mentionAtCursor();
    const name = item.value.split("/").pop();
    if (mention) {
      const end = mention.start + 1 + mention.query.length;
      input.value = `${input.value.slice(0, mention.start)}@${name} ${input.value.slice(end)}`;
      const caret = mention.start + name.length + 2;
      input.setSelectionRange(caret, caret);
    }
    mentions.add(item.value);
    renderChips();
  } else {
    input.value = `${item.value} `;
  }
  hidePalette();
  input.focus();
}

function autosize() {
  const input = $("chat-input");
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 192)}px`;
}

// ---- attachments and context chips ----------------------------------------

function chip(label, title, onRemove) {
  return el("span", { class: "inline-flex h-6 max-w-full items-center gap-1 rounded border border-line bg-raised pl-2 pr-1 text-2xs", title },
    el("span", { class: "truncate font-mono", text: label }),
    el("button", { class: "flex h-4 w-4 shrink-0 items-center justify-center rounded text-faint hover:bg-hover hover:text-ink", "aria-label": `Remove ${label}`, text: "✕", onclick: onRemove }));
}

function renderChips() {
  const file = activeFile();
  $("composer-chips").replaceChildren(
    ...(askedFile ? [chip(`asking about: ${askedFile.split("/").pop()}`, `Your questions are about ${askedFile}`, () => { askedFile = null; renderChips(); })] : []),
    ...(!askedFile && file && includeFile ? [chip(`open: ${file.split("/").pop()}`, `The assistant can see ${file}`, () => { includeFile = false; renderChips(); })] : []),
    ...[...mentions].map((path) => chip(`@${path.split("/").pop()}`, `The assistant will read ${path}`, () => { mentions.delete(path); renderChips(); })),
    ...attachments.map((a) => chip(a.name, a.saved_to ? `${a.size} bytes · saved to ${a.saved_to}` : `${a.size} bytes · attached only`, async () => {
      await api(`/api/files/upload/${a.id}?session_id=${session.id}`, { method: "DELETE" }).catch(() => {});
      attachments = attachments.filter((item) => item.id !== a.id);
      renderChips();
    })),
  );
}

// Attached files are also copied into the selected folder, so the assistant's tools can work on them and save
// what they generate beside them. With no folder selected, the user is asked where to save.
async function upload(files) {
  if (!files.length) return;
  let target = getActiveProject();
  if (!target) {
    target = await chooseSaveFolder(files.map((file) => file.name));
    if (target === null) return; // Cancelled.
    if (target && target !== getActiveProject()) emit("use-folder", target);
  }
  let saved = 0;
  for (const file of files) {
    const form = new FormData();
    form.append("session_id", session.id);
    form.append("file", file);
    if (target) form.append("target_dir", target);
    try {
      const result = await api("/api/files/upload", { method: "POST", body: form });
      attachments.push(result);
      if (result.saved_to) saved += 1;
      renderChips();
    } catch (error) {
      $("empty-chat")?.remove();
      notice("bad", `${file.name}: ${error.message}`);
    }
  }
  if (saved) {
    emit("files-changed");
    emit("log", { time: new Date().toTimeString().slice(0, 8), message: `Saved ${saved} attached file${saved === 1 ? "" : "s"} to ${target}`, level: "info" });
  }
}

// ---- restore and init -----------------------------------------------------

async function restore() {
  const state = await api(`/api/session?session_id=${session.id}`);
  attachments = state.attachments;
  renderChips();
  $("chat-command").textContent = state.pinned_command || "";
  if (!state.messages.length) return emptyState();
  messages().replaceChildren();
  for (const message of state.messages) {
    if (message.role === "user") userBubble(message.content);
    else assistantBlock(message.content);
  }
  messages().scrollTop = messages().scrollHeight;
}

export async function loadPalette() {
  [commands, apps] = await Promise.all([api("/api/commands"), api("/api/permissions")]);
}

export function initChat() {
  const input = $("chat-input");
  $("send").addEventListener("click", () => send());
  $("stop").addEventListener("click", () => api("/api/chat/stop", { json: { session_id: session.id } }));
  $("attach").addEventListener("click", () => $("file-input").click());
  $("file-input").addEventListener("change", (event) => {
    upload([...event.target.files]);
    event.target.value = "";
  });
  input.addEventListener("input", () => { autosize(); updatePalette(); });
  input.addEventListener("focus", () => loadPalette().then(updatePalette).catch(() => {}));
  input.addEventListener("blur", hidePalette);
  input.addEventListener("keydown", (event) => {
    if (paletteItems.length) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        paletteIndex = (paletteIndex + (event.key === "ArrowDown" ? 1 : -1) + paletteItems.length) % paletteItems.length;
        return renderPalette();
      }
      if (event.key === "Tab" || event.key === "Enter") {
        event.preventDefault();
        return choosePalette(paletteItems[paletteIndex]);
      }
      if (event.key === "Escape") return hidePalette();
    }
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      send();
    }
  });
  // A new chat gets a new id; the previous one stays in History.
  $("new-chat").addEventListener("click", () => switchChat(newSessionId()));
  $("open-history").addEventListener("click", openHistory);
  $("close-history").addEventListener("click", () => $("history-dialog").close());
  let searchTimer;
  $("history-search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(renderHistoryList, 150);
  });
  $("clear-skill").addEventListener("click", () => send("/auto"));
  let lastFile = null;
  on("active-file", (path) => {
    if (path !== lastFile) includeFile = true; // A dismissed chip stays dismissed for that file.
    lastFile = path;
    renderChips();
  });
  on("ask-file", (path) => {
    askedFile = path;
    renderChips();
    const input = $("chat-input");
    // Data files go to the analyst, which has tools for exact numbers. The user can edit or remove the prefix.
    if (DATA_FILE.test(path) && !input.value.trim()) input.value = "/analyze ";
    emit("show-pane", "pane-chat");
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  });
  on("notice", ({ level, message }) => { $("empty-chat")?.remove(); notice(level, message); });
  initModes();
  restore().catch((error) => notice("bad", error.message));
  loadPalette().catch(() => {});
}

// ---- permission modes -------------------------------------------------------

const AUTO_HELP = "Actions whose files are all inside this project run without asking: edits, new files, renames, moves to Trash, data outputs, and commands (sandboxed to this folder). Anything outside it still asks. Disabled applications stay disabled.";

function initModes() {
  const auto = $("auto-approve");
  const plan = $("plan-mode");
  auto.checked = store.get("workspace.autoApprove") === "1";
  plan.checked = store.get("workspace.planMode") === "1";
  const sync = () => {
    const project = getActiveProject();
    auto.disabled = !project;
    const name = project && project.startsWith("/") ? project.split("/").pop() : project;
    $("auto-approve-label").textContent = project ? `Allow all actions in ${name}` : "Allow all actions (select a project first)";
    $("auto-approve-wrap").title = project ? `${project}\n\n${AUTO_HELP}` : AUTO_HELP;
    $("auto-approve-wrap").classList.toggle("on", auto.checked && Boolean(project) && !plan.checked);
    $("auto-approve-wrap").classList.toggle("disabled", !project);
    $("plan-mode-wrap").classList.toggle("on", plan.checked);
    $("chat-input").placeholder = plan.checked
      ? "Plan mode: describe the change; the assistant investigates and proposes a plan without changing anything"
      : "Ask anything. Type / for skills, # for applications, @ to add a file";
  };
  auto.addEventListener("change", () => { store.set("workspace.autoApprove", auto.checked ? "1" : "0"); sync(); });
  plan.addEventListener("change", () => { store.set("workspace.planMode", plan.checked ? "1" : "0"); sync(); });
  on("project-changed", sync);
  sync();
}

// ---- history (saved in SQLite) ---------------------------------------------------

async function switchChat(id) {
  if (running) {
    notice("warn", "A task is still running. Stop it before switching conversations.");
    return false;
  }
  setSessionId(id);
  askedFile = null;
  mentions.clear();
  emit("activation", { cleared: true });
  emit("log-clear");
  await restore().catch((error) => notice("bad", error.message));
  emit("session-changed", id);
  $("chat-input").focus();
  return true;
}

function when(seconds) {
  const date = new Date(seconds * 1000);
  const days = Math.floor((Date.now() - date) / 86_400_000);
  if (days < 1 && new Date().getDate() === date.getDate()) return `Today ${date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
  if (days < 2) return "Yesterday";
  if (days < 7) return date.toLocaleDateString([], { weekday: "long" });
  return date.toLocaleDateString();
}

function historyRow(chat) {
  const current = chat.id === session.id;
  const title = el("span", { class: "hs-title", text: chat.title });
  const open = el("button", {
    class: `hs-open${current ? " current" : ""}`, title: current ? "This conversation" : "Continue this conversation",
    onclick: async () => { if (await switchChat(chat.id)) $("history-dialog").close(); },
  }, title, el("span", { class: "hs-meta", text: `${when(chat.updated)} · ${chat.messages} message${chat.messages === 1 ? "" : "s"}${current ? " · open now" : ""}` }));

  const rename = el("button", { class: "hs-act", title: "Rename", "aria-label": `Rename ${chat.title}`, text: "✎" });
  rename.addEventListener("click", () => {
    const field = el("input", { class: "field hs-edit", value: chat.title, "aria-label": "Conversation name" });
    const save = async () => {
      const value = field.value.trim();
      if (value && value !== chat.title) {
        try { await api(`/api/chats/${chat.id}/rename`, { json: { title: value } }); } catch (error) { notice("bad", error.message); }
      }
      renderHistoryList();
    };
    field.addEventListener("keydown", (event) => {
      event.stopPropagation();
      if (event.key === "Enter") { event.preventDefault(); save(); }
      if (event.key === "Escape") { event.preventDefault(); renderHistoryList(); }
    });
    field.addEventListener("blur", save);
    open.replaceWith(field);
    field.focus();
    field.select();
  });

  // Two clicks to delete: the first arms the button, so a slip of the mouse does not lose a conversation.
  const remove = el("button", { class: "hs-act", title: "Delete", "aria-label": `Delete ${chat.title}`, text: "🗑" });
  remove.addEventListener("click", async () => {
    if (!remove.classList.contains("armed")) {
      remove.classList.add("armed");
      remove.textContent = "Delete?";
      setTimeout(() => { remove.classList.remove("armed"); remove.textContent = "🗑"; }, 3000);
      return;
    }
    try {
      await api(`/api/chats/${chat.id}`, { method: "DELETE" });
      if (current) await switchChat(newSessionId());
      renderHistoryList();
    } catch (error) {
      notice("bad", error.message);
    }
  });
  return el("li", { class: "hs-row" }, open, rename, remove);
}

async function renderHistoryList() {
  const list = $("history-list");
  try {
    const { chats } = await api(`/api/chats?${qs({ q: $("history-search").value.trim() })}`);
    list.replaceChildren(...(chats.length ? chats.map(historyRow)
      : [el("li", { class: "hs-empty", text: $("history-search").value ? "No conversations match." : "No saved conversations yet. They appear here after the first reply." })]));
  } catch (error) {
    list.replaceChildren(el("li", { class: "hs-empty", text: error.message }));
  }
}

async function openHistory() {
  $("history-search").value = "";
  $("history-dialog").showModal();
  await renderHistoryList();
  $("history-search").focus();
}
