// frontend/js/theme.js
const STORAGE_KEY = "fathom-stories:theme";

export function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  const btn = document.getElementById("btn-theme");
  if (btn) btn.textContent = theme === "light" ? "◑" : "◐";
}

export function currentTheme() {
  return document.documentElement.getAttribute("data-theme") || "dark";
}

export function loadTheme() {
  let saved = null;
  try { saved = localStorage.getItem(STORAGE_KEY); } catch (_) {}
  applyTheme(saved === "light" ? "light" : "dark");
}

export function toggleTheme() {
  const next = currentTheme() === "light" ? "dark" : "light";
  applyTheme(next);
  try { localStorage.setItem(STORAGE_KEY, next); } catch (_) {}
  window.dispatchEvent(new CustomEvent("theme-changed", { detail: { theme: next } }));
}

