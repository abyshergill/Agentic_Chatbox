// The conversation: welcome screen, streaming replies, tool steps, approvals, composer, palette and menus.
import { $, api, el, emit, newSessionId, on, qs, session, setSessionId, store } from "./util.js";
import { renderMarkdown } from "./markdown.js";
import { activeFile, getActiveProject, openFilesWindow, openOutside, setActiveProject, showDiff } from "./workspace-link.js";
import { chooseSaveFolder, openFolder } from "./folders.js";
import { icon, toolIcon } from "./icons.js";
import { closePopover, copyText, menuItem, popover, switchEl, tag, toast } from "./ui.js";
import { chatConnectors, log, modelLabel, refreshAgentSkills, refreshHealth, setConnectorOn, setModel, state } from "./state.js";

const STATUS = {
  success: "Done", failed: "Failed", denied: "Denied", rejected: "Declined", running: "Running",
  waiting: "Needs approval", stopped: "Stopped",
};
const DATA_FILE = /\.(csv|tsv|jsonl?|xlsx?|parquet)$/i;

let running = false;
let controller = null;
let commands = [];
let apps = [];
let attachments = [];
let includeFile = true;
let askedFile = null; // A file chosen with "Ask" in the explorer; overrides the open editor tab.
const mentions = new Set(); // Files added with @; sent with the next message.
const chosenSkills = new Set(); // Agent Skills turned on for the next message.
let mentionTimer = null;
let turn = null;
let paletteItems = [];
let paletteIndex = 0;
let lastUserText = "";

const thread = () => $("thread");
const input = () => $("chat-input");

function scrollDown(force = false) {
  const box = $("messages");
  if (force || box.scrollHeight - box.scrollTop - box.clientHeight < 200) box.scrollTop = box.scrollHeight;
}

function setEmpty(empty) {
  $("conversation").classList.toggle("empty", empty);
}

function setRunning(value) {
  running = value;
  $("send").classList.toggle("hidden", value);
  $("stop").classList.toggle("hidden", !value);
  emit("running", value);
  updateSend();
}

function updateSend() {
  $("send").disabled = running || !input().value.trim();
}

// ---- welcome ----------------------------------------------------------------------

function greeting() {
  const hour = new Date().getHours();
  const part = hour < 5 ? "Working late" : hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  const name = state.preferences.display_name;
  return name ? `${part}, ${name}` : part;
}

function welcome() {
  setEmpty(true);
  const suggestions = [
    ["file", "Summarize a document", "Summarize @"],
    ["data", "Analyze a dataset", "/analyze "],
    ["terminal", "Run the tests", "#terminal Run the tests in "],
    ["git", "Review my changes", "Review my uncommitted changes"],
    ["sparkles", "Write a new skill", "Help me write a new skill that "],
  ];
  thread().replaceChildren(
    el("div", { class: "welcome" },
      el("div", { class: "welcome-mark" }, icon("logo")),
      el("h1", { text: greeting() }),
      el("p", { text: "Your local agent: chat, work on files, run tools and connectors — with your approval." })),
  );
  const chips = el("div", { class: "suggestions" }, suggestions.map(([name, label, text]) => el("button", {
    class: "suggestion", onclick: () => {
      input().value = text;
      input().focus();
      autosize();
      updatePalette();
      updateSend();
    },
  }, icon(name), label)));
  thread().append(chips);
}

// ---- messages ----------------------------------------------------------------------

function userMessage(text, files = []) {
  setEmpty(false);
  if ($("thread").querySelector(".welcome")) thread().replaceChildren();
  const node = el("div", { class: "msg-user" },
    files.length ? el("div", { class: "msg-files" }, files.map((name) => el("span", { text: name }))) : null,
    el("div", { class: "bubble", text }));
  thread().append(node);
  scrollDown(true);
}

function assistantMessage(markdown) {
  const root = el("div", { class: "msg-assistant" });
  const reply = el("div", { class: "reply" });
  reply.innerHTML = renderMarkdown(markdown);
  linkifyFiles(reply);
  root.append(reply, actionsBar(() => markdown, null));
  thread().append(root);
  return root;
}

function actionsBar(getText, meta) {
  const copy = el("button", { class: "ghost sm", title: "Copy", "aria-label": "Copy reply" }, icon("copy", "i i-sm"));
  copy.addEventListener("click", async () => {
    if (await copyText(getText())) {
      copy.replaceChildren(icon("check", "i i-sm"));
      setTimeout(() => copy.replaceChildren(icon("copy", "i i-sm")), 1400);
    }
  });
  const retry = el("button", {
    class: "ghost sm", title: "Send the last message again", "aria-label": "Retry",
    onclick: () => { if (lastUserText && !running) send(lastUserText); },
  }, icon("retry", "i i-sm"));
  return el("div", { class: "msg-actions" }, copy, retry, meta ? el("span", { class: "msg-meta", text: meta }) : null);
}

function notice(level, text, parent = null) {
  const node = el("div", { class: `notice ${level}`, role: level === "bad" ? "alert" : null, text });
  if (parent) parent.insertBefore(node, turn?.indicator && parent === turn.root ? turn.indicator : null);
  else { setEmpty(false); thread().append(node); }
  scrollDown();
}

// ---- generated files: links that open outside the app -----------------------------------------

