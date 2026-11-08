// Page shell: status, panes, activity panel, permissions dialog, theme.
import { $, api, el, emit, on, qs, session, store } from "./util.js";
import { applyEditorTheme, initEditor } from "./editor.js";
import { initTree } from "./tree.js";
import { initChat } from "./chat.js";
import { initDashboard } from "./dashboard.js";
import { initDesktop } from "./desktop.js";
import { initModels } from "./models.js";

// ---- status ---------------------------------------------------------------

let skillDefaultModel = "";
let modelCatalog = [];

// Remembers the chosen model, and says plainly when a choice sends data off this computer.
function modelChosen() {
  const id = $("model-select").value;
  store.set("workspace.model", id);
  const model = modelCatalog.find((m) => m.id === id);
  if (model?.remote) {
    emit("notice", { level: "warn", message: `${model.label} is a remote model. Your messages, and the contents of files the assistant reads, will be sent to that provider.` });
  } else if (model?.kind === "gguf") {
    emit("notice", { level: "info", message: `${model.label} runs on this computer with llama.cpp. The first message loads it, which can take a few seconds.` });
  }
}

async function refreshHealth() {
  let health;
  try {
    health = await api("/api/health");
  } catch {
    setPill($("ollama-pill"), "bad", "Server unreachable");
    return;
  }
  $("app-name").textContent = health.name;
  const { ollama } = health;
  const catalog = health.catalog?.length ? health.catalog : ollama.models.map((id) => ({ id, label: id, kind: "ollama", remote: false }));
  const count = catalog.length;
  const kinds = { ollama: 0, gguf: 0, api: 0 };
  for (const model of catalog) kinds[model.kind] += 1;
  setPill($("ollama-pill"), count ? "good" : "bad", count ? `Models · ${count}` : "No models");
  $("ollama-pill").title = [
    ollama.ollama_error ? "Ollama: not running" : `Ollama: ${kinds.ollama} model${kinds.ollama === 1 ? "" : "s"}`,
    `GGUF files: ${kinds.gguf}`, `API models: ${kinds.api}`,
    count ? "" : (ollama.error || ""),
  ].filter(Boolean).join("\n");

  const isolated = health.isolation !== "none";
  setPill($("isolation-pill"), isolated ? "good" : "warn", isolated ? "Sandboxed" : "No sandbox");
  $("isolation-pill").title = isolated
    ? "Commands run in an OS sandbox: no network, and writes only inside permitted folders"
    : "This platform has no OS sandbox. Approved commands run with your user's full rights.";

  const select = $("model-select");
  const current = select.value || store.get("workspace.model") || "";
  const group = (label, kind) => {
    const models = catalog.filter((model) => model.kind === kind);
    return models.length ? el("optgroup", { label }, models.map((model) =>
      el("option", { value: model.id, text: model.remote ? `${model.label} · remote` : model.label }))) : null;
  };
  select.replaceChildren(
    el("option", { value: "", text: skillDefaultModel ? `Skill default (${skillDefaultModel})` : "Automatic (local)" }),
    group("Ollama", "ollama"), group("GGUF files", "gguf"), group("API", "api"),
  );
  select.value = catalog.some((model) => model.id === current) ? current : "";
  modelCatalog = catalog;

  const slot = $("warning-btn-slot");
  slot.replaceChildren(...(health.warnings.length ? [el("button", {
    class: "text-btn text-warn", text: `${health.warnings.length} config warning${health.warnings.length === 1 ? "" : "s"}`,
    onclick: openPermissions,
  })] : []));
}

function setPill(pill, tone, text) {
  const color = { good: "bg-good", bad: "bg-bad", warn: "bg-warn" }[tone];
  pill.replaceChildren(el("span", { class: `h-1.5 w-1.5 rounded-full ${color}`, "aria-hidden": "true" }), text);
}

function showActivation(event) {
  const clear = $("clear-skill");
  if (event.cleared) {
    $("chat-skill").textContent = "Automatic";
    $("chat-command").textContent = "";
    $("chat-agent").textContent = "Picks a skill from each message";
    clear.classList.add("hidden");
    skillDefaultModel = "";
    emit("scope-changed", { title: "Automatic" });
    refreshHealth();
    return;
  }
  $("chat-skill").textContent = event.hash_app ? `#${event.hash_app}` : event.skill;
  $("chat-command").textContent = event.hash_app ? "" : event.command;
  $("chat-agent").textContent = `${event.agent} · ${event.pinned ? "stays active" : "this message only"}`;
  clear.classList.toggle("hidden", !event.pinned);
  skillDefaultModel = event.model;
  emit("scope-changed", { title: event.skill });
  refreshHealth();
}

// ---- activity panel -------------------------------------------------------

let activityMode = "live";
const liveLines = [];
const LEVEL = { warn: "text-warn", bad: "text-bad", info: "text-soft" };

