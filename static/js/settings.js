// Settings window: General, Models, Connectors (MCP), Skills, Agents & permissions, Activity.
import { $, api, el, emit, on, qs, session, store } from "./util.js";
import { chooseFolder, chooseGguf, isDesktop } from "./desktop.js";
import { hydrateIcons, icon } from "./icons.js";
import { armedButton, iconButton, switchEl, tag, toast } from "./ui.js";
import { modelLabel, refreshAgentSkills, refreshHealth, refreshPreferences, setModel, state } from "./state.js";
import { openFilesWindow } from "./workspace-link.js";

const TITLES = {
  general: "General", models: "Models", media: "Media & GPU", connectors: "Connectors", skills: "Skills",
  permissions: "Agents & permissions", activity: "Activity",
};
const RENDER = {};
let current = "general";
let pollTimer = null;
let renderToken = 0;

export function openSettings(tab = "general") {
  const dialog = $("settings");
  if (!dialog.open) dialog.showModal();
  select(tab);
}

function select(tab) {
  current = TITLES[tab] ? tab : "general";
  for (const button of document.querySelectorAll(".set-tab")) button.classList.toggle("active", button.dataset.tab === current);
  $("set-title").textContent = TITLES[current];
  render();
}

async function render() {
  clearTimeout(pollTimer);
  const token = ++renderToken;
  const body = $("set-body");
  const nodes = await RENDER[current]().catch((error) => [el("div", { class: "err-box", text: error.message })]);
  if (token !== renderToken) return; // A newer render won.
  const scroll = body.scrollTop;
  body.replaceChildren(...nodes.filter(Boolean));
  body.scrollTop = scroll;
  hydrateIcons(body);
}

function poll(ms = 1500) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(() => { if ($("settings").open) render(); }, ms);
}

const section = (title, lead, ...children) => el("section", { class: "set-section" },
  el("h4", {}, ...(Array.isArray(title) ? title : [title])), lead ? el("p", { class: "lead", text: lead }) : null, ...children);
const field = (label, control, help) => el("div", { class: "form-row" },
  el("label", { class: "field-label" }, label), control, help ? el("p", { class: "field-help", text: help }) : null);
const input = (attrs = {}) => el("input", { class: `input ${attrs.mono ? "mono" : ""}`, ...attrs, mono: null });
const textarea = (attrs = {}, value = "") => { const node = el("textarea", { class: `textarea ${attrs.mono ? "mono" : ""}`, ...attrs, mono: null }); node.value = value; return node; };
function seg(options, value, onChange) {
  const box = el("div", { class: "seg", role: "radiogroup" });
  for (const [key, label] of options) {
    box.append(el("button", { class: key === value ? "active" : "", role: "radio", "aria-checked": String(key === value), text: label,
      onclick: (event) => {
        onChange(key);
        for (const b of box.children) {
          b.classList.toggle("active", b === event.currentTarget);
          b.setAttribute("aria-checked", String(b === event.currentTarget));
        }
      } }));
  }
  return box;
}
const cardRow = ({ iconName, iconClass = "", title, titleExtra = [], sub, subClass = "", actions = [] }) =>
  el("div", { class: "card-row" },
    iconName ? el("span", { class: `card-icon ${iconClass}` }, icon(iconName)) : null,
    el("div", { class: "card-main" },
      el("div", { class: "card-title" }, el("span", { text: title }), ...titleExtra),
      sub ? el("div", { class: `card-sub ${subClass}`, text: sub, title: sub }) : null),
    el("div", { class: "card-actions" }, ...actions));

async function act(promise, success) {
  try {
    const result = await promise;
    if (success) toast(success);
    return result;
  } catch (error) {
    toast(error.message, "bad", 7000);
    return null;
  }
}

// ---- General ------------------------------------------------------------------------------

RENDER.general = async () => {
  await refreshPreferences();
  const prefs = state.preferences;
  const name = input({ value: prefs.display_name, placeholder: "What should the assistant call you?", maxlength: 60 });
  const instructions = textarea({ rows: 7, placeholder: "e.g. I work in Python and SQL. Keep answers short. Use British English. Always add type hints.", maxlength: 8000 }, prefs.custom_instructions);
  const save = el("button", { class: "btn accent", text: "Save", onclick: async () => {
    const saved = await act(api("/api/preferences", { json: { display_name: name.value, custom_instructions: instructions.value } }), "Saved");
    if (saved) { state.preferences = saved; emit("preferences-changed"); }
  } });
  const theme = store.get("workspace.theme") || "system";
  const font = store.get("workspace.font") || "serif";
  return [
    section("Profile", "Used in every conversation. Stored on this computer in data/preferences.json.",
      field("Your name", name),
      field("Custom instructions", instructions, "Standing preferences the assistant follows in every chat. They never override safety rules or permissions."),
      el("div", { class: "form-actions", style: "justify-content:flex-start" }, save)),
    section("Appearance", null,
      field("Theme", seg([["system", "System"], ["light", "Light"], ["dark", "Dark"]], theme, (value) => {
        store.set("workspace.theme", value);
        emit("appearance-changed");
      })),
      field("Reply font", seg([["serif", "Serif"], ["sans", "Sans-serif"]], font, (value) => {
        store.set("workspace.font", value);
        emit("appearance-changed");
      }))),
    section("Keyboard shortcuts", null, el("dl", { class: "kv" },
      ...[["⌘K", "New chat"], ["⌘B", "Show or hide the sidebar"], ["⌘E", "Show or hide files and editor"], ["⌘,", "Settings"],
        ["⌘/", "Focus the message box"], ["Enter / ⇧Enter", "Send / new line"], ["/  #  @", "Skills and agents, applications, files"]]
        .flatMap(([key, what]) => [el("dt", { text: key }), el("dd", { text: what })]))),
  ];
};

// ---- Models -----------------------------------------------------------------------------------

const PULL_SUGGESTIONS = ["qwen2.5-coder:7b", "qwen3:8b", "llama3.2", "gpt-oss:20b", "mistral-small3.2"];

