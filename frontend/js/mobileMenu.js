// frontend/js/mobileMenu.js
import { runFetch, runRegroup, runGrouping, stats } from "./apiService.js";
import { getInboxCounts, getEvents } from "./state.js";
import { loadTheme, toggleTheme } from "./theme.js";
import { getPwaInstallState, installPwa } from "./pwa.js";
import { clearRuntimeCache } from "./swBridge.js";
import { escapeHtml } from "./eventTabs.js";

let isOpen = false;
let onRunAfterRefresh = null;
let cachedStats = null;
let panelEl = null;

async function refreshCachedStats() {
  try { cachedStats = await stats(); } catch (_) { cachedStats = null; }
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
        <button class="mobile-menu-item" data-action="theme">
          <span class="item-icon">◐</span>
          <span class="item-text">Toggle light/dark</span>
        </button>
      </div>
    </div>
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
  root.querySelectorAll("button[data-action]").forEach(btn => {
    btn.addEventListener("click", async () => {
      const action = btn.dataset.action;
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
        closeMenu();
      } else if (action === "install") {
        installPwa();
      }
    });
  });
}

export function setupMobileMenu(onAfterRefresh) {
  onRunAfterRefresh = onAfterRefresh;
  const btn = document.getElementById("btn-menu");
  const close = document.getElementById("btn-menu-close");
  const backdrop = document.getElementById("mobile-menu-backdrop");
  if (btn) btn.addEventListener("click", async () => {
    await refreshCachedStats();
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
}

export async function renderMobileMenu() {
  await refreshCachedStats();
  if (isOpen) {
    if (isMobileWidth()) renderSheetBody();
    else renderPanelBody();
  }
}
