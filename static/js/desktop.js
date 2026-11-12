// Opening files outside the app, and desktop-window extras.
// Opening works in the browser version too: the server runs on this computer and asks the system to open the file.
import { api, emit, session, store } from "./util.js";

// Files that are better in their own app than in the code editor. The backend re-checks every request.
const NATIVE = new Set(["pptx", "ppt", "key", "pdf", "docx", "doc", "xlsx", "xls", "xlsm", "numbers", "pages", "odt", "ods", "odp",
  "png", "jpg", "jpeg", "gif", "webp"]);
const ext = (path) => path.split(".").pop().toLowerCase();

export const isDesktop = () => Boolean(window.pywebview?.api?.open_path);
export const opensNatively = (path) => NATIVE.has(ext(path));

async function openRequest(path, reveal) {
  try {
    await api("/api/files/open", { json: { session_id: session.id, path, reveal, base: store.get("workspace.project") || null } });
  } catch (error) {
    emit("notice", { level: "bad", message: error.message });
  }
}

export async function absolutePath(path) {
  if (!isDesktop()) return null;
  const result = await window.pywebview.api.absolute_path(path);
  return typeof result === "object" && result ? result.path : null;
}

export const openNative = (path) => openRequest(path, false);
export const revealNative = (path) => openRequest(path, true);
export const chooseGguf = () => window.pywebview.api.choose_gguf();
export const chooseFolder = () => window.pywebview.api.choose_folder();

export function initDesktop() {
  // The bridge arrives shortly after the page loads; redraw so the desktop buttons appear.
  const ready = () => {
    document.documentElement.dataset.desktop = "true";
    emit("desktop-ready");
  };
  window.addEventListener("pywebviewready", ready);
  if (isDesktop()) ready(); // The bridge may already be there.
}