RENDER.models = async () => {
  const [data, ollama] = await Promise.all([api("/api/models"), api("/api/models/ollama")]);
  await refreshHealth();
  const lmModels = state.catalog.filter((m) => m.kind === "lmstudio");
  const jobs = data.downloads || [];
  if (jobs.some((j) => j.status === "running")) poll(1200);

  // Ollama
  const pullName = input({ placeholder: "Model name, e.g. qwen2.5-coder:7b", list: "pull-suggestions" });
  const pull = el("button", { class: "btn", text: "Pull", onclick: async () => {
    if (!pullName.value.trim()) return;
    if (await act(api("/api/models/ollama/pull", { json: { name: pullName.value.trim() } }))) render();
  } });
  const ollamaCard = el("div", { class: "card" },
    cardRow({
      iconName: "cpu", iconClass: ollama.running ? "accent" : "", title: "Ollama",
      titleExtra: [tag(ollama.running ? "running" : "not running", ollama.running ? "good" : "bad")],
      sub: ollama.running ? `${data.ollama_url} · ${ollama.models.length} model${ollama.models.length === 1 ? "" : "s"}` : "Install from ollama.com, then start it (the desktop app starts it for you).",
    }),
    ...ollama.models.map((m) => cardRow({
      title: m.name, sub: [m.parameters, m.quantization, m.family, `${m.size_gb} GB`].filter(Boolean).join(" · "),
      titleExtra: inUse(m.name),
      actions: [useButton(m.name), armedButton("Delete", "Delete?", async () => { if (await act(api("/api/models/ollama/delete", { json: { name: m.name } }), `Deleted ${m.name}`)) render(); })],
    })),
    ollama.running ? el("div", { class: "card-extra" }, el("div", { class: "inline-row" }, pullName, pull),
      el("datalist", { id: "pull-suggestions" }, PULL_SUGGESTIONS.map((v) => el("option", { value: v }))),
      el("p", { class: "field-help", text: "Models that support tool calling work best: qwen2.5-coder, qwen3, llama3.1+, gpt-oss, mistral-small." })) : null);

  // LM Studio
  const lmCard = el("div", { class: "card" },
    cardRow({
      iconName: "server", iconClass: data.lmstudio.running ? "accent" : "", title: "LM Studio",
      titleExtra: [tag(data.lmstudio.running ? "detected" : "not running", data.lmstudio.running ? "good" : "")],
      sub: data.lmstudio.running ? `${data.lmstudio.url} · models appear in the model menu automatically` : `Start the local server in LM Studio (Developer › Start Server). Looking at ${data.lmstudio.url}.`,
    }),
    ...lmModels.map((m) => cardRow({ title: m.label, sub: m.id, titleExtra: inUse(m.id), actions: [useButton(m.id)] })));

  // GGUF
  const ggufPath = input({ placeholder: "/path/to/model.gguf", mono: true });
  const ggufUrl = input({ placeholder: "https://huggingface.co/…/resolve/main/model-Q4_K_M.gguf", mono: true });
  const runtime = data.runtime;
  const ggufCard = el("div", { class: "card" },
    cardRow({
      iconName: "file", title: "llama.cpp runtime",
      titleExtra: [tag(runtime.available ? (runtime.running ? `running ${runtime.model}` : "ready") : "not installed", runtime.available ? "good" : "bad")],
      sub: runtime.detail, subClass: "wrap",
      actions: runtime.running ? [el("button", { class: "btn sm", text: "Stop", onclick: async () => { await act(api("/api/models/gguf/stop", { method: "POST" })); render(); } })] : [],
    }),
    ...data.gguf.map((f) => cardRow({
      title: f.name, titleExtra: f.exists ? inUse(`gguf:${f.name}`) : [tag("missing", "bad")],
      sub: `${f.size_mb ? `${(f.size_mb / 1000).toFixed(1)} GB · ` : ""}${f.path}`, subClass: "mono",
      actions: f.from_folder ? [useButton(`gguf:${f.name}`, f.exists), tag("models/ folder")] : [useButton(`gguf:${f.name}`, f.exists), armedButton("Remove", "Remove?", async () => {
        if (await act(api("/api/models/gguf/remove", { json: { path: f.path } }))) render();
      })],
    })),
    el("div", { class: "card-extra" },
      field("Add a .gguf file on this computer", el("div", { class: "inline-row" }, ggufPath,
        isDesktop() ? el("button", { class: "btn", text: "Browse…", onclick: async () => { const picked = await chooseGguf(); if (picked) ggufPath.value = picked; } }) : null,
        el("button", { class: "btn", text: "Add", onclick: async () => {
          if (!ggufPath.value.trim()) return;
          if (await act(api("/api/models/gguf", { json: { path: ggufPath.value.trim() } }), "Added")) render();
        } }))),
      field("Download a .gguf from a link", el("div", { class: "inline-row" }, ggufUrl,
        el("button", { class: "btn", text: "Download", onclick: async () => {
          if (!ggufUrl.value.trim()) return;
          if (await act(api("/api/models/download", { json: { url: ggufUrl.value.trim() } }))) render();
        } })), `Saved to ${data.models_dir}. Hugging Face page links work too. Q4_K_M files are a good balance of size and quality.`)));

  // Downloads
  const downloads = jobs.length ? section("Downloads", null, el("div", { class: "card" }, ...jobs.map((job) => el("div", { class: "card-row" },
    el("span", { class: "card-icon" }, icon(job.kind === "ollama" ? "cpu" : "download")),
    el("div", { class: "card-main" },
      el("div", { class: "card-title" }, el("span", { text: job.name }), tag(job.status, job.status === "done" ? "good" : job.status === "error" ? "bad" : "accent")),
      el("div", { class: "card-sub wrap", text: job.status === "running"
        ? `${job.detail || "Downloading"}${job.total ? ` · ${(job.received / 1e9).toFixed(2)} of ${(job.total / 1e9).toFixed(2)} GB` : ""}` : job.detail }),
      job.status === "running" && job.percent !== null ? el("div", { class: "progress" }, el("div", { style: `width:${job.percent}%` })) : null),
    el("div", { class: "card-actions" }, job.status === "running"
      ? el("button", { class: "btn sm", text: "Cancel", onclick: async () => { await act(api(`/api/models/downloads/${job.id}/cancel`, { method: "POST" })); render(); } })
      : null)))),
  el("div", { class: "form-actions", style: "justify-content:flex-start;margin-top:8px" },
    el("button", { class: "btn sm", text: "Clear finished", onclick: async () => { await api("/api/models/downloads/clear", { method: "POST" }); render(); } }))) : null;

  const current = el("div", { class: "card" }, cardRow({
    iconName: "spark", iconClass: "accent", title: `Chat model: ${modelLabel(state.model)}`,
    sub: state.model ? "Used for new messages in every chat. Choose another below, or in the model menu under the message box."
      : "The best installed local model that supports tools is picked for each message. Choose a fixed one below.",
    actions: state.model ? [el("button", { class: "btn sm", text: "Use automatic", onclick: () => { setModel(""); render(); } })] : [],
  }));
  return [
    current,
    section("Local models", "Run on this computer. Nothing you type leaves it.", ollamaCard, lmCard),
    downloads,
    section("GGUF files", "Run a .gguf model file directly with llama.cpp, without Ollama.", ggufCard),
    section("API providers", "Any OpenAI-compatible API: OpenAI, Anthropic, Gemini, Groq, OpenRouter, Mistral, DeepSeek, vLLM, a llama.cpp server… Hosted models receive your messages and the files the assistant reads.",
      ...providerCards(data), providerForm(data)),
  ];
};

