// Small shared helpers: DOM building, API calls, safe storage.

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child !== null && child !== undefined && child !== false) node.append(child);
  }
  return node;
}

export const $ = (id) => document.getElementById(id);

export const store = {
  get(key, fallback = null) {
    try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(key, value); } catch { /* A convenience only. */ }
  },
};

export function newSessionId() {
  const bytes = crypto.getRandomValues(new Uint8Array(12));
  return "s" + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export const session = {
  id: (() => {
    try {
      // This tab's conversation, else the last one used (so the app reopens where you left off), else a new one.
      const existing = sessionStorage.getItem("workspace.session") || localStorage.getItem("workspace.lastSession");
      const id = existing || newSessionId();
      sessionStorage.setItem("workspace.session", id);
      localStorage.setItem("workspace.lastSession", id);
      return id;
    } catch {
      return newSessionId();
    }
  })(),
};

// Switches the page to another conversation (History) or a new one (New chat).
export function setSessionId(id) {
  session.id = id;
  try { sessionStorage.setItem("workspace.session", id); localStorage.setItem("workspace.lastSession", id); } catch { /* Convenience only. */ }
}

export async function api(path, options = {}) {
  const init = { ...options };
  if (options.json !== undefined) {
    init.method = options.method || "POST";
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(options.json);
    delete init.json;
  }
  const response = await fetch(path, init);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch { /* Keep the status text. */ }
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

export const qs = (params) => new URLSearchParams(params).toString();

// Shared events between panes.
export const bus = new EventTarget();
export const emit = (name, detail) => bus.dispatchEvent(new CustomEvent(name, { detail }));
export const on = (name, handler) => bus.addEventListener(name, (event) => handler(event.detail));