function renderLive() {
  const body = $("activity-body");
  if (!liveLines.length) {
    body.replaceChildren(el("div", { class: "text-faint", text: "Everything the assistant does appears here." }));
    return;
  }
  body.replaceChildren(...liveLines.map((line) => el("div", { class: `whitespace-pre-wrap ${LEVEL[line.level] || "text-soft"}` },
    el("span", { class: "text-faint", text: `[${line.time}] ` }), line.message)));
  body.scrollTop = body.scrollHeight;
}

async function renderHistory() {
  const body = $("activity-body");
  try {
    const rows = await api(`/api/execution/history?${qs({ session_id: session.id })}`);
    if (!rows.length) {
      body.replaceChildren(el("div", { class: "text-faint", text: "No tool activity in this conversation yet. The full record is in logs/audit.jsonl." }));
      return;
    }
    const tone = { success: "text-good", failed: "text-bad", denied: "text-bad", rejected: "text-warn" };
    body.replaceChildren(...rows.reverse().map((row) => el("div", {
      class: "flex gap-2 whitespace-nowrap",
      title: `${row.agent} / ${row.skill} / ${row.model}\npermission: ${row.permission}\napproval: ${row.approval}\n${row.result}`,
    },
      el("span", { class: "text-faint", text: new Date(row.timestamp).toLocaleTimeString() }),
      el("span", { class: `w-14 shrink-0 ${tone[row.status] || "text-soft"}`, text: row.status }),
      el("span", { class: "font-semibold", text: row.operation }),
      el("span", { class: "truncate text-soft", text: row.target }))));
  } catch (error) {
    body.replaceChildren(el("div", { class: "text-bad", text: error.message }));
  }
}

function setActivity(mode) {
  activityMode = mode;
  for (const [id, key] of [["activity-live", "live"], ["activity-history", "history"]]) {
    $(id).setAttribute("aria-pressed", String(key === mode));
    $(id).classList.toggle("text-ink", key === mode);
  }
  mode === "live" ? renderLive() : renderHistory();
}

// ---- permissions dialog ---------------------------------------------------

async function openPermissions() {
  const dialog = $("permissions-dialog");
  const body = $("permissions-body");
  body.replaceChildren(el("div", { class: "text-xs text-soft", text: "Loading…" }));
  dialog.showModal();
  const [permissions, skills, agents, health] = await Promise.all([
    api("/api/permissions"), api("/api/skills"), api("/api/agents"), api("/api/health"),
  ]);

  body.replaceChildren(
    ...(health.warnings.length ? [warningsSection(health.warnings)] : []),
    applicationsSection(permissions),
    skillsSection(agents, skills),
  );
}

const tag = (text, cls = "border-line text-soft") =>
  el("span", { class: `rounded border px-1.5 font-mono text-2xs ${cls}`, text });

function warningsSection(warnings) {
  const list = el("ul", { class: "space-y-1 rounded border border-warn bg-warn-wash p-2 text-xs" },
    warnings.map((text) => el("li", { text })));
  return el("section", {}, el("h3", { class: "eyebrow mb-1.5 text-warn", text: "Configuration warnings" }), list);
}

function applicationRow(app) {
  const operations = app.operations.map((op) => {
    const needs = app.approval_required && !app.approval_exempt.includes(op);
    return el("span", {
      class: `font-mono text-2xs ${needs ? "text-ink" : "text-soft"}`,
      title: needs ? "Needs your approval" : "Runs without approval",
      text: needs ? `${op}*` : op,
    });
  });
  return el("div", { class: "flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2" },
    el("span", { class: "w-24 shrink-0 font-mono text-xs font-semibold", text: `#${app.id}` }),
    tag(app.enabled ? "enabled" : "disabled", app.enabled ? "border-good text-good" : "border-bad text-bad"),
    el("div", { class: "flex min-w-0 flex-1 flex-wrap gap-x-2 gap-y-1" }, operations));
}

function applicationsSection(permissions) {
  const intro = el("p", { class: "mb-2 text-xs text-soft" },
    "Read-only here on purpose. To change access, edit ",
    el("code", { class: "font-mono", text: "arch/permission.md" }),
    ". Edits apply to the next action, and the assistant cannot change this file.");
  return el("section", {},
    el("h3", { class: "eyebrow mb-1", text: "Applications" }),
    intro,
    el("div", { class: "divide-y divide-line rounded border border-line bg-raised" }, permissions.map(applicationRow)),
    el("p", { class: "mt-1 text-2xs text-faint", text: "* needs your approval each time" }));
}

function skillItem(skill) {
  const detail = skill.valid
    ? `${skill.model || "any model"} · ${skill.allowed_paths.join(", ")}`
    : `Malformed: ${skill.errors.join("; ")}`;
  return el("li", { class: "text-xs" },
    el("span", { class: `font-mono ${skill.valid ? "text-accent" : "text-bad"}`, text: skill.command || skill.id }),
    ` ${detail}`);
}

