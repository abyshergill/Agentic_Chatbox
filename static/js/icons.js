// Line icons (24x24, stroke). `icon(name)` returns an <svg>; `hydrateIcons()` fills every [data-icon] element.

const P = {
  logo: '<path d="M5 6.5A3.5 3.5 0 0 1 8.5 3h7A3.5 3.5 0 0 1 19 6.5v5a3.5 3.5 0 0 1-3.5 3.5H11l-4.2 3.6c-.5.4-1.3.1-1.3-.6V15A3.5 3.5 0 0 1 5 11.5z" fill="currentColor" stroke="none"/><circle cx="9.2" cy="9" r="1.15" fill="var(--accent)" stroke="none"/><circle cx="12" cy="9" r="1.15" fill="var(--accent)" stroke="none"/><circle cx="14.8" cy="9" r="1.15" fill="var(--accent)" stroke="none"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  x: '<path d="M6 6l12 12M18 6 6 18"/>',
  search: '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>',
  sidebar: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M9.5 4.5v15"/>',
  panel: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M14.5 4.5v15"/>',
  folder: '<path d="M3.5 7.5A2 2 0 0 1 5.5 5.5h4l2 2h7a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/>',
  file: '<path d="M14 3.5H7.5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V8z"/><path d="M14 3.5V8h4.5"/>',
  paperclip: '<path d="m20 11.5-7.8 7.8a4.6 4.6 0 0 1-6.5-6.5l8.2-8.2a3 3 0 0 1 4.3 4.3L10 17.1a1.5 1.5 0 0 1-2.1-2.1l7.4-7.4"/>',
  at: '<circle cx="12" cy="12" r="3.8"/><path d="M15.8 8.2V13a2.5 2.5 0 0 0 5 0v-1a8.8 8.8 0 1 0-3.5 7"/>',
  plug: '<path d="M9 3.5v4M15 3.5v4M6.5 7.5h11V11a5.5 5.5 0 0 1-11 0zM12 16.5v4"/>',
  sparkles: '<path d="M10 3.5 11.6 8a3 3 0 0 0 1.9 1.9L18 11.5l-4.5 1.6a3 3 0 0 0-1.9 1.9L10 19.5l-1.6-4.5a3 3 0 0 0-1.9-1.9L2 11.5l4.5-1.6A3 3 0 0 0 8.4 8z"/><path d="M18.5 3v4M16.5 5h4"/>',
  cpu: '<rect x="6.5" y="6.5" width="11" height="11" rx="2"/><path d="M9.5 2.5v4M14.5 2.5v4M9.5 17.5v4M14.5 17.5v4M2.5 9.5h4M2.5 14.5h4M17.5 9.5h4M17.5 14.5h4"/>',
  theme: '<circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 1 0 16z" fill="currentColor" stroke="none"/>',
  activity: '<path d="M3 12h4l3-7 4 14 3-7h4"/>',
  sliders: '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
  list: '<path d="M9 6.5h11M9 12h11M9 17.5h11"/><circle cx="4.5" cy="6.5" r="1" fill="currentColor"/><circle cx="4.5" cy="12" r="1" fill="currentColor"/><circle cx="4.5" cy="17.5" r="1" fill="currentColor"/>',
  bolt: '<path d="M13 2.5 4.5 13.5H12l-1 8 8.5-11H12z"/>',
  chevron: '<path d="m7 10 5 5 5-5"/>',
  "chevron-right": '<path d="m10 7 5 5-5 5"/>',
  "arrow-up": '<path d="M12 19V5M6 11l6-6 6 6"/>',
  stop: '<rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor"/>',
  user: '<circle cx="12" cy="8.5" r="3.8"/><path d="M4.5 20a7.5 7.5 0 0 1 15 0"/>',
  shield: '<path d="M12 3 5 6v5.5c0 4.3 2.9 7.7 7 9 4.1-1.3 7-4.7 7-9V6z"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  copy: '<rect x="8.5" y="8.5" width="11" height="11" rx="2"/><path d="M15.5 8.5V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v7.5a2 2 0 0 0 2 2h2.5"/>',
  retry: '<path d="M20 12a8 8 0 1 1-2.3-5.6M20 4v4.5h-4.5"/>',
  edit: '<path d="M14.5 5.5 18.5 9.5M4 20l4.2-1 10.6-10.6a2 2 0 0 0 0-2.8l-.4-.4a2 2 0 0 0-2.8 0L5 15.8z"/>',
  trash: '<path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 12.5h9l1-12.5"/>',
  terminal: '<rect x="3" y="4.5" width="18" height="15" rx="2.5"/><path d="m7 9.5 3 2.5-3 2.5M12.5 15h4.5"/>',
  tool: '<path d="M14.5 6.5a4 4 0 0 0 5 5L12 19a2.1 2.1 0 0 1-3-3l7.5-7.5"/><path d="M14.5 6.5 17 4"/>',
  globe: '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.5 2.7 3.5 5.5 3.5 8.5s-1 5.8-3.5 8.5c-2.5-2.7-3.5-5.5-3.5-8.5s1-5.8 3.5-8.5z"/>',
  data: '<ellipse cx="12" cy="6" rx="7" ry="2.5"/><path d="M5 6v12c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5V6M5 12c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5"/>',
  git: '<circle cx="6.5" cy="6" r="2"/><circle cx="6.5" cy="18" r="2"/><circle cx="17.5" cy="9" r="2"/><path d="M6.5 8v8M17.5 11c0 3.5-5 3-9.5 5.5"/>',
  eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.8"/>',
  download: '<path d="M12 4v11M7 10.5l5 5 5-5M4.5 19.5h15"/>',
  upload: '<path d="M12 20V9M7 13.5l5-5 5 5M4.5 4.5h15"/>',
  power: '<path d="M12 3.5v8M7 6.5a7 7 0 1 0 10 0"/>',
  refresh: '<path d="M20 12a8 8 0 1 1-2.3-5.6M20 4v4.5h-4.5"/>',
  lock: '<rect x="5" y="10.5" width="14" height="10" rx="2"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/>',
  warn: '<path d="M12 4 2.5 20h19z"/><path d="M12 10v4.5M12 17.5v.5"/>',
  server: '<rect x="4" y="4" width="16" height="7" rx="2"/><rect x="4" y="13" width="16" height="7" rx="2"/><path d="M8 7.5h.01M8 16.5h.01"/>',
  image: '<rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><circle cx="9" cy="9.5" r="1.8"/><path d="m20.5 16-5-5-9 8.5"/>',
  video: '<rect x="3" y="6" width="13" height="12" rx="2.5"/><path d="m16 10.5 5-3v9l-5-3z"/>',
  gpu: '<rect x="2.5" y="7" width="19" height="10" rx="2"/><circle cx="8" cy="12" r="2.5"/><circle cx="16" cy="12" r="2.5"/><path d="M6 17v2.5M10 17v2.5M14 17v2.5M18 17v2.5"/>',
  cloud: '<path d="M7 18.5a4.5 4.5 0 0 1-.6-9 6 6 0 0 1 11.5 1.5 3.8 3.8 0 0 1-.4 7.5z"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="m11 12 8.5-8.5M16.5 6.5 19 9"/>',
  spark: '<path d="M12 2.5c.6 4.6 2.9 6.9 7.5 7.5-4.6.6-6.9 2.9-7.5 7.5-.6-4.6-2.9-6.9-7.5-7.5 4.6-.6 6.9-2.9 7.5-7.5z" fill="currentColor" stroke="none"/><path d="M19 15.5c.3 2 1.2 2.9 3 3.2-1.8.3-2.7 1.2-3 3.3-.3-2.1-1.2-3-3-3.3 1.8-.3 2.7-1.2 3-3.2z" fill="currentColor" stroke="none"/>',
};

const NS = "http://www.w3.org/2000/svg";

export function icon(name, cls = "i") {
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.7");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("class", cls);
  svg.innerHTML = P[name] || P.tool; // Static strings defined above, never user data.
  return svg;
}

export function hydrateIcons(root = document) {
  for (const node of root.querySelectorAll("[data-icon]")) {
    if (node.dataset.hydrated) continue;
    node.dataset.hydrated = "1";
    node.prepend(icon(node.dataset.icon));
  }
}

// An icon for a tool call, from its application.
export function toolIcon(app) {
  return {
    filesystem: "file", terminal: "terminal", python: "terminal", git: "git", data: "data", web: "globe",
    documents: "file", mcp: "plug", skills: "sparkles", delegate: "user", browser: "globe", vscode: "edit",
  }[app] || "tool";
}
