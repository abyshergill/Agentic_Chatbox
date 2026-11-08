// Desktop window extras. Present only when the app runs through desktop.py; in a browser these do nothing.
import { emit } from "./util.js";

// Files that are better in their own app than in the code editor. The backend re-checks every request.
const NATIVE = new Set(["pptx", "ppt", "key", "pdf", "docx", "doc", "xlsx", "xls", "numbers", "pages", "odt", "ods", "odp",
  "png", "jpg", "jpeg", "gif", "webp"]);
const ext = (path) => path.split(".").pop().toLowerCase();

export const isDesktop = () => Boolean(window.pywebview?.api?.open_path);
export const opensNatively = (path) => isDesktop() && NATIVE.has(ext(path));

async function call(method, path) {
  const result = await window.pywebview.api[method](path);
  if (result !== true) window.alert(result || "That did not work.");
}

export async function absolutePath(path) {
  const result = await window.pywebview.api.absolute_path(path);
  return typeof result === "object" && result ? result.path : null;
}

export const openNative = (path) => call("open_path", path);
export const chooseGguf = () => window.pywebview.api.choose_gguf();
export const revealNative = (path) => call("reveal_path", path);

export function initDesktop() {
  // The bridge arrives shortly after the page loads; redraw so the desktop buttons appear.
  const ready = () => {
    document.documentElement.dataset.desktop = "true";
    emit("desktop-ready");
  };
  window.addEventListener("pywebviewready", ready);
  if (isDesktop()) ready(); // The bridge may already be there.
}