function skillsSection(agents, skills) {
  const cards = agents.map((agent) => el("div", { class: "rounded border border-line bg-raised p-3" },
    el("div", { class: "text-xs font-semibold", text: agent.name }),
    el("div", { class: "text-2xs text-soft", text: agent.description }),
    el("div", { class: "mt-1.5 flex flex-wrap gap-1" }, agent.applications.map((id) => tag(`#${id}`))),
    el("ul", { class: "mt-2 space-y-1" }, skills.filter((skill) => skill.agent === agent.id).map(skillItem))));
  return el("section", {},
    el("h3", { class: "eyebrow mb-2", text: "Agents and skills" }),
    el("div", { class: "space-y-2" }, cards));
}

// ---- layout ---------------------------------------------------------------

function initSplitters() {
  for (const splitter of document.querySelectorAll(".splitter")) {
    const pane = $(splitter.dataset.pane);
    const key = `workspace.width.${pane.id}`;
    const saved = Number(store.get(key));
    if (saved) pane.style.width = `${saved}px`;
    splitter.addEventListener("pointerdown", (start) => {
      start.preventDefault();
      splitter.setPointerCapture(start.pointerId);
      splitter.classList.add("dragging");
      const origin = pane.getBoundingClientRect().width;
      const direction = splitter.dataset.edge === "right" ? 1 : -1;
      const move = (event) => {
        const width = Math.max(180, Math.min(window.innerWidth * 0.6, origin + direction * (event.clientX - start.clientX)));
        pane.style.width = `${width}px`;
      };
      const up = () => {
        splitter.classList.remove("dragging");
        splitter.removeEventListener("pointermove", move);
        store.set(key, Math.round(pane.getBoundingClientRect().width));
      };
      splitter.addEventListener("pointermove", move);
      splitter.addEventListener("pointerup", up, { once: true });
    });
  }

  const handle = $("activity-splitter");
  const activity = $("activity");
  const savedHeight = Number(store.get("workspace.activity"));
  if (savedHeight) activity.style.height = `${savedHeight}px`;
  handle.addEventListener("pointerdown", (start) => {
    start.preventDefault();
    handle.setPointerCapture(start.pointerId);
    handle.classList.add("dragging");
    const origin = activity.getBoundingClientRect().height;
    const move = (event) => {
      activity.style.height = `${Math.max(32, Math.min(window.innerHeight * 0.7, origin - (event.clientY - start.clientY)))}px`;
    };
    const up = () => {
      handle.classList.remove("dragging");
      handle.removeEventListener("pointermove", move);
      store.set("workspace.activity", Math.round(activity.getBoundingClientRect().height));
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up, { once: true });
  });
  $("activity-collapse").addEventListener("click", () => {
    const collapsed = activity.getBoundingClientRect().height <= 34;
    activity.style.height = collapsed ? `${Number(store.get("workspace.activity")) > 34 ? store.get("workspace.activity") : 176}px` : "32px";
  });
}

function showPane(id) {
  for (const pane of document.querySelectorAll(".pane")) pane.classList.toggle("pane-active", pane.id === id);
  for (const button of document.querySelectorAll("[data-show]")) {
    const current = button.dataset.show === id;
    button.classList.toggle("font-semibold", current);
    button.classList.toggle("text-ink", current);
    button.classList.toggle("text-soft", !current);
  }
}

function initTheme() {
  const saved = store.get("workspace.theme");
  if (saved) document.documentElement.dataset.theme = saved;
  $("toggle-theme").addEventListener("click", () => {
    const dark = getComputedStyle(document.documentElement).colorScheme.includes("dark");
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    store.set("workspace.theme", next);
    applyEditorTheme();
  });
}

// ---- start ----------------------------------------------------------------

initDesktop();
initModels();
$("model-select").addEventListener("change", modelChosen);
on("models-changed", refreshHealth);
initTheme();
initSplitters();
initEditor();
initTree();
initChat();
initDashboard();
setActivity("live");
refreshHealth();
setInterval(refreshHealth, 15000);

on("activation", showActivation);
on("log", (line) => {
  liveLines.push(line);
  if (liveLines.length > 500) liveLines.shift();
  if (activityMode === "live") renderLive();
  else renderHistory();
});
on("log-clear", () => { liveLines.length = 0; setActivity("live"); });
on("show-pane", showPane);

$("activity-live").addEventListener("click", () => setActivity("live"));
$("activity-history").addEventListener("click", () => setActivity("history"));
$("open-permissions").addEventListener("click", openPermissions);
$("close-permissions").addEventListener("click", () => $("permissions-dialog").close());
for (const button of document.querySelectorAll("[data-show]")) {
  button.addEventListener("click", () => showPane(button.dataset.show));
}

api(`/api/session?session_id=${session.id}`).then((state) => {
  const command = state.pinned_command;
  if (command) $("chat-command").textContent = command;
}).catch(() => {});