const OUTPUT = /\.(html?|pptx?|key|docx?|rtf|odt|xlsx?|xlsm|ods|odp|csv|tsv|pdf|png|jpe?g|gif|webp|svg|mp4|webm|mov|md|txt|py|ipynb|js|mjs|ts|css|json|ya?ml|sql|sh)$/i;
// [pattern, icon, kind, action, where]: "outside" opens in the default app or browser, "files" in the Files window.
const FILE_KINDS = [
  [/\.html?$/i, "globe", "Web page", "Open in browser", "outside"],
  [/\.(pptx?|key|odp)$/i, "file", "Presentation", "Open", "outside"],
  [/\.(docx?|rtf|odt)$/i, "file", "Document", "Open", "outside"],
  [/\.(xlsx?|xlsm|ods)$/i, "data", "Spreadsheet", "Open", "outside"],
  [/\.(csv|tsv)$/i, "data", "Data file", "Open", "outside"],
  [/\.pdf$/i, "file", "PDF", "Open", "outside"],
  [/\.(png|jpe?g|gif|webp|svg)$/i, "image", "Image", "Open", "outside"],
  [/\.(mp4|webm|mov)$/i, "video", "Video", "Play", "outside"],
  [/\.md$/i, "file", "Markdown", "Open", "files"],
  [/\.(py|ipynb)$/i, "terminal", "Python script", "Open", "files"],
  [/\.(js|mjs|ts|css|json|ya?ml|sql|sh|txt)$/i, "file", "Text file", "Open", "files"],
];
const kindOf = (path) => FILE_KINDS.find(([re]) => re.test(path)) || [null, "file", "File", "Open", "files"];
const openPath = (path) => (kindOf(path)[4] === "files" ? openFilesWindow(path) : openOutside(path));
const mediaUrl = (path) => `/api/files/media?${qs({ session_id: session.id, path, base: getActiveProject() || "" })}`;

function preview(path) {
  if (/\.(png|jpe?g|gif|webp)$/i.test(path)) {
    const image = el("img", { class: "out-image", src: mediaUrl(path), alt: path.split("/").pop(), loading: "lazy", title: "Open in your image viewer" });
    image.addEventListener("click", () => openOutside(path));
    image.addEventListener("error", () => image.remove());
    return image;
  }
  if (/\.(mp4|webm)$/i.test(path)) {
    const video = el("video", { class: "out-video", src: mediaUrl(path), controls: true, preload: "metadata", playsinline: true });
    video.addEventListener("error", () => video.remove());
    return video;
  }
  return null;
}

function fileCard(paths) {
  const rows = paths.map((path) => {
    const [, iconName, kind, action] = kindOf(path);
    const name = path.split("/").pop();
    return el("div", { class: "out-item" },
      preview(path),
      el("div", { class: "out-row" },
        el("span", { class: "out-icon" }, icon(iconName, "i i-sm")),
        el("button", { class: "out-main", title: `${action}: ${path}`, onclick: () => openPath(path) },
          el("span", { class: "out-name", text: name }),
          el("span", { class: "out-sub", text: `${kind} · ${path}` })),
        el("button", { class: "out-open", text: action, onclick: () => openPath(path) }),
        el("button", { class: "ghost sm", title: "Show in Finder", "aria-label": `Show ${name} in Finder`, onclick: () => openOutside(path, { reveal: true }) }, icon("folder", "i i-sm"))));
  });
  return el("div", { class: "outputs" }, el("div", { class: "out-title", text: paths.length === 1 ? "Created a file" : `Created ${paths.length} files` }), ...rows);
}

// File paths the reply mentions in `code` become links too, so restored chats keep them.
function linkifyFiles(root) {
  for (const code of root.querySelectorAll("code")) {
    if (code.closest("pre")) continue;
    const text = code.textContent.trim();
    if (!OUTPUT.test(text) || /\s/.test(text) || text.length > 400) continue;
    code.replaceWith(fileLink(text));
  }
  // Paths written as plain text, e.g. "saved to projects/acme/dashboards/sales.html".
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: (node) => (node.parentElement.closest("pre, code, a, .file-link") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
  });
  const nodes = [];
  while (walker.nextNode()) if (PLAIN_PATH.test(walker.currentNode.nodeValue)) nodes.push(walker.currentNode);
  for (const node of nodes) {
    const parts = node.nodeValue.split(PLAIN_PATH_SPLIT);
    node.replaceWith(...parts.map((part, index) => (index % 2 ? fileLink(part) : part)));
  }
}

const EXT = "html?|pptx?|key|docx?|rtf|odt|xlsx?|xlsm|ods|odp|csv|tsv|pdf|png|jpe?g|gif|webp|svg|mp4|webm|mov|md|py|ipynb";
const PLAIN_PATH = new RegExp(`(?:^|[\\s(])(?:/|[\\w.-]+/)[\\w./-]*\\.(?:${EXT})(?=$|[\\s),.;:!?])`, "i");
const PLAIN_PATH_SPLIT = new RegExp(`((?:/|[\\w.-]+/)[\\w./-]*\\.(?:${EXT}))(?=$|[\\s),.;:!?])`, "gi");

function fileLink(path) {
  const [, iconName, , action] = kindOf(path);
  return el("a", { class: "file-link", href: "#", "data-open-path": path, title: `${action}: ${path}` }, icon(iconName, "i i-sm"), path);
}