// ---- Media & GPU -------------------------------------------------------------------------------

const IMAGE_MODELS = ["stabilityai/sd-turbo", "stabilityai/sdxl-turbo", "stable-diffusion-v1-5/stable-diffusion-v1-5"];
const OLLAMA_IMAGE_MODELS = ["x/z-image-turbo", "x/flux2-klein"];
let mediaPreview = null;

RENDER.media = async () => {
  const data = await api("/api/media");
  const cfg = data.config;
  const { gpu, cpu } = data.hardware;
  const pkgs = data.packages;
  const jobs = data.jobs || [];
  const running = jobs.filter((j) => j.status === "running");
  if (running.length) poll(1500);
  const save = async (values, message) => {
    const saved = await act(api("/api/media/settings", { json: values }), message);
    if (saved) render();
    return saved;
  };

  // GPU
  const gpuCard = el("div", { class: "card" },
    cardRow({
      iconName: "gpu", iconClass: data.gpu_in_use ? "accent" : "",
      title: gpu.available ? gpu.name : "No GPU found",
      titleExtra: [gpu.available ? tag(data.gpu_in_use ? "in use" : "off", data.gpu_in_use ? "good" : "") : tag("CPU only")],
      sub: gpu.available ? `${gpu.detail}${gpu.memory_gb ? ` · ${gpu.memory_gb} GB` : ""}` : "Everything runs on the processor. NVIDIA (CUDA), AMD (ROCm) and Apple Silicon GPUs are used when present.",
      subClass: "wrap",
      actions: [switchEl(data.gpu_in_use, (on) => save({ use_gpu: on }, on ? "GPU on: local models and images use the GPU" : "GPU off: local models and images run on the CPU"),
        { label: "Use the GPU", disabled: !gpu.available })],
    }),
    cardRow({ iconName: "cpu", title: cpu.name || "Processor", sub: `${cpu.cores} cores${cpu.memory_gb ? ` · ${cpu.memory_gb} GB memory` : ""} · ${data.hardware.os}` }),
    el("div", { class: "card-extra" }, el("p", { class: "field-help", text: "The switch applies to Ollama chat models, .gguf files and local image generation; the next message or image picks it up. Turn it off to keep the GPU free for other work (things run slower on the CPU). LM Studio and API models choose their own hardware." })));

  // Image engines
  const sdReady = data.engines.diffusers;
  const modelField = input({ value: cfg.diffusers_model, mono: true, list: "sd-models", "aria-label": "Stable Diffusion model" });
  const sdCard = el("div", { class: "card" },
    cardRow({
      iconName: "image", iconClass: data.engine === "diffusers" ? "accent" : "", title: "Stable Diffusion on this computer",
      titleExtra: [sdReady ? tag("installed", "good") : tag("not installed", "bad"), sdReady ? tag(data.model_cached ? "model downloaded" : "model not downloaded", data.model_cached ? "good" : "") : null,
        data.engine === "diffusers" ? tag("in use", "accent") : null].filter(Boolean),
      sub: sdReady ? `Private and free. Runs on the ${data.gpu_in_use ? "GPU" : "CPU"}${data.device ? ` (last run on ${data.device.toUpperCase()})` : ""}.`
        : "Installs PyTorch and diffusers into the app's Python (about 1 GB), then downloads a model (sd-turbo is about 2.5 GB).",
      subClass: "wrap",
      actions: [
        !sdReady || !pkgs["imageio-ffmpeg"] ? el("button", { class: "btn sm accent", text: "Install", disabled: running.some((j) => j.kind === "install"),
          onclick: async () => { if (await act(api("/api/media/install", { method: "POST" }), "Installing… this takes a few minutes")) render(); } }) : null,
        sdReady && !data.model_cached ? el("button", { class: "btn sm", text: "Download model", disabled: running.some((j) => j.kind === "image-model"),
          onclick: async () => { if (await act(api("/api/media/download-model", { method: "POST" }), "Downloading the model")) render(); } }) : null,
      ].filter(Boolean),
    }),
    el("div", { class: "card-extra" },
      field("Model", el("div", { class: "inline-row" }, modelField,
        el("button", { class: "btn", text: "Save", onclick: () => save({ diffusers_model: modelField.value.trim() }, "Saved") })),
        "A Hugging Face model id or a local folder. Turbo models draw in 1-4 steps: fastest. stable-diffusion-v1-5 is slower but follows prompts in more styles."),
      el("datalist", { id: "sd-models" }, IMAGE_MODELS.map((v) => el("option", { value: v })))));

  const ollamaField = input({ value: cfg.ollama_image_model, mono: true, list: "ollama-image-models", "aria-label": "Ollama image model" });
  const ollamaCard = el("div", { class: "card" },
    cardRow({
      iconName: "cpu", iconClass: data.engine === "ollama" ? "accent" : "", title: "Ollama image model",
      titleExtra: [data.engines.ollama ? tag("installed", "good") : tag("not pulled"), tag("experimental"), data.engine === "ollama" ? tag("in use", "accent") : null].filter(Boolean),
      sub: "Newer Ollama versions can draw images on macOS. Pull the model, then choose Ollama as the engine.", subClass: "wrap",
      actions: [data.engines.ollama ? null : el("button", { class: "btn sm", text: "Pull", onclick: async () => {
        if (await act(api("/api/models/ollama/pull", { json: { name: ollamaField.value.trim() } }), "Pulling… see Settings › Models for progress")) render();
      } })].filter(Boolean),
    }),
    el("div", { class: "card-extra" }, field("Model", el("div", { class: "inline-row" }, ollamaField,
      el("button", { class: "btn", text: "Save", onclick: () => save({ ollama_image_model: ollamaField.value.trim() }, "Saved") }))),
      el("datalist", { id: "ollama-image-models" }, OLLAMA_IMAGE_MODELS.map((v) => el("option", { value: v })))));

  const providerSelect = el("select", { class: "select", "aria-label": "Image API provider" },
    el("option", { value: "", text: data.providers.length ? "Choose a provider…" : "Add a provider in Settings › Models first" }),
    data.providers.map((p) => el("option", { value: p.id, text: `${p.name}${p.remote ? " (remote)" : ""}`, selected: p.id === cfg.api_provider })));
  const apiModel = input({ value: cfg.api_image_model, mono: true, placeholder: "e.g. gpt-image-1, dall-e-3, flux-schnell" });
  const apiCard = el("div", { class: "card" },
    cardRow({
      iconName: "cloud", iconClass: data.engine === "api" ? "accent" : "", title: "Image API",
      titleExtra: [data.engines.api ? tag("set up", "good") : tag("not set up"), data.provider_remote ? tag("remote", "remote") : null, data.engine === "api" ? tag("in use", "accent") : null].filter(Boolean),
      sub: "Any OpenAI-compatible /images/generations endpoint. Remote providers receive your prompts.", subClass: "wrap",
    }),
    el("div", { class: "card-extra" }, el("div", { class: "inline-row" }, providerSelect, apiModel,
      el("button", { class: "btn", text: "Save", onclick: () => save({ api_provider: providerSelect.value, api_image_model: apiModel.value.trim() }, "Saved") }))));

  // Try it
  const prompt = input({ placeholder: "A lighthouse on a cliff at sunset, oil painting", "aria-label": "Test prompt" });
  const tryBtn = el("button", { class: "btn accent", text: "Generate", disabled: !data.engine });
  const result = el("div", { class: "media-try" });
  if (mediaPreview) result.append(el("img", { src: mediaPreview.image, alt: mediaPreview.prompt }), el("p", { class: "field-help", text: mediaPreview.how }));
  tryBtn.addEventListener("click", async () => {
    if (!prompt.value.trim()) { prompt.focus(); return; }
    tryBtn.disabled = true;
    tryBtn.textContent = "Generating…";
    result.replaceChildren(el("p", { class: "field-help", text: data.model_cached || data.engine !== "diffusers" ? "Drawing… the first image also loads the model." : "Downloading the model and drawing… this can take several minutes the first time." }));
    const made = await act(api("/api/media/test", { json: { prompt: prompt.value.trim() } }));
    tryBtn.disabled = false;
    tryBtn.textContent = "Generate";
    if (made) {
      mediaPreview = { ...made, prompt: prompt.value.trim() };
      result.replaceChildren(el("img", { src: made.image, alt: prompt.value.trim() }), el("p", { class: "field-help", text: made.how }));
    } else result.replaceChildren();
  });
  prompt.addEventListener("keydown", (event) => { if (event.key === "Enter") tryBtn.click(); });

  const jobsCard = jobs.length ? el("div", { class: "card" }, ...jobs.map((job) => cardRow({
    iconName: job.kind === "install" ? "download" : "image", title: job.name,
    titleExtra: [tag(job.status, job.status === "done" ? "good" : job.status === "error" ? "bad" : "accent")],
    sub: job.detail, subClass: "wrap",
  }))) : null;

  const engineLabel = { diffusers: "Stable Diffusion", ollama: "Ollama", api: "Image API" }[data.engine];
  return [
    section("GPU", "Detected automatically. Local work uses the GPU when one is present, unless you turn it off.", gpuCard),
    section("Images", data.engine ? `The assistant draws with ${engineLabel}. Ask in any chat: "make an image of…".` : "Set up one engine below so the assistant can draw images.",
      field("Engine", seg([["auto", "Automatic"], ["diffusers", "Stable Diffusion"], ["ollama", "Ollama"], ["api", "API"]], cfg.image_engine,
        (value) => save({ image_engine: value }, "Saved"))),
      jobsCard, sdCard, ollamaCard, apiCard,
      field("Try it", el("div", { class: "inline-row" }, prompt, tryBtn)), result),
    section("Video", null, el("div", { class: "card" }, cardRow({
      iconName: "video", title: "Videos from scenes or pictures",
      titleExtra: [data.video.mp4 ? tag("MP4", "good") : tag("GIF only")],
      sub: data.video.mp4 ? "The assistant draws one picture per scene and joins them with slow zoom, cross-fades and captions."
        : "Install video support (the Install button above) to save MP4 files; until then videos are animated GIFs.",
      subClass: "wrap",
    }))),
    section("Other files", null, el("p", { class: "lead", text: "Markdown (.md), Python scripts (.py), web pages (.html), Word, Excel and PowerPoint files need no setup: ask for them in a chat. They appear as links that open in the right app or in your browser." })),
  ];
};

