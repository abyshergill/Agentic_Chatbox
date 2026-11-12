// Small UI building blocks shared by the chat and settings: popovers, toasts, switches.
import { $, el } from "./util.js";
import { icon } from "./icons.js";

let openPopover = null;

export function closePopover() {
  if (!openPopover) return;
  const { node, cleanup } = openPopover;
  openPopover = null;
  cleanup();
  node.remove();
}

// Opens a popover anchored to `anchor`. `build(close)` returns its child nodes. Placed above the anchor when there is
// more room there (the composer sits at the bottom of the window).
export function popover(anchor, build, { align = "left", width } = {}) {
  if (openPopover?.anchor === anchor) return closePopover();
  closePopover();
  const node = el("div", { class: "popover", role: "menu" });
  if (width) node.style.width = `${width}px`;
  const render = () => node.replaceChildren(...build(closePopover, render).filter(Boolean));
  render();
  document.body.append(node);
  const place = () => {
    const box = anchor.getBoundingClientRect();
    const height = node.offsetHeight;
    const below = window.innerHeight - box.bottom;
    const top = below > height + 12 || box.top < height + 12 ? box.bottom + 6 : box.top - height - 6;
    let left = align === "right" ? box.right - node.offsetWidth : box.left;
    left = Math.max(8, Math.min(left, window.innerWidth - node.offsetWidth - 8));
    node.style.top = `${Math.max(8, top)}px`;
    node.style.left = `${left}px`;
  };
  place();
  const onDown = (event) => {
    if (!node.contains(event.target) && !anchor.contains(event.target)) closePopover();
  };
  const onKey = (event) => { if (event.key === "Escape") { event.stopPropagation(); closePopover(); anchor.focus(); } };
  const observer = new ResizeObserver(place);
  observer.observe(node);
  document.addEventListener("mousedown", onDown, true);
  document.addEventListener("keydown", onKey, true);
  window.addEventListener("resize", closePopover);
  openPopover = {
    node, anchor,
    cleanup: () => {
      observer.disconnect();
      document.removeEventListener("mousedown", onDown, true);
      document.removeEventListener("keydown", onKey, true);
      window.removeEventListener("resize", closePopover);
    },
  };
  node.querySelector("button:not(:disabled), input")?.focus({ preventScroll: true });
  return node;
}

export function menuItem({ label, sub, iconName, onClick, right, active, disabled, title }) {
  return el("button", { class: `pop-item${active ? " active" : ""}`, role: "menuitem", disabled, title, onclick: onClick },
    iconName ? icon(iconName) : null,
    el("span", { class: "grow" }, el("span", { text: label }), sub ? el("span", { class: "sub", text: sub }) : null),
    right || null);
}

export function switchEl(checked, onChange, { label, disabled } = {}) {
  const input = el("input", { type: "checkbox", role: "switch", "aria-label": label || "Toggle", disabled });
  input.checked = Boolean(checked);
  input.addEventListener("change", () => onChange(input.checked, input));
  input.addEventListener("click", (event) => event.stopPropagation());
  return el("span", { class: "switch" }, input, el("span"));
}

export function toast(message, level = "info", ms = 4200) {
  const node = el("div", { class: `toast ${level === "info" ? "" : level}`, role: level === "bad" ? "alert" : "status", text: message });
  $("toasts").append(node);
  setTimeout(() => node.remove(), ms);
}

export function tag(text, tone = "") {
  return el("span", { class: `tag ${tone}`, text });
}

export function iconButton(name, title, onClick, cls = "ghost sm") {
  return el("button", { class: cls, title, "aria-label": title, onclick: onClick }, icon(name, "i i-sm"));
}

// A button that asks once more before doing something destructive.
export function armedButton(label, confirmLabel, onConfirm, cls = "btn sm danger") {
  const button = el("button", { class: cls, text: label });
  let timer = null;
  button.addEventListener("click", async (event) => {
    event.stopPropagation();
    if (!button.dataset.armed) {
      button.dataset.armed = "1";
      button.textContent = confirmLabel;
      timer = setTimeout(() => { delete button.dataset.armed; button.textContent = label; }, 3000);
      return;
    }
    clearTimeout(timer);
    button.disabled = true;
    try { await onConfirm(); } finally { button.disabled = false; delete button.dataset.armed; button.textContent = label; }
  });
  return button;
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const area = el("textarea", { style: "position:fixed;opacity:0" });
    area.value = text;
    document.body.append(area);
    area.select();
    const ok = document.execCommand("copy");
    area.remove();
    return ok;
  }
}

export function timeAgo(seconds) {
  const diff = Date.now() / 1000 - seconds;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} h ago`;
  return new Date(seconds * 1000).toLocaleDateString();
}
