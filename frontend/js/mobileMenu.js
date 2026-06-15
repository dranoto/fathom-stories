// frontend/js/mobileMenu.js
import { runFetch, runRegroup, runGrouping, stats, listFeeds, addFeed, removeFeed, pauseFeed, unpauseFeed, refreshFeed } from "./apiService.js";
import { getInboxCounts, getEvents, setEvents } from "./state.js";
import { loadTheme, toggleTheme, currentTheme } from "./theme.js";
import { getFontSize, setFontSize } from "./fontSize.js";
import { getPwaInstallState, installPwa } from "./pwa.js";
import { clearRuntimeCache } from "./swBridge.js";
import { escapeHtml, renderEventTabs } from "./eventTabs.js";

let isOpen = false;
let onRunAfterRefresh = null;
let cachedStats = null;
let cachedFeeds = null;
let panelEl = null;

async function refreshCachedStats() {
  try { cachedStats = await stats(); } catch (_) { cachedStats = null; }
}

async function refreshCachedFeeds() {
  try { cachedFeeds = await listFeeds(); } catch (_) { cachedFeeds = null; }
}

function isMobileWidth() {
  return window.matchMedia("(max-width: 640px)").matches;
}

function buildMenuBody() {
  const inbox = getInboxCounts();
  const totalArticles = cachedStats ? cachedStats.articles_total : "—";
  const active = cachedStats ? cachedStats.events_active : "—";
  const cooling = cachedStats && cachedStats.events_cooling > 0 ? ` · ${cachedStats.events_cooling} cooling` : "";
  const statsLine = `${totalArticles} articles · ${inbox.unread} in inbox · ${active} active${cooling}`;

  const pwa = getPwaInstallState();
  const installBtn = pwa.canInstall
    ? `<button class="mobile-menu-item" data-action="install">
         <span class="item-icon">⬇</span>
         <span class="item-text">Install app</span>
       </button>`
    : "";

  const feedsHtml = (cachedFeeds || []).map(f => {
    const status = f.is_paused ? "paused" : (f.last_error ? "error" : "ok");
    const statusLabel = f.is_paused ? "Paused" : (f.last_error ? "Error" : "Active");
    const lastFetched = f.last_fetched_at
      ? new Date(f.last_fetched_at).toLocaleString()
      : "never";
    return `<div class="feed-row" data-feed-id="${f.id}">
      <div class="feed-row-main">
        <div class="feed-row-name">${escapeHtml(f.name || f.url)}</div>
        <div class="feed-row-url">${escapeHtml(f.url)}</div>
        <div class="feed-row-meta">
          <span class="feed-row-status feed-row-status-${status}">${statusLabel}</span>
          <span>${f.article_count} articles</span>
          <span>last ${escapeHtml(lastFetched)}</span>
        </div>
      </div>
      <div class="feed-row-actions">
        <button class="feed-action" data-feed-action="refresh" data-feed-id="${f.id}" title="Refresh now">↻</button>
        <button class="feed-action" data-feed-action="pause" data-feed-id="${f.id}" title="${f.is_paused ? "Unpause" : "Pause"}">${f.is_paused ? "▶" : "⏸"}</button>
        <button class="feed-action feed-action-danger" data-feed-action="remove" data-feed-id="${f.id}" title="Remove">×</button>
      </div>
    </div>`;
  }).join("");

  const feedsBlock = `
    <div class="menu-section">
      <div class="menu-section-header">
        <span class="menu-section-title">Feeds</span>
        <button class="menu-section-action" data-action="add-feed">+ Add feed</button>
      </div>
      <div class="menu-section-body">
        ${feedsHtml || `<div class="menu-section-empty">No feeds configured</div>`}
      </div>
    </div>
  `;

  const theme = currentTheme();
  const fontSize = getFontSize();
  const themeLabel = theme === "light" ? "☀ Light" : "☾ Dark";
  const sizeBtns = [
    { level: "small", size: 1, label: "A" },
    { level: "medium", size: 2, label: "A" },
    { level: "large", size: 3, label: "A" },
  ].map(b => `<button class="menu-appearance-btn${b.level === fontSize ? " active" : ""}" data-action="font-size" data-font-size="${b.level}" data-size="${b.size}" title="${b.level} text">${b.label}</button>`).join("");

  return `
    <div class="menu-section">
      <div class="menu-section-header">
        <span class="menu-section-title">Status</span>
      </div>
      <div class="menu-section-body">
        <div class="menu-stats">${escapeHtml(statsLine)}</div>
      </div>
    </div>
    <div class="menu-section">
      <div class="menu-section-header">
        <span class="menu-section-title">Appearance</span>
      </div>
      <div class="menu-section-body">
        <div class="menu-appearance-row">
          <span class="menu-appearance-label">Theme</span>
          <button class="menu-appearance-theme" data-action="theme">${themeLabel}</button>
        </div>
        <div class="menu-appearance-row">
          <span class="menu-appearance-label">Text size</span>
          <div class="menu-appearance-options">${sizeBtns}</div>
        </div>
      </div>
    </div>
    <div class="menu-section">
      <div class="menu-section-header">
        <span class="menu-section-title">Actions</span>
      </div>
      <div class="menu-section-body">
        ${installBtn}
        <button class="mobile-menu-item" data-action="refresh">
          <span class="item-icon">↻</span>
          <span class="item-text">Refresh feeds now</span>
        </button>
        <button class="mobile-menu-item" data-action="hard-refresh">
          <span class="item-icon">⤓</span>
          <span class="item-text">Hard refresh (clear cache)</span>
        </button>
        <button class="mobile-menu-item" data-action="regroup">
          <span class="item-icon">⇆</span>
          <span class="item-text">Regroup now</span>
        </button>
      </div>
    </div>
    ${feedsBlock}
  `;
}