const inUse = (id) => (state.model === id ? [tag("in use", "good")] : []);

function useButton(id, enabled = true) {
  if (state.model === id) return el("button", { class: "btn sm", text: "In use", disabled: true });
  const known = state.catalog.some((m) => m.id === id);
  return el("button", {
    class: "btn sm accent", text: "Use", disabled: !enabled,
    title: known ? `Chat with ${id}` : "Not in the model list yet; it is checked when you send a message",
    onclick: () => {
      setModel(id);
      const remote = state.catalog.find((m) => m.id === id)?.remote;
      toast(remote ? `Now chatting with ${modelLabel(id)}. It is remote: messages and files it reads go to that provider.` : `Now chatting with ${modelLabel(id)}`, remote ? "warn" : "info", remote ? 6000 : 4200);
      render();
    },
  });
}

function providerCards(data) {
  if (!data.providers.length) return [];
  return [el("div", { class: "card" }, ...data.providers.flatMap((p) => [cardRow({
    iconName: p.remote ? "cloud" : "server", title: p.name,
    titleExtra: [p.remote ? tag("remote", "remote") : tag("local", "good"), p.native_tools ? null : tag("prompted tools")].filter(Boolean),
    sub: `${p.base_url} · ${p.models.length} model${p.models.length === 1 ? "" : "s"}`,
    actions: [
      iconButton("edit", "Edit", () => { editingProvider = p; render(); }),
      armedButton("Delete", "Delete?", async () => { if (await act(api(`/api/models/providers/${p.id}`, { method: "DELETE" }))) { emit("models-changed"); render(); } }),
    ],
  }), ...p.models.map((model) => cardRow({ title: model, titleExtra: inUse(`api:${p.id}:${model}`), actions: [useButton(`api:${p.id}:${model}`)] }))]))];
}

let editingProvider = null;