// Copy buttons on code blocks (rendered as HTML strings by markdown.js), and file links.
function onThreadClick(event) {
  const fileLink = event.target.closest("[data-open-path]");
  if (fileLink) {
    event.preventDefault();
    openPath(fileLink.dataset.openPath);
    return;
  }
  const button = event.target.closest("[data-copy]");
  if (!button) return;
  const code = button.closest(".code-block")?.querySelector("code");
  if (!code) return;
  copyText(code.textContent).then((ok) => {
    if (!ok) return;
    button.textContent = "Copied";
    setTimeout(() => { button.textContent = "Copy"; }, 1400);
  });
}

// ---- a running turn -----------------------------------------------------------------

function newTurn() {
  const indicator = el("div", { class: "thinking" }, icon("spark", "spark spin"), el("span", { text: "Thinking…" }));
  const root = el("div", { class: "msg-assistant" }, indicator);
  thread().append(root);
  return { root, indicator, reply: null, text: "", final: "", steps: null, cards: new Map(), frame: 0, toolCount: 0, outputs: new Set() };
}

function place(node) {
  turn.root.insertBefore(node, turn.indicator);
  scrollDown();
}

function thinking(label) {
  if (!turn) return;
  turn.indicator.classList.toggle("hidden", label === null);
  if (label) turn.indicator.lastChild.textContent = label;
}

function renderText() {
  if (!turn.reply) {
    turn.reply = el("div", { class: "reply streaming" });
    place(turn.reply);
    thinking(null);
  }
  turn.reply.innerHTML = renderMarkdown(turn.text);
  scrollDown();
}

function queueRender() {
  if (turn.frame) return;
  turn.frame = requestAnimationFrame(() => {
    if (!turn) return;
    turn.frame = 0;
    renderText();
  });
}

function closeText(text) {
  cancelAnimationFrame(turn.frame);
  turn.frame = 0;
  turn.text = text;
  if (text) {
    renderText();
    turn.reply.classList.remove("streaming");
    linkifyFiles(turn.reply);
    turn.final = text;
  } else if (turn.reply) {
    turn.reply.remove();
  }
  turn.reply = null;
  turn.text = "";
}

function stepsGroup() {
  if (turn.steps) return turn.steps;
  const label = el("span", { text: "Working…" });
  const head = el("button", { class: "steps-head", "aria-expanded": "false" }, icon("tool", "i i-sm"), label, icon("chevron", "i i-sm chev"));
  const body = el("div", { class: "steps-body" });
  const box = el("div", { class: "steps" }, head, body);
  head.addEventListener("click", () => {
    const open = box.classList.toggle("open");
    head.setAttribute("aria-expanded", String(open));
  });
  place(box);
  turn.steps = { box, body, label, count: 0 };
  return turn.steps;
}

function updateStepsLabel(running) {
  const group = turn.steps;
  if (!group) return;
  const n = group.count;
  group.label.textContent = running ? `Running ${running}…` : `Used ${n} tool${n === 1 ? "" : "s"}`;
}

function stepCard(event) {
  const group = stepsGroup();
  group.count += 1;
  turn.toolCount += 1;
  const status = el("span", { class: "step-status" });
  const detail = el("div", { class: "step-detail" });
  const args = event.args && Object.keys(event.args).length ? JSON.stringify(event.args, null, 2) : "";
  if (args) detail.append(el("div", { class: "label", text: "Input" }), el("pre", { text: args }));
  const row = el("button", { class: "step-row", "aria-expanded": "false" },
    el("span", { class: `step-icon${event.app === "mcp" ? " mcp" : ""}` }, icon(toolIcon(event.app), "i i-sm")),
    el("span", { class: "step-name", text: prettyTool(event.tool) }),
    el("span", { class: "step-target", text: event.target === "(unknown)" ? "" : event.target }),
    status);
  const actions = el("div");
  const node = el("div", { class: "step" }, row, actions, detail);
  row.addEventListener("click", () => {
    const open = node.classList.toggle("open");
    row.setAttribute("aria-expanded", String(open));
  });
  group.body.append(node);
  const card = { node, row, status, detail, actions, tool: event.tool, finished: false };
  setStatus(card, "running");
  updateStepsLabel(prettyTool(event.tool));
  scrollDown();
  return card;
}

function prettyTool(name) {
  const mcp = name.match(/^mcp_([^_]+)_(.+)$/);
  return mcp ? `${mcp[2]}` : name.replace(/_/g, " ");
}

function setStatus(card, key) {
  card.status.className = `step-status ${key}`;
  card.status.replaceChildren(...(key === "running" ? [el("span", { class: "spinner" })] : []), STATUS[key] || key);
}

