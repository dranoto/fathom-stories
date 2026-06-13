// frontend/js/mobileMenu.js
import { runFetch, runRegroup, stats } from "./apiService.js";
import { getInboxCounts, getEvents } from "./state.js";
import { loadTheme, toggleTheme } from "./theme.js";
import { getPwaInstallState, installPwa } from "./pwa.js";

let isOpen = false;
let onRunAfterRefresh = null;
let cachedStats = null;

async function refreshCachedStats() {
  try { cachedStats = await stats(); } catch (_) { cachedStats = null; }
}

function setOpen(open) {
  isOpen = open;
  const menu = document.getElementById("mobile-menu");
  if (!menu) return;
  menu.hidden = !open;
  if (open) renderBody();
}

function renderBody() {
  const body = document.getElementById("mobile-menu-body");
  if (!body) return;
  const inbox = getInboxCounts();
  const events = getEvents() || [];
  const totalArticles = cachedStats ? cachedStats.articles_total : "—";
  const active = cachedStats ? cachedStats.events_active : "—";
  const cooling = cachedStats && cachedStats.events_cooling > 0 ? ` · ${cachedStats.events_cooling} cooling` : "";
  const statsLine = `${totalArticles} articles · ${inbox.unread} in inbox · ${active} active${cooling}`;

  const pwa = getPwaInstallState();
  const installBtn = pwa.canInstall
    ? `<button class="mobile-menu-install" data-action="install">
         <span class="item-icon">⬇</span>
         <span class="item-text">Install app</span>
       </button>`
    : "";

  body.innerHTML = `
    <div style="padding: 8px 18px; color: var(--text-dim); font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em;">Status</div>
    <div style="padding: 4px 18px 12px; color: var(--text-dim); font-size: 12px;">${escapeHtml(statsLine)}</div>
    ${installBtn}
    <button class="mobile-menu-item" data-action="refresh">
      <span class="item-icon">↻</span>
      <span class="item-text">Refresh feeds now</span>
      <span class="item-meta" id="menu-chip-refresh">--:--</span>
    </button>
    <button class="mobile-menu-item" data-action="regroup">
      <span class="item-icon">⇆</span>
      <span class="item-text">Regroup now</span>
      <span class="item-meta" id="menu-chip-regroup">--:--</span>
    </button>
    <button class="mobile-menu-item" data-action="theme">
      <span class="item-icon">◐</span>
      <span class="item-text">Toggle light/dark</span>
    </button>
    <a class="mobile-menu-item" href="http://localhost:8001" target="_blank" rel="noopener">
      <span class="item-icon">⚙</span>
      <span class="item-text">Open admin (desktop)</span>
    </a>
  `;

  body.querySelectorAll("button[data-action]").forEach(btn => {
    btn.addEventListener("click", async () => {
      const action = btn.dataset.action;
      if (action === "refresh") {
        setOpen(false);
        try {
          const r = await runFetch();
          if (r && r.new_articles > 0) {
            try {
              const { runGrouping } = await import("./apiService.js");
              await runGrouping();
            } catch (_) {}
          }
          window.dispatchEvent(new CustomEvent("article-moved"));
        } catch (e) { alert("Refresh failed: " + e.message); }
      } else if (action === "regroup") {
        setOpen(false);
        try {
          await runRegroup();
          window.dispatchEvent(new CustomEvent("article-moved"));
        } catch (e) { alert("Regroup failed: " + e.message); }
      } else if (action === "theme") {
        toggleTheme();
        setOpen(false);
      } else if (action === "install") {
        installPwa();
      }
    });
  });
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

export function setupMobileMenu(onAfterRefresh) {
  onRunAfterRefresh = onAfterRefresh;
  const btn = document.getElementById("btn-menu");
  const close = document.getElementById("btn-menu-close");
  const backdrop = document.getElementById("mobile-menu-backdrop");
  if (btn) btn.addEventListener("click", () => {
    refreshCachedStats();
    setOpen(true);
  });
  if (close) close.addEventListener("click", () => setOpen(false));
  if (backdrop) backdrop.addEventListener("click", () => setOpen(false));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && isOpen) setOpen(false);
  });
  document.addEventListener("mobile-menu-refresh-chips", () => {
    if (isOpen) renderBody();
  });
}

export async function renderMobileMenu({ onRunAfterRefresh: _cb } = {}) {
  await refreshCachedStats();
  if (isOpen) renderBody();
}