function providerForm(data) {
  const p = editingProvider;
  const preset = el("select", { class: "select" }, el("option", { value: "", text: "Choose a provider…" }),
    data.presets.map((item) => el("option", { value: item.base_url, text: item.name })));
  const name = input({ value: p?.name || "", placeholder: "OpenAI" });
  const url = input({ value: p?.base_url || "", placeholder: "https://api.openai.com/v1", mono: true });
  const key = input({ type: "password", value: "", placeholder: p ? `${p.api_key || "no key"} (leave empty to keep)` : "sk-…", autocomplete: "off" });
  const models = textarea({ rows: 3, mono: true, placeholder: "One model per line, e.g. gpt-4.1-mini" }, (p?.models || []).join("\n"));
  const nativeBox = el("input", { type: "checkbox" });
  nativeBox.checked = p ? p.native_tools : true;
  preset.addEventListener("change", () => {
    const chosen = data.presets.find((item) => item.base_url === preset.value);
    if (!chosen) return;
    url.value = chosen.base_url;
    if (!name.value || data.presets.some((item) => item.name === name.value)) name.value = chosen.name.replace(/ \(.*\)$/, "");
  });
  const find = el("button", { class: "btn sm", text: "Find models", onclick: async () => {
    find.disabled = true;
    const found = await act(api("/api/models/providers/discover", { json: { id: p?.id || null, base_url: url.value, api_key: key.value || null } }));
    find.disabled = false;
    if (found) {
      const chosen = new Set(models.value.split(/[\n,]/).map((s) => s.trim()).filter(Boolean));
      list.replaceChildren(...found.models.slice(0, 300).map((m) => {
        const box = el("input", { type: "checkbox" });
        box.checked = chosen.has(m);
        box.addEventListener("change", () => {
          const set = new Set(models.value.split(/[\n,]/).map((s) => s.trim()).filter(Boolean));
          if (box.checked) set.add(m); else set.delete(m);
          models.value = [...set].join("\n");
        });
        return el("label", { class: "tool-item", style: "cursor:pointer" }, box, el("span", { class: "t-name", text: m }));
      }));
      list.classList.remove("hidden");
      toast(`${found.models.length} model${found.models.length === 1 ? "" : "s"} found: tick the ones you want.`);
    }
  } });
  const list = el("div", { class: "tool-list hidden", style: "max-height:220px;overflow:auto;margin-top:8px" });
  const save = el("button", { class: "btn accent", text: p ? "Save changes" : "Add provider", onclick: async () => {
    const saved = await act(api("/api/models/providers", { json: {
      id: p?.id || null, name: name.value, base_url: url.value, api_key: key.value || null,
      models: models.value.split(/[\n,]/).map((s) => s.trim()).filter(Boolean), native_tools: nativeBox.checked,
    } }), p ? "Saved" : "Provider added");
    if (saved) { editingProvider = null; emit("models-changed"); render(); }
  } });
  return el("div", { class: "card form-card", style: "margin-top:10px" },
    el("h4", { style: "margin:0 0 14px;font-size:14px", text: p ? `Edit ${p.name}` : "Add a provider" }),
    p ? null : field("Preset", preset),
    el("div", { class: "form-grid" }, field("Name", name), field("API base URL", url)),
    field("API key", key, "Stored in data/models.json, readable only by you. Not needed for servers on this computer."),
    field(el("span", {}, "Models ", find), el("div", {}, models, list)),
    el("label", { class: "form-row", style: "display:flex;gap:8px;align-items:center;font-size:13px" }, nativeBox,
      "The API supports native tool calling (turn off for models that only follow prompted tools)"),
    el("div", { class: "form-actions" }, p ? el("button", { class: "btn", text: "Cancel", onclick: () => { editingProvider = null; render(); } }) : null, save));
}

// ---- Connectors (MCP) ----------------------------------------------------------------------------

const MCP_PRESETS = [
  { name: "filesystem", title: "Filesystem", text: "Read and write files in folders you choose", config: { command: "npx", args: ["-y", "@modelcontextprotocol/server-filesystem", "~/Documents"] }, needs: "Edit the folder path in the arguments" },
  { name: "memory", title: "Memory", text: "A knowledge graph the assistant can remember things in", config: { command: "npx", args: ["-y", "@modelcontextprotocol/server-memory"] } },
  { name: "sequential-thinking", title: "Sequential thinking", text: "Step-by-step reasoning tool for hard problems", config: { command: "npx", args: ["-y", "@modelcontextprotocol/server-sequential-thinking"] } },
  { name: "fetch", title: "Fetch", text: "Fetch web pages as Markdown (needs uv)", config: { command: "uvx", args: ["mcp-server-fetch"] } },
  { name: "git", title: "Git", text: "Inspect a git repository (needs uv)", config: { command: "uvx", args: ["mcp-server-git", "--repository", "/path/to/repo"] }, needs: "Set the repository path" },
  { name: "time", title: "Time", text: "Current time and time-zone conversion (needs uv)", config: { command: "uvx", args: ["mcp-server-time"] } },
  { name: "everything", title: "Everything (demo)", text: "Reference server for testing every MCP feature", config: { command: "npx", args: ["-y", "@modelcontextprotocol/server-everything"] } },
];

let mcpForm = null; // null | { name, original, config }
let showJson = false;
const openTools = new Set();
const openLogs = new Map();

RENDER.connectors = async () => {
  const data = await api("/api/mcp");
  const servers = data.servers;
  if (servers.some((s) => s.status === "starting")) poll(1500);
  $("tab-count-connectors").textContent = servers.length || "";
  const head = el("div", { class: "inline-row", style: "flex-wrap:wrap;margin-bottom:18px" },
    el("button", { class: "btn accent", onclick: () => { mcpForm = { name: "", original: null, config: { command: "", args: [] } }; showJson = false; render(); } }, icon("plus", "i i-sm"), "Add connector"),
    el("button", { class: "btn", text: "Paste JSON config", onclick: () => { showJson = !showJson; mcpForm = null; render(); } }),
    data.claude_desktop_config ? el("button", { class: "btn", text: "Import from Claude Desktop", onclick: async () => {
      const result = await act(api("/api/mcp/import", { json: { from_claude_desktop: true } }));
      if (result) { toast(result.added.length ? `Imported ${result.added.join(", ")}` : "Nothing new to import"); afterMcpChange(); }
    } }) : null);
  return [
    el("p", { class: "lead", style: "margin:0 0 16px;color:var(--soft);font-size:13px;max-width:700px",
      text: "Connectors are MCP (Model Context Protocol) servers: they give the assistant new tools — files, databases, APIs, browsers and more. Local servers run as programs on this computer; remote servers are reached by URL. Every tool call asks for your approval unless you choose “Always allow” for that tool." }),
    head,
    showJson ? jsonImportCard() : null,
    mcpForm ? mcpFormCard() : null,
    servers.length ? section("Your connectors", null, ...servers.map(serverCard))
      : el("div", { class: "empty-card" }, el("strong", { text: "No connectors yet" }), "Add one below, or paste a config from Claude Desktop, Cursor or a server's README."),
    section("Popular servers", "Click one to fill in the form. Local servers need Node.js (npx) or uv (uvx) installed.",
      el("div", { class: "preset-grid" }, MCP_PRESETS.map((preset) => el("button", { class: "preset", onclick: () => {
        mcpForm = { name: preset.name, original: null, config: structuredClone(preset.config) };
        showJson = false;
        render();
        if (preset.needs) toast(preset.needs);
      } }, el("span", { class: "card-icon accent" }, icon("plug")), el("div", {}, el("strong", { text: preset.title }), el("span", { text: preset.text })))))),
  ];
};