function previewNode(event) {
  if (!event.preview) return null;
  const pre = el("pre", { class: event.is_diff ? "diff" : "" });
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
  setStatus(card, "waiting");
  turn.steps.box.classList.add("open");
  thinking(null);
  const decide = async (approved, always = false) => {
    for (const button of panel.querySelectorAll("button")) button.disabled = true;
    try {
      if (always && event.mcp) {
        await api(`/api/mcp/servers/${encodeURIComponent(event.mcp.server)}/always-allow`, { json: { tool: event.mcp.tool, allowed: true } });
        toast(`${event.mcp.tool} on ${event.mcp.server} will run without asking. Change this in Settings › Connectors.`);
      }
      await api(approved ? "/api/tools/approve" : "/api/tools/reject", { json: { session_id: session.id, approval_id: event.approval_id } });
    } catch (error) {
      notice("bad", error.message, turn?.root);
    }
  };
  const title = event.mcp ? `Allow ${event.mcp.server} to use ${event.mcp.tool}?`
    : event.destructive ? `Move to Trash?` : `Allow ${prettyTool(event.tool)}?`;
  const detail = event.mcp ? "A connector tool wants to run with the input below."
    : event.destructive ? "This moves it to the Trash, where you can restore it."
      : `#${event.app} wants to ${event.operation.replace(/_/g, " ")}: ${event.target}`;
  const panel = el("div", { class: `approval${event.destructive ? " danger" : ""}`, "data-approval": event.approval_id },
    el("div", { class: "approval-head" },
      el("span", { class: "approval-icon" }, icon(event.destructive ? "warn" : event.mcp ? "plug" : "shield", "i i-sm")),
      el("div", {}, el("strong", { text: title }), el("p", { text: detail }))),
    previewNode(event),
    el("div", { class: "approval-buttons" },
      el("button", { class: `btn sm ${event.destructive ? "danger solid" : "primary"}`, text: event.mcp ? "Allow once" : "Allow", onclick: () => decide(true) }),
      event.mcp ? el("button", { class: "btn sm", text: "Always allow", title: "Run this tool without asking from now on", onclick: () => decide(true, true) }) : null,
      el("button", { class: "btn sm", text: "Decline", onclick: () => decide(false) }),
      event.is_diff && event.content !== null ? el("button", {
        class: "btn sm link", text: "Open diff in editor", style: "margin-left:auto",
        onclick: () => showDiff(event.target, event.content),
      }) : null));
  card.actions.replaceChildren(panel);
  scrollDown(true);
  emit("attention");
}

function handleEvent(event) {
  switch (event.type) {
    case "activation":
      emit("activation", event);
      break;
    case "log":
      log(event.message, event.level);
      if (/^Thinking/.test(event.message)) thinking(event.message.replace("Thinking", "Thinking"));
      break;
    case "token":
      turn.text += event.text;
      queueRender();
      break;
    case "assistant_step":
    case "assistant_final":
      closeText(event.text);
      if (event.type === "assistant_final") turn.steps && updateStepsLabel(null);
      thinking(event.type === "assistant_step" ? "Thinking…" : null);
      break;
    case "tool_call":
      closeText(turn.text);
      turn.cards.set(event.id, stepCard(event));
      thinking(null);
      break;
    case "auto_approved": {
      const card = turn.cards.get(event.id);
      card?.actions.replaceChildren(el("div", { class: "step-note", text: `Allowed automatically (${event.scope})` }));
      break;
    }
    case "approval_required": {
      const card = turn.cards.get(event.id);
      if (card) approvalPanel(card, event);
      break;
    }
    case "approval_resolved": {
      const card = [...turn.cards.values()].find((item) => item.actions.querySelector(`[data-approval="${CSS.escape(event.approval_id)}"]`));
      if (card) {
        card.actions.replaceChildren();
        if (event.approved) setStatus(card, "running");
      }
      break;
    }
    case "tool_result": {
      const card = turn.cards.get(event.id);
      if (card) {
        card.finished = true;
        setStatus(card, event.status);
        card.detail.append(el("div", { class: "label", text: "Result" }), el("pre", { text: event.output || "(no output)" }));
        if (event.status !== "success" && event.status !== "rejected") {
          card.node.classList.add("open");
          turn.steps.box.classList.add("open");
        }
      }
      updateStepsLabel(null);
      thinking("Thinking…");
      if (event.status === "success" && event.tool !== "delete_file") {
        for (const path of event.changed_paths || []) if (OUTPUT.test(path)) turn.outputs.add(path);
      }
      if (event.changed_paths?.length) {
        emit("paths-changed", event.changed_paths);
        emit("files-changed");
      }
      break;
    }
    case "refused":
      closeText(turn.text);
      notice("warn", event.message, turn.root);
      break;
    case "error":
      closeText(turn.text);
      notice("bad", event.message, turn.root);
      log(event.message, "bad");
      break;
    case "stopped":
      closeText(turn.text);
      notice("info", event.message, turn.root);
      break;
    case "summary":
      finishTurn(event.actions, event.model_replied);
      break;
  }
}

function finishTurn(actions, modelReplied) {
  thinking(null);
  if (turn.steps) updateStepsLabel(null);
  const counts = {};
  for (const action of actions) counts[action.status] = (counts[action.status] || 0) + 1;
  const parts = Object.entries(counts).map(([key, n]) => `${n} ${STATUS[key]?.toLowerCase() || key}`);
  const meta = actions.length ? `${actions.length} action${actions.length === 1 ? "" : "s"}: ${parts.join(", ")}`
    : modelReplied ? "No tools used" : "";
  const final = turn.final;
  if (turn.outputs.size) place(fileCard([...turn.outputs]));
  if (final || meta) place(actionsBar(() => final, meta));
}

// A stopped or dropped run leaves nothing pending.
function settleOpenCards() {
  for (const card of turn.cards.values()) {
    if (card.finished) continue;
    card.actions.replaceChildren();
    setStatus(card, "stopped");
  }
  thinking(null);
  if (turn.steps) updateStepsLabel(null);
}

