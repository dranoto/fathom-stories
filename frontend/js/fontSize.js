// frontend/js/fontSize.js
const STORAGE_KEY = "fathom-stories:font-scale";
const VALID = ["small", "medium", "large"];

export function getFontSize() {
  const attr = document.documentElement.getAttribute("data-font-scale");
  return VALID.includes(attr) ? attr : "medium";
}

export function applyFontSize(level) {
  if (!VALID.includes(level)) level = "medium";
  document.documentElement.setAttribute("data-font-scale", level);
  window.dispatchEvent(new CustomEvent("font-size-changed", { detail: { level } }));
}

export function setFontSize(level) {
  applyFontSize(level);
  try { localStorage.setItem(STORAGE_KEY, level); } catch (_) {}
}

export function loadFontSize() {
  let saved = null;
  try { saved = localStorage.getItem(STORAGE_KEY); } catch (_) {}
  applyFontSize(VALID.includes(saved) ? saved : "medium");
}
