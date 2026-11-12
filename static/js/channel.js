// Messages between the chat window and the Files window (open this file, files changed, project chosen...).
// Uses BroadcastChannel, and in the desktop app also relays through Python, since separate native windows may not
// share a channel. Each message carries an id, so one delivered twice is handled once.

const SELF = Math.random().toString(36).slice(2);
const seen = new Set();
const handlers = new Map();
const channel = "BroadcastChannel" in window ? new BroadcastChannel("agentic-chatbox") : null;

function deliver(message) {
  if (!message || typeof message.type !== "string" || message.from === SELF) return;
  if (message.id) {
    if (seen.has(message.id)) return;
    seen.add(message.id);
    if (seen.size > 500) seen.delete(seen.values().next().value);
  }
  for (const handler of handlers.get(message.type) || []) handler(message);
}

channel?.addEventListener("message", (event) => deliver(event.data));
window.__fromApp = deliver; // Called by desktop.py.

export function send(type, data = {}) {
  const message = { ...data, type, from: SELF, id: `${SELF}-${Date.now()}-${Math.random().toString(36).slice(2)}` };
  channel?.postMessage(message);
  window.pywebview?.api?.relay?.(JSON.stringify(message))?.catch?.(() => {});
}

export function listen(type, handler) {
  if (!handlers.has(type)) handlers.set(type, []);
  handlers.get(type).push(handler);
}