// ---- sending ------------------------------------------------------------------------

async function send(textOverride) {
  const text = (textOverride ?? input().value).trim();
  if (!text || running) return;
  input().value = "";
  autosize();
  hidePalette();
  closePopover();
  const files = [...mentions].map((p) => `@${p.split("/").pop()}`).concat(attachments.map((a) => a.name), [...chosenSkills].map((s) => `skill: ${s}`));
  userMessage(text, files);
  lastUserText = text;
  const contextFiles = [...mentions];
  const skills = [...chosenSkills];
  mentions.clear();
  chosenSkills.clear();
  renderChips();
  turn = newTurn();
  setRunning(true);
  controller = new AbortController();
  log(`You: ${text.slice(0, 160)}`);

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
        selection: null,
        model: state.model || null,
        auto_approve: $("auto-approve").checked && getActiveProject() ? getActiveProject() : null,
        plan_only: $("plan-mode").checked,
        context_files: contextFiles,
        skills,
        mcp_servers: chatConnectors(),
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
    if (error.name !== "AbortError") notice("bad", `The connection to the server failed: ${error.message}`, turn?.root);
  } finally {
    if (turn) {
      closeText(turn.text);
      settleOpenCards();
    }
    turn = null;
    controller = null;
    setRunning(false);
    input().focus();
    emit("chats-changed");
  }
}

// ---- palette: / skills and agent skills, # applications, @ files ---------------------------

