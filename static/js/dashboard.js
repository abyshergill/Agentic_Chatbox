// Dashboard preview: shows a generated HTML file in a sandboxed frame over the editor.
// The frame has no scripts and the backend serves it with a policy that forbids them.
import { $, emit, on, qs, session } from "./util.js";
import { activeFile } from "./editor.js";

let current = null;

function url(path) {
  return `/api/dashboards/view?${qs({ session_id: session.id, path })}`;
}

function show(path) {
  current = path;
  $("preview-path").textContent = path;
  $("preview-frame").src = url(path);
  $("preview-external").href = url(path);
  $("preview-host").classList.remove("hidden");
  $("preview-host").classList.add("flex");
  emit("show-pane", "pane-editor");
}

function hide() {
  current = null;
  $("preview-frame").removeAttribute("src");
  $("preview-host").classList.add("hidden");
  $("preview-host").classList.remove("flex");
}

function syncButton() {
  const file = activeFile();
  $("preview-open").classList.toggle("hidden", !(file && /\.html$/i.test(file)));
}

export function initDashboard() {
  $("preview-close").addEventListener("click", hide);
  $("preview-open").addEventListener("click", () => { const file = activeFile(); if (file) show(file); });
  on("preview-dashboard", show);
  on("active-file", () => { syncButton(); if (current) hide(); });
  // When the assistant writes a dashboard, show it straight away. Re-saving the open one refreshes it.
  on("paths-changed", (paths) => {
    const made = paths.find((p) => /(^|\/)(dashboards|presentations)\/[^/]+\.html$/i.test(p));
    if (made) show(made);
  });
}
