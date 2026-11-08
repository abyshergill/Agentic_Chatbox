// Models dialog: Ollama status, .gguf files run with llama.cpp, and OpenAI-compatible API providers.
import { $, api, el, emit } from "./util.js";
import { chooseGguf, isDesktop } from "./desktop.js";

let state = null;
let editing = null; // The provider being edited, or {} for a new one, or null.

const chip = (tone, text, title = "") =>
  el("span", { class: "md-chip", title }, el("span", { class: `md-dot ${tone}` }), text);

async function load() {
  state = await api("/api/models");
  render();
}

function render(error = "") {
  const body = $("models-body");
  const runtime = state.runtime;
  body.replaceChildren(
    el("div", { class: "md-status" },
      chip(state.ollama_error ? "bad" : "good", state.ollama_error ? "Ollama: not running" : "Ollama: connected", state.ollama_error || ""),
      chip(runtime.available ? (runtime.running ? "good" : "warn") : "bad",
        runtime.running ? `GGUF: running ${runtime.model}` : runtime.available ? "GGUF: ready" : "GGUF: no runtime", runtime.detail),
      chip(state.providers.length ? "good" : "", `API providers: ${state.providers.length}`)),
    ...(error ? [el("div", { class: "md-error mb-3", role: "alert", text: error })] : []),
    ggufSection(),
    providersSection(),
  );
}

// ---- .gguf files --------------------------------------------------------------

function ggufSection() {
  const runtime = state.runtime;
  const list = el("div", { class: "md-list" });
  if (!state.gguf.length) list.append(el("div", { class: "md-empty", text: "No .gguf files yet." }));
  for (const file of state.gguf) {
    list.append(el("div", { class: "md-row" },
      el("div", { class: "md-main" },
        el("div", { class: "md-title" }, file.name, file.exists ? null : el("span", { class: "md-badge remote", text: "missing" }),
          runtime.running && runtime.model === file.name ? el("span", { class: "md-badge", text: "loaded" }) : null),
        el("div", { class: "md-sub", title: file.path, text: `${file.size_mb ? `${file.size_mb.toLocaleString()} MB · ` : ""}${file.path}` })),
      file.from_folder
        ? el("span", { class: "text-2xs text-faint", text: "from models folder" })
        : el("button", { class: "md-btn danger", text: "Remove", onclick: () => removeGguf(file.path) })));
  }
  const pathInput = el("input", { type: "text", placeholder: "/path/to/model.gguf", "aria-label": "Path to a .gguf file" });
  const add = async () => {
    if (!pathInput.value.trim()) return;
    await call(() => api("/api/models/gguf", { json: { path: pathInput.value.trim() } }));
  };
  pathInput.addEventListener("keydown", (event) => { if (event.key === "Enter") add(); });
  return el("section", { class: "md-section" },
    el("h3", { text: "GGUF model files (no Ollama needed)" }),
    el("p", { class: "md-hint" },
      "Runs the file directly with llama.cpp on this computer. Add a file below, or drop .gguf files into ",
      el("code", { text: state.models_dir }), ". Pick it in the chat's Model menu; it loads on first use."),
    runtime.available ? null : el("div", { class: "md-warning mb-2", text: runtime.detail }),
    list,
    el("div", { class: "md-addrow" },
      isDesktop() ? el("button", { class: "md-btn", text: "Choose .gguf file…", onclick: browseGguf }) : null,
      pathInput,
      el("button", { class: "md-btn", text: "Add", onclick: add })),
    runtime.running ? el("div", { class: "md-actions" },
      el("button", { class: "md-btn", text: `Unload ${runtime.model}`, title: "Frees the memory it uses", onclick: () => call(() => api("/api/models/gguf/stop", { method: "POST" })) })) : null);
}

async function browseGguf() {
  const path = await chooseGguf();
  if (path) await call(() => api("/api/models/gguf", { json: { path } }));
}

function removeGguf(path) {
  return call(() => api("/api/models/gguf/remove", { json: { path } }));
}

// ---- API providers --------------------------------------------------------------

function providersSection() {
  const list = el("div", { class: "md-list" });
  if (!state.providers.length) list.append(el("div", { class: "md-empty", text: "No API providers yet." }));
  for (const provider of state.providers) {
    list.append(el("div", { class: "md-row" },
      el("div", { class: "md-main" },
        el("div", { class: "md-title" }, provider.name,
          el("span", { class: `md-badge${provider.remote ? " remote" : ""}`, text: provider.remote ? "remote" : "this computer" })),
        el("div", { class: "md-sub", title: provider.models.join(", "),
          text: `${provider.base_url} · ${provider.models.length} model${provider.models.length === 1 ? "" : "s"}: ${provider.models.join(", ")}` })),
      el("button", { class: "md-btn", text: "Edit", onclick: () => { editing = provider; render(); } }),
      el("button", { class: "md-btn danger", text: "Remove", onclick: () => removeProvider(provider) })));
  }
  return el("section", { class: "md-section" },
    el("h3", { text: "API models" }),
    el("p", { class: "md-hint", text: "Any OpenAI-compatible API: OpenAI, Anthropic, Gemini, Groq, OpenRouter, Mistral, DeepSeek, "
      + "or a local server such as LM Studio. Keys are stored in data/models.json on this computer, readable only by you, "
      + "and the assistant cannot read them." }),
    list,
    editing ? providerForm(editing) : el("div", { class: "md-actions" },
      el("button", { class: "md-btn primary", text: "Add API provider", onclick: () => { editing = {}; render(); } })));
}