function afterMcpChange() {
  refreshHealth();
  render();
}

function serverCard(server) {
  const tone = { connected: "good", starting: "accent", error: "bad", disabled: "", stopped: "" }[server.status];
  const label = { connected: "connected", starting: "starting…", error: "error", disabled: "disabled", stopped: "stopped" }[server.status] || server.status;
  const toolsOpen = openTools.has(server.name);
  const actions = [
    switchEl(!server.disabled, async (on) => {
      await act(api(`/api/mcp/servers/${encodeURIComponent(server.name)}/toggle`, { json: { enabled: on } }));
      afterMcpChange();
    }, { label: `Enable ${server.name}` }),
    iconButton("refresh", "Reconnect", async () => { await act(api(`/api/mcp/servers/${encodeURIComponent(server.name)}/restart`, { method: "POST" })); afterMcpChange(); }),
    iconButton("edit", "Edit", async () => {
      const detail = await act(api(`/api/mcp/servers/${encodeURIComponent(server.name)}`));
      if (detail) { mcpForm = { name: server.name, original: server.name, config: detail.config }; showJson = false; render(); $("set-body").scrollTop = 0; }
    }),
    armedButton("Remove", "Remove?", async () => { if (await act(api(`/api/mcp/servers/${encodeURIComponent(server.name)}`, { method: "DELETE" }), `Removed ${server.name}`)) afterMcpChange(); }),
  ];
  const extra = [];
  if (server.error) extra.push(el("div", { class: "err-box", text: server.error }));
  if (server.status === "connected" && server.tools.length && toolsOpen) {
    extra.push(el("div", { class: "tool-list" }, server.tools.map((t) => el("div", { class: "tool-item" },
      el("div", { class: "grow" }, el("div", { class: "t-name" }, t.name, t.read_only ? " " : "", t.read_only ? tag("read-only") : ""), el("div", { class: "t-desc", text: t.description, title: t.description })),
      el("label", {}, "Always allow", switchEl(t.always_allow, async (on) => {
        await act(api(`/api/mcp/servers/${encodeURIComponent(server.name)}/always-allow`, { json: { tool: t.name, allowed: on } }));
        render();
      }, { label: `Always allow ${t.name}` }))))));
  }
  if (openLogs.has(server.name)) extra.push(el("div", { class: "log-box", text: openLogs.get(server.name) || "(no log output)" }));
  const links = el("div", { class: "inline-row", style: "gap:4px;margin-top:6px" },
    server.status === "connected" && server.tools.length ? el("button", { class: "btn sm", text: toolsOpen ? "Hide tools" : `Show ${server.tools.length} tool${server.tools.length === 1 ? "" : "s"}`, onclick: () => { toolsOpen ? openTools.delete(server.name) : openTools.add(server.name); render(); } }) : null,
    server.kind === "local" ? el("button", { class: "btn sm", text: openLogs.has(server.name) ? "Hide log" : "Show log", onclick: async () => {
      if (openLogs.has(server.name)) openLogs.delete(server.name);
      else openLogs.set(server.name, (await act(api(`/api/mcp/servers/${encodeURIComponent(server.name)}`)))?.log || "");
      render();
    } }) : null);
  return el("div", { class: "card" },
    cardRow({
      iconName: server.kind === "local" ? "server" : "cloud", iconClass: server.status === "connected" ? "accent" : "",
      title: server.name,
      titleExtra: [tag(label, tone), server.kind === "remote" ? tag(server.transport) : null, server.server_info ? el("span", { class: "card-sub", text: server.server_info }) : null].filter(Boolean),
      sub: server.kind === "local" ? server.command : server.url, subClass: "mono", actions,
    }),
    el("div", { class: "card-extra" }, links, ...extra));
}

function linesToObject(text, separator) {
  const result = {};
  for (const line of text.split("\n")) {
    const index = line.indexOf(separator);
    if (index <= 0) continue;
    result[line.slice(0, index).trim()] = line.slice(index + 1).trim();
  }
  return result;
}

function mcpFormCard() {
  const form = mcpForm;
  const config = form.config || {};
  let kind = config.url ? "remote" : "local";
  const name = input({ value: form.name, placeholder: "my-server" });
  const command = input({ value: config.command || "", placeholder: "npx, uvx, docker, python, /full/path/to/server", mono: true });
  const args = textarea({ rows: 4, mono: true, placeholder: "One argument per line\n-y\n@modelcontextprotocol/server-filesystem\n/Users/you/Documents" }, (config.args || []).join("\n"));
  const env = textarea({ rows: 3, mono: true, placeholder: "KEY=value, one per line (API tokens go here)" }, Object.entries(config.env || {}).map(([k, v]) => `${k}=${v}`).join("\n"));
  const cwd = input({ value: config.cwd || "", placeholder: "Optional working folder", mono: true });
  const url = input({ value: config.url || "", placeholder: "https://example.com/mcp", mono: true });
  const transport = el("select", { class: "select" }, el("option", { value: "http", text: "Streamable HTTP (recommended)" }), el("option", { value: "sse", text: "SSE (older servers)" }));
  transport.value = config.transport || "http";
  const headers = textarea({ rows: 3, mono: true, placeholder: "Authorization: Bearer …, one header per line" }, Object.entries(config.headers || {}).map(([k, v]) => `${k}: ${v}`).join("\n"));
  const local = el("div", {}, field("Command", command, "The program that starts the server. Common ones: npx (Node.js), uvx (uv), docker."),
    field("Arguments", args), el("div", { class: "form-grid" }, field("Environment variables", env), field("Working folder", cwd)));
  const remote = el("div", {}, field("Server URL", url), el("div", { class: "form-grid" }, field("Transport", transport), field("Headers", headers)));
  const swap = () => { local.classList.toggle("hidden", kind !== "local"); remote.classList.toggle("hidden", kind !== "remote"); };
  swap();
  const save = el("button", { class: "btn accent", text: form.original ? "Save and reconnect" : "Add and connect", onclick: async () => {
    const payload = kind === "local"
      ? { command: command.value.trim(), args: args.value.split("\n").map((s) => s.trim()).filter(Boolean), env: linesToObject(env.value, "="), ...(cwd.value.trim() ? { cwd: cwd.value.trim() } : {}) }
      : { url: url.value.trim(), transport: transport.value, headers: linesToObject(headers.value, ":") };
    if (config.alwaysAllow) payload.alwaysAllow = config.alwaysAllow;
    save.disabled = true;
    save.textContent = "Connecting…";
    const result = await act(api("/api/mcp/servers", { json: { name: name.value.trim(), original: form.original, config: payload } }));
    save.disabled = false;
    if (result) {
      const server = result.servers.find((s) => s.name === name.value.trim());
      toast(server?.status === "connected" ? `${server.name} connected: ${server.tools.length} tools` : `${name.value} saved, but it did not connect: ${server?.error || server?.status}`, server?.status === "connected" ? "info" : "warn", 7000);
      mcpForm = null;
      if (server?.status === "connected") openTools.add(server.name);
      afterMcpChange();
    } else {
      save.textContent = form.original ? "Save and reconnect" : "Add and connect";
    }
  } });
  return el("div", { class: "card form-card", style: "margin-bottom:24px" },
    el("h4", { style: "margin:0 0 14px;font-size:14px", text: form.original ? `Edit ${form.original}` : "New connector" }),
    el("div", { class: "form-grid" }, field("Name", name), field("Type", seg([["local", "Local (command)"], ["remote", "Remote (URL)"]], kind, (value) => { kind = value; swap(); }))),
    local, remote,
    el("div", { class: "form-actions" }, el("button", { class: "btn", text: "Cancel", onclick: () => { mcpForm = null; render(); } }), save));
}

