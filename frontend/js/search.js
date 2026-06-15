// frontend/js/search.js
import { searchArticles } from "./apiService.js";
import { getActiveEventId, setInboxOpen, setActiveEventId } from "./state.js";
import { escapeHtml } from "./eventTabs.js";
import { closeReader } from "./reader.js";

let debounceHandle = null;
let currentQuery = "";
let lastResults = [];

function formatRelative(d) {
  if (!d) return "";
  const diff = Date.now() - new Date(d).getTime();
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return "just now";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h`;
  const days = Math.floor(hr / 24);
  if (days < 30) return `${days}d`;
  return new Date(d).toLocaleDateString();
}

function renderResults(results) {
  const panel = document.getElementById("search-results");
  if (!results.length) {
    panel.innerHTML = `<div class="search-empty">No matches</div>`;
    panel.hidden = false;
    return;
  }
  panel.innerHTML = results.map(a => {
    const inEvent = a.event_id
      ? `<div class="search-result-event">in: ${escapeHtml(a.event_name || "(unnamed event)")}</div>`
      : `<div class="search-result-event search-result-inbox">in: Inbox</div>`;
    const readCls = a.is_read ? "search-result-read" : "";
    return `<div class="search-result ${readCls}" data-article-id="${a.id}" data-event-id="${a.event_id || ""}">
      <div class="search-result-title">${escapeHtml(a.title || "(untitled)")}</div>
      <div class="search-result-meta">
        <span>${escapeHtml(a.publisher_name || "")}</span>
        <span>·</span>
        <span>${formatRelative(a.published_date)}</span>
        ${inEvent}
      </div>
    </div>`;
  }).join("");
  panel.hidden = false;
  panel.querySelectorAll(".search-result").forEach(el => {
    el.addEventListener("click", () => {
      const id = parseInt(el.dataset.articleId, 10);
      const eventId = el.dataset.eventId ? parseInt(el.dataset.eventId, 10) : null;
      openResult(id, eventId);
    });
  });
}

async function openResult(articleId, eventId) {
  closeSearch();
  if (eventId) {
    setInboxOpen(false);
    setActiveEventId(eventId);
    window.dispatchEvent(new CustomEvent("open-reader", { detail: { articleId } }));
  } else {
    setInboxOpen(true);
    setActiveEventId(null);
    window.dispatchEvent(new CustomEvent("open-reader", { detail: { articleId } }));
  }
}

async function runSearch(q) {
  if (!q) {
    const panel = document.getElementById("search-results");
    panel.hidden = true;
    panel.innerHTML = "";
    lastResults = [];
    return;
  }
  try {
    const results = await searchArticles({ keyword: q, limit: 25 });
    lastResults = results;
    if (currentQuery === q) renderResults(results);
  } catch (e) {
    const panel = document.getElementById("search-results");
    panel.innerHTML = `<div class="search-empty">Error: ${escapeHtml(e.message)}</div>`;
    panel.hidden = false;
  }
}

function closeSearch() {
  const input = document.getElementById("search-input");
  const panel = document.getElementById("search-results");
  input.value = "";
  currentQuery = "";
  lastResults = [];
  panel.hidden = true;
  panel.innerHTML = "";
  document.activeElement.blur();
}

export function setupSearch() {
  const input = document.getElementById("search-input");
  if (!input) return;

  input.addEventListener("input", () => {
    const q = input.value.trim();
    currentQuery = q;
    if (debounceHandle) clearTimeout(debounceHandle);
    if (!q) {
      const panel = document.getElementById("search-results");
      panel.hidden = true;
      panel.innerHTML = "";
      return;
    }
    debounceHandle = setTimeout(() => runSearch(q), 300);
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      closeSearch();
    } else if (e.key === "Enter" && lastResults.length) {
      e.preventDefault();
      const top = lastResults[0];
      openResult(top.id, top.event_id);
    }
  });

  document.addEventListener("click", (e) => {
    const searchEl = document.getElementById("header-search");
    if (!searchEl) return;
    if (searchEl.contains(e.target)) return;
    const panel = document.getElementById("search-results");
    if (panel && !panel.hidden) closeSearch();
  });
}