function paletteOptions(value) {
  const match = value.match(/^([/#])([\w-]*)$/);
  if (!match) return [];
  const [, prefix, partial] = match;
  const needle = partial.toLowerCase();
  if (prefix === "/") {
    const agentSkills = state.agentSkills.filter((s) => s.enabled && !s.errors.length).map((s) => ({
      value: s.name, label: `/${s.name}`, hint: s.description, skill: true, group: "Agent skills",
    }));
    const auto = { value: "/auto", label: "/auto", hint: "Clear the active agent and choose automatically", group: "Agents" };
    return [
      ...agentSkills,
      ...commands.map((c) => ({ value: c.command, label: c.command, hint: c.valid ? c.description : "Malformed skill file", disabled: !c.valid, group: "Agents" })),
      auto,
    ].filter((item) => item.label.slice(1).toLowerCase().includes(needle));
  }
  return apps.filter((a) => a.chat_command).map((a) => ({
    value: `#${a.id}`, label: `#${a.id}`, hint: a.enabled ? `${a.name}: ${a.operations.join(", ")}` : `${a.name}: disabled in permission.md`, disabled: !a.enabled, group: "Applications",
  })).filter((item) => item.label.slice(1).toLowerCase().includes(needle));
}

function hidePalette() {
  paletteItems = [];
  $("palette").classList.add("hidden");
}

function renderPalette() {
  const nodes = [];
  let group = null;
  paletteItems.forEach((item, index) => {
    if (item.group && item.group !== group) {
      group = item.group;
      nodes.push(el("div", { class: "p-group", text: group }));
    }
    nodes.push(el("button", {
      class: `${index === paletteIndex ? "active" : ""} ${item.disabled ? "disabled" : ""}`,
      role: "option", "aria-selected": String(index === paletteIndex),
      onmousedown: (event) => { event.preventDefault(); choosePalette(item); },
    }, el("span", { class: "p-label", text: item.label }), el("span", { class: "p-hint", text: item.hint || "" })));
  });
  $("palette").replaceChildren(...nodes);
  $("palette").classList.toggle("hidden", !paletteItems.length);
  $("palette").querySelector(".active")?.scrollIntoView({ block: "nearest" });
}

function mentionAtCursor() {
  const box = input();
  const before = box.value.slice(0, box.selectionStart ?? box.value.length);
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
  } else {
    entries = (await api(`/api/files/tree?${qs({ session_id: session.id })}`)).entries;
  }
  return entries.slice(0, 30).map((entry) => ({
    value: entry.path, label: `@${entry.name || entry.path.split("/").pop()}${entry.type === "dir" ? "/" : ""}`, hint: entry.path, mention: true, group: "Files",
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
  paletteItems = paletteOptions(input().value);
  paletteIndex = 0;
  renderPalette();
}

function choosePalette(item) {
  if (item.disabled) return;
  const box = input();
  if (item.mention) {
    const mention = mentionAtCursor();
    const name = item.value.split("/").pop();
    if (mention) {
      const end = mention.start + 1 + mention.query.length;
      box.value = `${box.value.slice(0, mention.start)}@${name} ${box.value.slice(end)}`;
      const caret = mention.start + name.length + 2;
      box.setSelectionRange(caret, caret);
    }
    mentions.add(item.value);
  } else if (item.skill) {
    chosenSkills.add(item.value);
    box.value = "";
  } else {
    box.value = `${item.value} `;
  }
  renderChips();
  hidePalette();
  updateSend();
  box.focus();
}

function autosize() {
  const box = input();
  box.style.height = "auto";
  box.style.height = `${Math.min(box.scrollHeight, 280)}px`;
}

// ---- chips: context files, attachments, chosen skills -----------------------------------

function chip(label, title, onRemove, kind = "file", iconName = "file") {
  return el("span", { class: `chip ${kind}`, title },
    icon(iconName),
    el("span", { text: label }),
    el("button", { "aria-label": `Remove ${label}`, onclick: onRemove }, icon("x", "i i-sm")));
}

function renderChips() {
  const file = activeFile();
  $("composer-chips").replaceChildren(
    ...[...chosenSkills].map((name) => chip(name, "Skill: its instructions go to the assistant with this message", () => { chosenSkills.delete(name); renderChips(); }, "skill", "sparkles")),
    ...(askedFile ? [chip(askedFile.split("/").pop(), `Your questions are about ${askedFile}`, () => { askedFile = null; renderChips(); }, "file", "eye")] : []),
    ...(!askedFile && file && includeFile ? [chip(file.split("/").pop(), `The assistant can see ${file} (open in the editor)`, () => { includeFile = false; renderChips(); }, "file", "eye")] : []),
    ...[...mentions].map((path) => chip(path.split("/").pop(), `The assistant will read ${path}`, () => { mentions.delete(path); renderChips(); }, "file", "at")),
    ...attachments.map((a) => chip(a.name, a.saved_to ? `${a.size} bytes · saved to ${a.saved_to}` : `${a.size} bytes · attached`, async () => {
      await api(`/api/files/upload/${a.id}?session_id=${session.id}`, { method: "DELETE" }).catch(() => {});
      attachments = attachments.filter((item) => item.id !== a.id);
      renderChips();
    }, "file", "paperclip")),
  );
}

async function upload(files) {
  if (!files.length) return;
  let target = getActiveProject();
  if (!target) {
    target = await chooseSaveFolder(files.map((file) => file.name));
    if (target === null) return;
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
      if (result.id) attachments.push(result);
      if (result.saved_to) saved += 1;
      renderChips();
    } catch (error) {
      toast(`${file.name}: ${error.message}`, "bad");
    }
  }
  if (saved) {
    emit("files-changed");
    log(`Saved ${saved} attached file${saved === 1 ? "" : "s"} to ${target}`);
  }
}

// ---- composer menus ------------------------------------------------------------------------

function openPlusMenu() {
  popover($("plus-btn"), (close) => [
    menuItem({ label: "Upload files", sub: "Attach to this chat and save into the project", iconName: "paperclip", onClick: () => { close(); $("file-input").click(); } }),
    menuItem({ label: "Add a file from your projects", sub: "Or type @ in the message", iconName: "at", onClick: () => {
      close();
      const box = input();
      const pos = box.selectionStart ?? box.value.length;
      const pad = pos > 0 && !/\s$/.test(box.value.slice(0, pos)) ? " " : "";
      box.value = `${box.value.slice(0, pos)}${pad}@${box.value.slice(pos)}`;
      box.focus();
      box.setSelectionRange(pos + pad.length + 1, pos + pad.length + 1);
      updatePalette();
    } }),
    menuItem({ label: "Open a folder…", sub: "Let the assistant work in a folder on this computer", iconName: "folder", onClick: async () => { close(); const path = await openFolder(); if (path) setActiveProject(path); } }),
    el("div", { class: "pop-sep" }),
    el("div", { class: "pop-title" }, el("span", { text: "Use a skill for this message" }),
      el("button", { text: "Manage", onclick: () => { close(); emit("open-settings", "skills"); } })),
    ...(state.agentSkills.filter((s) => s.enabled && !s.errors.length).map((s) => menuItem({
      label: s.name, sub: s.description, iconName: "sparkles",
      right: chosenSkills.has(s.name) ? icon("check", "tick") : null,
      onClick: () => {
        if (chosenSkills.has(s.name)) chosenSkills.delete(s.name); else chosenSkills.add(s.name);
        renderChips();
        close();
        input().focus();
      },
    }))),
    state.agentSkills.some((s) => s.enabled) ? null : el("div", { class: "pop-empty", text: "No skills enabled. Add one in Settings › Skills." }),
  ], { width: 340 });
}

function openToolsMenu() {
  popover($("tools-btn"), (close, rerender) => {
    const project = getActiveProject();
    const projectName = project ? project.split("/").pop() : null;
    const connectors = state.connectors;
    return [
      el("div", { class: "pop-title" }, el("span", { text: "Connectors" }),
        el("button", { text: "Manage", onclick: () => { close(); emit("open-settings", "connectors"); } })),
      ...(connectors.length ? connectors.map((c) => {
        const live = c.status === "connected";
        const sub = c.status === "connected" ? `${c.tools} tool${c.tools === 1 ? "" : "s"}` : c.status === "starting" ? "Starting…" : c.status === "disabled" ? "Disabled in settings" : "Not connected";
        return el("div", { class: "pop-item" }, icon("plug"),
          el("span", { class: "grow" }, el("span", { text: c.name }), el("span", { class: "sub", text: sub })),
          switchEl(live && !state.offConnectors.has(c.name), (on) => { setConnectorOn(c.name, on); updateToolsBadge(); }, { label: `Use ${c.name} in this chat`, disabled: !live }));
      }) : [el("div", { class: "pop-empty", text: "No connectors yet. Add MCP servers to give the assistant new tools." })]),
      el("div", { class: "pop-sep" }),
      el("div", { class: "pop-title" }, el("span", { text: "Mode" })),
      el("div", { class: "pop-item" }, icon("list"),
        el("span", { class: "grow" }, el("span", { text: "Plan mode" }), el("span", { class: "sub", text: "Investigate and propose a plan; change nothing" })),
        switchEl($("plan-mode").checked, (on) => { setMode("plan", on); rerender(); }, { label: "Plan mode" })),
      el("div", { class: "pop-item", title: AUTO_HELP }, icon("bolt"),
        el("span", { class: "grow" }, el("span", { text: projectName ? `Allow all actions in ${projectName}` : "Allow all actions in project" }),
          el("span", { class: "sub", text: project ? "Edits and commands inside the project run without asking" : "Choose a project with the folder button first" })),
        switchEl($("auto-approve").checked && Boolean(project), (on) => { setMode("auto", on); rerender(); }, { label: "Allow all actions", disabled: !project })),
    ];
  }, { width: 340 });
}

const AUTO_HELP = "Actions whose files are all inside this project run without asking: edits, new files, renames, moves to Trash, data outputs, and commands (sandboxed to this folder). Anything outside it still asks.";

function setMode(which, on) {
  const box = $(which === "plan" ? "plan-mode" : "auto-approve");
  box.checked = on;
  store.set(which === "plan" ? "workspace.planMode" : "workspace.autoApprove", on ? "1" : "0");
  syncModes();
}

function syncModes() {
  const project = getActiveProject();
  const plan = $("plan-mode").checked;
  const auto = $("auto-approve").checked && Boolean(project) && !plan;
  $("plan-chip").classList.toggle("hidden", !plan);
  $("auto-chip").classList.toggle("hidden", !auto);
  if (project) $("auto-chip-text").textContent = `Auto · ${project.split("/").pop()}`;
  input().placeholder = plan ? "Plan mode: describe the change; nothing will be modified"
    : "How can I help you today?";
}

function updateToolsBadge() {
  const live = state.connectors.filter((c) => c.status === "connected" && !state.offConnectors.has(c.name)).length;
  $("tools-badge").textContent = String(live);
  $("tools-badge").classList.toggle("hidden", !live);
  $("tools-btn").title = live ? `${live} connector${live === 1 ? "" : "s"} on · plan mode · auto-approve` : "Tools: connectors, plan mode, auto-approve";
}

async function openProjectMenu() {
  let projects = [];
  let folders = [];
  try {
    [projects, folders] = await Promise.all([
      api(`/api/projects?${qs({ session_id: session.id })}`).then((r) => r.projects),
      api("/api/folders").then((r) => r.folders),
    ]);
  } catch { /* Still offer the other choices. */ }
  const current = getActiveProject();
  const choices = [
    ...folders.filter((f) => f.exists).map((f) => ({ path: f.path, name: f.name, sub: f.path })),
    ...projects.filter((p) => !p.path.includes("/arch/") && !p.path.startsWith("/")).map((p) => ({
      path: p.path, name: p.name, sub: `${p.path}${p.datasets ? ` · ${p.datasets} data file${p.datasets === 1 ? "" : "s"}` : ""}`,
    })),
  ];
  popover($("project-btn"), (close) => [
    el("div", { class: "pop-title" }, el("span", { text: "Work in a project" }),
      el("button", { text: "Files window", onclick: () => { close(); openFilesWindow(); } })),
    ...choices.map((c) => menuItem({
      label: c.name, sub: c.sub, iconName: "folder", right: c.path === current ? icon("check", "tick") : null,
      onClick: () => { setActiveProject(c.path); close(); },
    })),
    choices.length ? null : el("div", { class: "pop-empty", text: "No projects yet. Open a folder, or create one in the Files window." }),
    el("div", { class: "pop-sep" }),
    menuItem({ label: "Open a folder…", sub: "Any folder on this computer", iconName: "plus", onClick: async () => {
      close();
      const path = await openFolder();
      if (path) setActiveProject(path);
    } }),
    current ? menuItem({ label: "No project", sub: "The assistant asks where to save files", iconName: "x", onClick: () => { setActiveProject(null); close(); } }) : null,
  ], { width: 340 });
}

function renderProject() {
  const project = getActiveProject();
  $("project-label").textContent = project ? project.split("/").pop() : "No project";
  $("project-btn").classList.toggle("on", Boolean(project));
  $("project-btn").title = project ? `Working in ${project}. Generated files are saved here.` : "Choose the project the assistant works in and saves files to";
}

function openModelMenu() {
  popover($("model-btn"), (close) => {
    const groups = [["ollama", "Ollama"], ["lmstudio", "LM Studio"], ["gguf", "GGUF files"], ["api", "API providers"]];
    const pick = (id) => { setModel(id); close(); if (state.catalog.find((m) => m.id === id)?.remote) toast("This is a remote model: your messages and files the assistant reads are sent to that provider.", "warn", 6000); };
    const items = [menuItem({
      label: state.skillDefaultModel ? `Skill default (${state.skillDefaultModel})` : "Automatic",
      sub: "The best local model that supports tools", iconName: "spark",
      right: !state.model ? icon("check", "tick") : null, onClick: () => pick(""),
    })];
    for (const [kind, title] of groups) {
      const models = state.catalog.filter((m) => m.kind === kind);
      if (!models.length) continue;
      items.push(el("div", { class: "pop-title" }, el("span", { text: title })));
      for (const model of models) {
        items.push(menuItem({
          label: model.label, iconName: kind === "api" ? "cloud" : kind === "gguf" ? "file" : "cpu",
          right: model.id === state.model ? icon("check", "tick") : model.remote ? tag("remote", "remote") : null,
          onClick: () => pick(model.id),
        }));
      }
    }
    if (!state.catalog.length) items.push(el("div", { class: "pop-empty", text: "No models found. Start Ollama or LM Studio, add a .gguf file, or add an API provider." }));
    items.push(el("div", { class: "pop-sep" }), menuItem({ label: "Manage models…", iconName: "sliders", onClick: () => { close(); emit("open-settings", "models"); } }));
    return items;
  }, { align: "right", width: 320 });
}

function renderModelButton() {
  $("model-label").textContent = modelLabel(state.model);
  if (state.model && state.catalog.length && !state.catalog.some((m) => m.id === state.model)) {
    $("model-label").textContent = `${modelLabel(state.model)} (unavailable)`;
  }
}

// ---- restore, switch ------------------------------------------------------------------------

async function restore() {
  const data = await api(`/api/session?session_id=${session.id}`);
  attachments = data.attachments;
  renderChips();
  if (!data.messages.length) return welcome();
  setEmpty(false);
  thread().replaceChildren();
  for (const message of data.messages) {
    if (message.role === "user") {
      userMessage(message.content);
      lastUserText = message.content;
    } else {
      assistantMessage(message.content);
    }
  }
  scrollDown(true);
}

export async function loadPalette() {
  [commands, apps] = await Promise.all([api("/api/commands"), api("/api/permissions")]);
}

export async function switchChat(id) {
  if (running) {
    toast("A reply is still running. Stop it before switching chats.", "warn");
    return false;
  }
  setSessionId(id);
  askedFile = null;
  mentions.clear();
  chosenSkills.clear();
  emit("activation", { cleared: true });
  await restore().catch((error) => notice("bad", error.message));
  emit("session-changed", id);
  input().focus();
  return true;
}

export const newChat = () => switchChat(newSessionId());
export const isRunning = () => running;

// ---- init ----------------------------------------------------------------------------------------

export function initChat() {
  const box = input();
  $("send").addEventListener("click", () => send());
  $("stop").addEventListener("click", () => api("/api/chat/stop", { json: { session_id: session.id } }).catch(() => {}));
  $("plus-btn").addEventListener("click", openPlusMenu);
  $("tools-btn").addEventListener("click", openToolsMenu);
  $("model-btn").addEventListener("click", openModelMenu);
  $("project-btn").addEventListener("click", openProjectMenu);
  $("plan-chip").addEventListener("click", () => setMode("plan", false));
  $("auto-chip").addEventListener("click", () => setMode("auto", false));
  $("file-input").addEventListener("change", (event) => {
    upload([...event.target.files]);
    event.target.value = "";
  });
  $("thread").addEventListener("click", onThreadClick);
  box.addEventListener("input", () => { autosize(); updatePalette(); updateSend(); });
  box.addEventListener("focus", () => loadPalette().then(updatePalette).catch(() => {}));
  box.addEventListener("blur", () => setTimeout(hidePalette, 120));
  box.addEventListener("keydown", (event) => {
    if (paletteItems.length) {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        paletteIndex = (paletteIndex + (event.key === "ArrowDown" ? 1 : -1) + paletteItems.length) % paletteItems.length;
        return renderPalette();
      }
      if (event.key === "Tab" || (event.key === "Enter" && !event.shiftKey)) {
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

  // Drop files on the composer to attach them.
  const composer = $("composer");
  composer.addEventListener("dragover", (event) => {
    if (![...event.dataTransfer.types].includes("Files")) return;
    event.preventDefault();
    composer.classList.add("dragging");
  });
  composer.addEventListener("dragleave", () => composer.classList.remove("dragging"));
  composer.addEventListener("drop", (event) => {
    composer.classList.remove("dragging");
    if (!event.dataTransfer.files.length) return;
    event.preventDefault();
    upload([...event.dataTransfer.files]);
  });

  $("clear-skill").addEventListener("click", () => send("/auto"));
  let lastFile = null;
  on("active-file", (path) => {
    if (path !== lastFile) includeFile = true;
    lastFile = path;
    renderChips();
  });
  on("ask-file", (path) => {
    askedFile = path;
    renderChips();
    if (DATA_FILE.test(path) && !box.value.trim()) box.value = "/analyze ";
    box.focus();
    box.setSelectionRange(box.value.length, box.value.length);
    updateSend();
  });
  on("notice", ({ level, message }) => toast(message, level === "info" ? "info" : level));
  on("health", () => { renderModelButton(); updateToolsBadge(); });
  on("model-changed", renderModelButton);
  on("connectors-changed", updateToolsBadge);
  on("preferences-changed", () => { if ($("thread").querySelector(".welcome h1")) $("thread").querySelector(".welcome h1").textContent = greeting(); });
  on("project-changed", () => { syncModes(); renderProject(); });
  on("new-chat", () => newChat());
  on("focus-input", () => box.focus());

  $("plan-mode").checked = store.get("workspace.planMode") === "1";
  $("auto-approve").checked = store.get("workspace.autoApprove") === "1";
  syncModes();
  renderProject();
  restore().catch((error) => notice("bad", error.message));
  loadPalette().catch(() => {});
  refreshAgentSkills();
  renderModelButton();
  updateSend();
  refreshHealth();
}