function jsonImportCard() {
  const text = textarea({ rows: 10, mono: true, placeholder: '{\n  "mcpServers": {\n    "memory": { "command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"] }\n  }\n}' });
  const replace = el("input", { type: "checkbox" });
  return el("div", { class: "card form-card", style: "margin-bottom:24px" },
    el("h4", { style: "margin:0 0 6px;font-size:14px", text: "Paste a JSON config" }),
    el("p", { class: "field-help", style: "margin:0 0 12px", text: "The same format Claude Desktop, Cursor and most server READMEs use." }),
    text,
    el("label", { style: "display:flex;gap:8px;align-items:center;font-size:13px;margin-top:10px" }, replace, "Replace servers that already exist with the same name"),
    el("div", { class: "form-actions" },
      el("button", { class: "btn", text: "Cancel", onclick: () => { showJson = false; render(); } }),
      el("button", { class: "btn accent", text: "Import", onclick: async () => {
        let parsed;
        try { parsed = JSON.parse(text.value); } catch (error) { toast(`That is not valid JSON: ${error.message}`, "bad"); return; }
        const result = await act(api("/api/mcp/import", { json: { config: parsed, replace: replace.checked } }));
        if (result) { toast(result.added.length ? `Imported ${result.added.join(", ")}` : "All of those servers already exist"); showJson = false; afterMcpChange(); }
      } })));
}

// ---- Skills ------------------------------------------------------------------------------------------

let skillForm = null; // null | { original, name, description, body }

RENDER.skills = async () => {
  const data = await api("/api/agent-skills");
  state.agentSkills = data.skills;
  emit("agent-skills-changed");
  $("tab-count-skills").textContent = data.skills.filter((s) => s.enabled).length || "";
  const zip = el("input", { type: "file", accept: ".zip,.skill", class: "hidden" });
  zip.addEventListener("change", async () => {
    const file = zip.files[0];
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    const result = await act(api("/api/agent-skills/import", { method: "POST", body: form }));
    if (result) { toast(`Installed ${result.added.join(", ")}`); refreshAgentSkills(); render(); }
  });
  const importFolder = async () => {
    const path = isDesktop() ? await chooseFolder() : window.prompt("Full path of a skill folder (holding SKILL.md), or a folder of skills:");
    if (!path) return;
    const result = await act(api("/api/agent-skills/import-folder", { json: { path } }));
    if (result) { toast(`Installed ${result.added.join(", ")}`); refreshAgentSkills(); render(); }
  };
  return [
    el("p", { class: "lead", style: "margin:0 0 16px;color:var(--soft);font-size:13px;max-width:700px",
      text: "Skills are folders of instructions (a SKILL.md, plus optional reference files) that teach the assistant how to do specific tasks your way. The assistant sees each skill's name and description, and loads the full instructions when a request matches. You can also pick a skill for a message from the + menu or by typing /. Same format as Claude's Agent Skills." }),
    el("div", { class: "inline-row", style: "flex-wrap:wrap;margin-bottom:18px" },
      el("button", { class: "btn accent", onclick: () => { skillForm = { original: null, name: "", description: "", body: "" }; render(); } }, icon("plus", "i i-sm"), "New skill"),
      el("button", { class: "btn", onclick: () => zip.click() }, icon("upload", "i i-sm"), "Import .zip"),
      el("button", { class: "btn", text: "Import folder…", onclick: importFolder }), zip),
    skillForm ? skillFormCard() : null,
    data.skills.length ? el("div", { class: "card" }, ...data.skills.map((s) => cardRow({
      iconName: "sparkles", iconClass: s.enabled ? "accent" : "", title: s.name,
      titleExtra: [s.errors.length ? tag("needs fixing", "bad") : null, s.files.length > 1 ? tag(`${s.files.length} files`) : null].filter(Boolean),
      sub: s.errors.length ? s.errors.join("; ") : s.description, subClass: "wrap",
      actions: [
        switchEl(s.enabled, async (on) => { await act(api(`/api/agent-skills/${s.name}/toggle`, { json: { enabled: on } })); refreshHealth(); render(); }, { label: `Enable ${s.name}` }),
        iconButton("edit", "Edit", async () => {
          const detail = await act(api(`/api/agent-skills/${s.name}`));
          if (detail) { skillForm = { original: s.name, name: s.name, description: detail.description, body: detail.body, files: detail.files }; render(); $("set-body").scrollTop = 0; }
        }),
        armedButton("Delete", "Delete?", async () => { if (await act(api(`/api/agent-skills/${s.name}`, { method: "DELETE" }), `${s.name} moved to the Trash`)) { refreshAgentSkills(); refreshHealth(); render(); } }),
      ],
    }))) : el("div", { class: "empty-card" }, el("strong", { text: "No skills yet" }), "Create one, or import a skill folder or .zip."),
    el("p", { class: "field-help", style: "margin-top:12px", text: `Skills live in ${data.folder}. The assistant can read them but never change them.` }),
  ];
};