function providerForm(provider) {
  const isNew = !provider.id;
  const preset = el("select", { "aria-label": "Preset" },
    state.presets.map((p) => el("option", { value: p.base_url, text: p.name })));
  const name = el("input", { type: "text", value: provider.name || "", "aria-label": "Provider name" });
  const url = el("input", { type: "url", value: provider.base_url || "", placeholder: "https://api.example.com/v1", "aria-label": "API base URL" });
  const key = el("input", { type: "password", autocomplete: "off", placeholder: isNew ? "sk-…" : `${provider.api_key || "(none)"} (leave empty to keep)`, "aria-label": "API key" });
  const models = el("textarea", { "aria-label": "Model names", placeholder: "One model name per line, e.g. gpt-4.1-mini" });
  models.value = (provider.models || []).join("\n");
  const tools = el("input", { type: "checkbox", id: "md-native-tools" });
  tools.checked = provider.native_tools ?? true;
  const picks = el("div", { class: "md-picks md-wide hidden" });
  const warning = el("div", { class: "md-warning md-wide hidden" });
  const problem = el("div", { class: "md-error md-wide hidden", role: "alert" });

  const updateWarning = () => {
    let host = "";
    try { host = new URL(url.value).hostname; } catch { /* Not a URL yet. */ }
    const remote = host && !["127.0.0.1", "localhost", "::1", "[::1]"].includes(host);
    warning.classList.toggle("hidden", !remote);
    warning.textContent = remote
      ? `Remote model: your messages, and the contents of any file the assistant reads, will be sent to ${host}. `
        + "Use it only for data you are allowed to share with that provider."
      : "";
  };
  preset.addEventListener("change", () => {
    const chosen = state.presets.find((p) => p.base_url === preset.value);
    url.value = preset.value;
    if (chosen && chosen.name !== "Custom" && (!name.value || state.presets.some((p) => p.name === name.value))) name.value = chosen.name.replace(/ \(.*\)$/, "");
    updateWarning();
  });
  url.addEventListener("input", updateWarning);
  updateWarning();

  const showProblem = (text) => { problem.textContent = text; problem.classList.toggle("hidden", !text); };
  const find = async () => {
    showProblem("");
    picks.classList.remove("hidden");
    picks.replaceChildren(el("span", { class: "text-2xs text-soft", text: "Asking the API for its models…" }));
    try {
      const result = await api("/api/models/providers/discover", { json: { id: provider.id || null, base_url: url.value, api_key: key.value || null } });
      const chosen = new Set(models.value.split(/\s+/).filter(Boolean));
      picks.replaceChildren(...(result.models.length ? result.models : ["(the API listed no models)"]).map((model) => {
        const box = el("input", { type: "checkbox", value: model });
        box.checked = chosen.has(model);
        box.addEventListener("change", () => {
          const set = new Set(models.value.split(/\s+/).filter(Boolean));
          if (box.checked) set.add(model); else set.delete(model);
          models.value = [...set].join("\n");
        });
        return el("label", {}, box, model);
      }));
    } catch (error) {
      picks.classList.add("hidden");
      showProblem(`Could not list models: ${error.message}`);
    }
  };
  const save = async () => {
    showProblem("");
    try {
      state = await api("/api/models/providers", { json: {
        id: provider.id || null, name: name.value, base_url: url.value, api_key: key.value || null,
        models: models.value.split(/[\s,]+/).filter(Boolean), native_tools: tools.checked,
      } });
      editing = null;
      render();
      emit("models-changed");
    } catch (error) {
      showProblem(error.message);
    }
  };

  return el("div", { class: "md-form" },
    isNew ? el("label", { text: "Preset" }) : null, isNew ? preset : null,
    el("label", { text: "Name" }), name,
    el("label", { text: "API base URL" }), url,
    el("label", { text: "API key" }), key,
    warning,
    el("label", { text: "Models" }), models,
    el("span"), el("div", {}, el("button", { class: "md-btn", text: "Find models", onclick: find, title: "Ask the API which models your key can use" })),
    picks,
    el("span"), el("label", { for: "md-native-tools", class: "inline-flex items-center gap-1.5", style: "color:var(--ink)" }, tools,
      "Use native tool calling (turn off if the model replies with raw JSON instead of acting)"),
    problem,
    el("div", { class: "md-actions md-wide" },
      el("button", { class: "md-btn primary", text: isNew ? "Add provider" : "Save", onclick: save }),
      el("button", { class: "md-btn", text: "Cancel", onclick: () => { editing = null; render(); } })));
}

async function removeProvider(provider) {
  if (!window.confirm(`Remove ${provider.name} and its saved API key?`)) return;
  await call(() => api(`/api/models/providers/${encodeURIComponent(provider.id)}`, { method: "DELETE" }));
}

async function call(action) {
  try {
    state = await action();
    render();
    emit("models-changed");
  } catch (error) {
    render(error.message);
  }
}

export function initModels() {
  $("open-models").addEventListener("click", async () => {
    editing = null;
    $("models-body").replaceChildren(el("div", { class: "text-xs text-soft", text: "Loading…" }));
    $("models-dialog").showModal();
    try { await load(); } catch (error) { $("models-body").replaceChildren(el("div", { class: "md-error", text: error.message })); }
  });
  $("close-models").addEventListener("click", () => $("models-dialog").close());
}