function openMenu() {
  isOpen = true;
  if (isMobileWidth()) {
    const sheet = document.getElementById("mobile-menu");
    if (sheet) sheet.hidden = false;
    renderSheetBody();
  } else {
    ensureDesktopPanel();
    if (panelEl) {
      panelEl.hidden = false;
      renderPanelBody();
    }
  }
}

function closeMenu() {
  isOpen = false;
  const sheet = document.getElementById("mobile-menu");
  if (sheet) sheet.hidden = true;
  if (panelEl) panelEl.hidden = true;
}

function renderSheetBody() {
  const body = document.getElementById("mobile-menu-body");
  if (!body) return;
  body.innerHTML = buildMenuBody();
  wireActions(body);
}

function ensureDesktopPanel() {
  if (panelEl && document.body.contains(panelEl)) return;
  panelEl = document.createElement("div");
  panelEl.id = "desktop-menu-panel";
  panelEl.className = "desktop-menu-panel";
  panelEl.hidden = true;
  document.body.appendChild(panelEl);
  document.addEventListener("click", (e) => {
    if (!isOpen) return;
    if (panelEl.contains(e.target)) return;
    const btn = document.getElementById("btn-menu");
    if (btn && btn.contains(e.target)) return;
    closeMenu();
  });
}

function renderPanelBody() {
  if (!panelEl) return;
  panelEl.innerHTML = buildMenuBody();
  wireActions(panelEl);
}

function wireActions(root) {
  root.querySelectorAll("button[data-feed-action]").forEach(btn => {
    btn.addEventListener("click", async (e) => {
      e.stopPropagation();
      const action = btn.dataset.feedAction;
      const id = parseInt(btn.dataset.feedId, 10);
      if (action === "refresh") {
        btn.disabled = true;
        try {
          await refreshFeed(id);
          await refreshCachedFeeds();
          rerender();
        } catch (err) {
          alert("Refresh failed: " + err.message);
        } finally {
          btn.disabled = false;
        }
      } else if (action === "pause") {
        try {
          const current = (cachedFeeds || []).find(f => f.id === id);
          if (current && current.is_paused) {
            await unpauseFeed(id);
          } else {
            await pauseFeed(id);
          }
          await refreshCachedFeeds();
          rerender();
        } catch (err) {
          alert("Pause failed: " + err.message);
        }
      } else if (action === "remove") {
        if (!confirm("Remove this feed? Articles already fetched are kept.")) return;
        try {
          await removeFeed(id);
          await refreshCachedFeeds();
          rerender();
        } catch (err) {
          alert("Remove failed: " + err.message);
        }
      }
    });
  });
  root.querySelectorAll("button[data-action]").forEach(btn => {
    btn.addEventListener("click", async () => {
      const action = btn.dataset.action;
      if (action === "add-feed") {
        const url = prompt("Feed URL:");
        if (!url) return;
        try {
          await addFeed({ url });
          await refreshCachedFeeds();
          rerender();
        } catch (err) {
          alert("Add feed failed: " + err.message);
        }
        return;
      }
      if (action === "refresh") {
        closeMenu();
        try {
          const r = await runFetch();
          if (r && r.new_articles > 0) {
            try { await runGrouping(); } catch (_) {}
          }
          window.dispatchEvent(new CustomEvent("article-moved"));
        } catch (e) { alert("Refresh failed: " + e.message); }
      } else if (action === "hard-refresh") {
        closeMenu();
        try {
          await clearRuntimeCache();
          const r = await runFetch();
          if (r && r.new_articles > 0) {
            try { await runGrouping(); } catch (_) {}
          }
          window.dispatchEvent(new CustomEvent("article-moved"));
        } catch (e) { alert("Hard refresh failed: " + e.message); }
      } else if (action === "regroup") {
        closeMenu();
        try {
          await runRegroup();
          window.dispatchEvent(new CustomEvent("article-moved"));
        } catch (e) { alert("Regroup failed: " + e.message); }
      } else if (action === "theme") {
        toggleTheme();
        rerender();
      } else if (action === "font-size") {
        const level = btn.dataset.fontSize;
        if (level) {
          setFontSize(level);
          rerender();
        }
      } else if (action === "install") {
        installPwa();
      }
    });
  });
}

function rerender() {
  if (!isOpen) return;
  if (isMobileWidth()) renderSheetBody();
  else renderPanelBody();
}

export function setupMobileMenu(onAfterRefresh) {
  onRunAfterRefresh = onAfterRefresh;
  const btn = document.getElementById("btn-menu");
  const close = document.getElementById("btn-menu-close");
  const backdrop = document.getElementById("mobile-menu-backdrop");
  if (btn) btn.addEventListener("click", async () => {
    await Promise.all([refreshCachedStats(), refreshCachedFeeds()]);
    openMenu();
  });
  if (close) close.addEventListener("click", () => closeMenu());
  if (backdrop) backdrop.addEventListener("click", () => closeMenu());
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && isOpen) closeMenu();
  });
  document.addEventListener("mobile-menu-refresh-chips", () => {
    if (!isOpen) return;
    if (isMobileWidth()) renderSheetBody();
    else renderPanelBody();
  });
  window.addEventListener("theme-changed", () => {
    if (!isOpen) return;
    if (isMobileWidth()) renderSheetBody();
    else renderPanelBody();
  });
  window.addEventListener("font-size-changed", () => {
    if (!isOpen) return;
    if (isMobileWidth()) renderSheetBody();
    else renderPanelBody();
  });
}

export async function renderMobileMenu() {
  await Promise.all([refreshCachedStats(), refreshCachedFeeds()]);
  if (isOpen) {
    if (isMobileWidth()) renderSheetBody();
    else renderPanelBody();
  }
}