function skillFormCard() {
  const form = skillForm;
  const name = input({ value: form.name, placeholder: "weekly-report", maxlength: 64 });
  const description = textarea({ rows: 2, placeholder: "What it does and when to use it, e.g. “Write the weekly status report from my notes. Use when the user asks for a weekly report or status update.”", maxlength: 1024 }, form.description);
  const body = textarea({ rows: 16, mono: true, placeholder: "# Weekly report\n\n## Steps\n1. …\n\n## Output format\n…" }, form.body);
  const save = el("button", { class: "btn accent", text: form.original ? "Save" : "Create skill", onclick: async () => {
    const result = await act(api("/api/agent-skills", { json: { name: name.value, original: form.original, description: description.value, body: body.value } }), "Skill saved");
    if (result) { skillForm = null; refreshAgentSkills(); refreshHealth(); render(); }
  } });
  return el("div", { class: "card form-card", style: "margin-bottom:24px" },
    el("h4", { style: "margin:0 0 14px;font-size:14px", text: form.original ? `Edit ${form.original}` : "New skill" }),
    field("Name", name, "Lowercase letters, numbers and dashes."),
    field("Description", description, "The assistant decides when to use the skill from this, so name the situations and words that should trigger it."),
    field("Instructions", body, form.files?.length > 1 ? `Other files in this skill: ${form.files.filter((f) => f !== "SKILL.md").join(", ")}` : "Markdown. Steps, the exact output format, and rules."),
    el("div", { class: "form-actions" }, el("button", { class: "btn", text: "Cancel", onclick: () => { skillForm = null; render(); } }), save));
}

// ---- Agents & permissions -----------------------------------------------------------------------

RENDER.permissions = async () => {
  const [permissions, skills, agents, health, tree] = await Promise.all([
    api("/api/permissions"), api("/api/skills"), api("/api/agents"), api("/api/health"),
    api(`/api/files/tree?${qs({ session_id: session.id })}`).catch(() => ({ entries: [] })),
  ]);
  const archRoot = tree.entries.find((e) => e.root === "config");
  const openFile = (name) => openFilesWindow(`${archRoot.path}/${name}`);
  return [
    health.warnings.length ? section([icon("warn", "i"), "Configuration warnings"], null,
      el("div", { class: "err-box" }, health.warnings.join("\n"))) : null,
    section("Applications", "What the assistant may use, from arch/permission.md. It is re-read before every action and the assistant cannot change it. Operations marked * need your approval each time.",
      archRoot ? el("div", { style: "margin-bottom:10px" }, el("button", { class: "btn sm", onclick: () => openFile("permission.md") }, icon("edit", "i i-sm"), "Edit permission.md in the Files window")) : null,
      el("div", { class: "card" }, ...permissions.map((app) => cardRow({
        iconName: "shield", iconClass: app.enabled ? "accent" : "", title: `#${app.id}`,
        titleExtra: [tag(app.enabled ? "enabled" : "disabled", app.enabled ? "good" : "bad"), el("span", { class: "card-sub", text: app.name })],
        sub: app.operations.map((op) => app.approval_required && !app.approval_exempt.includes(op) ? `${op}*` : op).join("  ·  "), subClass: "mono",
      })))),
    section("Agents", "Specialist roles in arch/agents/. Type / in the message box to pick one, or let the app choose from your message.",
      archRoot ? el("div", { style: "margin-bottom:10px" }, el("button", { class: "btn sm", onclick: () => openFile("system.md") }, icon("edit", "i i-sm"), "Edit system.md in the Files window")) : null,
      ...agents.map((agent) => agentCard(agent, skills.filter((s) => s.agent === agent.id)))),
  ];
};

function agentCard(agent, skills) {
  const skillRow = (s) => el("div", { class: "tool-item" },
    el("div", { class: "grow" },
      el("div", { class: "t-name", text: s.command || s.id }),
      el("div", { class: "t-desc", text: s.valid ? s.description : `Malformed: ${s.errors.join("; ")}` })));
  return el("div", { class: "card" },
    cardRow({
      iconName: "user", title: agent.name, sub: agent.description, subClass: "wrap",
      titleExtra: agent.applications.slice(0, 6).map((id) => tag(`#${id}`)),
    }),
    el("div", { class: "card-extra" }, el("div", { class: "tool-list" }, skills.map(skillRow))));
}

// ---- Activity -------------------------------------------------------------------------------------------

let activityMode = "live";

RENDER.activity = async () => {
  const switcher = seg([["live", "Live log"], ["audit", "Audit history (this chat)"]], activityMode, (mode) => { activityMode = mode; render(); });
  if (activityMode === "live") {
    const lines = state.logs.slice(-400);
    return [switcher, el("div", { class: "card form-card activity-log", style: "margin-top:14px" },
      lines.length ? lines.map((line) => el("div", { class: line.level }, el("span", { class: "t", text: `[${line.time}] ` }), line.message))
        : el("div", { class: "t", text: "What the assistant does appears here as it happens." }))];
  }
  const rows = await api(`/api/execution/history?${qs({ session_id: session.id })}`);
  return [switcher, el("p", { class: "field-help", text: "Every tool call is recorded in logs/audit.jsonl." }),
    rows.length ? el("div", { class: "card", style: "margin-top:10px" }, rows.reverse().map((row) => el("div", {
      class: "audit-row", title: `${row.agent} / ${row.skill} / ${row.model}\npermission: ${row.permission}\napproval: ${row.approval}\n${row.result}`,
    }, el("span", { class: "card-sub", text: new Date(row.timestamp).toLocaleTimeString() }),
      el("span", { class: `st-${row.status}`, text: row.status }),
      el("span", { class: "op", text: row.operation }),
      el("span", { class: "tg", text: row.target }))))
      : el("div", { class: "empty-card", style: "margin-top:10px", text: "No tool activity in this chat yet." })];
};

// ---- init -------------------------------------------------------------------------------------------------

export function initSettings() {
  for (const button of document.querySelectorAll(".set-tab")) button.addEventListener("click", () => select(button.dataset.tab));
  $("close-settings").addEventListener("click", () => $("settings").close());
  $("settings").addEventListener("close", () => { clearTimeout(pollTimer); refreshHealth(); });
  $("settings").addEventListener("mousedown", (event) => { if (event.target === $("settings")) $("settings").close(); });
  on("log-line", () => { if ($("settings").open && current === "activity" && activityMode === "live") render(); });
  on("models-changed", () => refreshHealth());
}
