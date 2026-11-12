// Dashboards and other HTML pages open in the default web browser, through the app's viewer (which allows no scripts).
import { $, on } from "./util.js";
import { activeFile } from "./editor.js";
import { openNative } from "./desktop.js";

function syncButton() {
  const file = activeFile();
  $("preview-open").classList.toggle("hidden", !(file && /\.html?$/i.test(file)));
}

export function initDashboard() {
  $("preview-open").addEventListener("click", () => { const file = activeFile(); if (file) openNative(file); });
  on("preview-dashboard", (path) => openNative(path));
  on("active-file", syncButton);
}
