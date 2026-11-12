// App-wide state shared by the chat, sidebar and settings: models, connectors, skills, preferences.
import { api, emit, store } from "./util.js";

export const state = {
  health: null,
  catalog: [], // [{ id, label, kind, remote }]
  model: store.get("workspace.model") || "", // "" = automatic
  skillDefaultModel: "",
  connectors: [], // from /api/health: [{ name, status, tools }]
  agentSkills: [], // from /api/agent-skills
  preferences: { display_name: "", custom_instructions: "" },
  offConnectors: new Set(JSON.parse(store.get("workspace.offConnectors") || "[]")),
  logs: [],
};

export function setModel(id) {
  state.model = id;
  store.set("workspace.model", id);
  emit("model-changed", id);
}

export function setConnectorOn(name, on) {
  if (on) state.offConnectors.delete(name);
  else state.offConnectors.add(name);
  store.set("workspace.offConnectors", JSON.stringify([...state.offConnectors]));
  emit("connectors-changed");
}

// The MCP servers this chat may use: null means all (nothing switched off).
export function chatConnectors() {
  if (!state.offConnectors.size) return null;
  return state.connectors.map((c) => c.name).filter((name) => !state.offConnectors.has(name));
}

export async function refreshHealth() {
  try {
    state.health = await api("/api/health");
  } catch {
    state.health = null;
    emit("health", null);
    return null;
  }
  const { ollama } = state.health;
  state.catalog = state.health.catalog?.length
    ? state.health.catalog
    : ollama.models.map((id) => ({ id, label: id, kind: "ollama", remote: false }));
  state.connectors = state.health.connectors || [];
  emit("health", state.health);
  return state.health;
}

export async function refreshAgentSkills() {
  try {
    state.agentSkills = (await api("/api/agent-skills")).skills;
  } catch {
    state.agentSkills = [];
  }
  emit("agent-skills-changed");
  return state.agentSkills;
}

export async function refreshPreferences() {
  try {
    state.preferences = await api("/api/preferences");
  } catch { /* Defaults. */ }
  emit("preferences-changed");
}

export function modelLabel(id) {
  if (!id) return state.skillDefaultModel ? `Skill default` : "Automatic";
  const entry = state.catalog.find((m) => m.id === id);
  return entry ? entry.label : id.replace(/^(gguf|lmstudio|api:[^:]+):/, "");
}

export function log(message, level = "info") {
  state.logs.push({ time: new Date().toTimeString().slice(0, 8), message, level });
  if (state.logs.length > 800) state.logs.shift();
  emit("log-line");
}
