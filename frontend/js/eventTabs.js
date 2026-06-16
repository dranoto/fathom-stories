// frontend/js/eventTabs.js
import {
  getEvents,
  setActiveEventId,
  getActiveEventId,
  getInboxOpen,
  getInboxCounts,
  getMinorDrawerOpen,
  setMinorDrawerOpen,
  getSortMode,
  getScoreKnobs,
  getSeenEventIds,
} from "./state.js";
import { sortEventsByScore } from "./score.js";
import { isDesktopLayout } from "./layout.js";

const INBOX_ID = "__inbox__";

const CARD_WIDTH = 180;
const GAP_WIDTH = 8;
const CONTAINER_PADDING = 24;

function _ts(v) {
  if (!v) return 0;
  const t = new Date(v).getTime();
  return Number.isFinite(t) ? t : 0;
}

function _getViewportWidth() {
  if (typeof window === "undefined") return 1200;
  return window.innerWidth || document.documentElement.clientWidth || 1200;
}

function _getBarWidth() {
  if (typeof document === "undefined") return 1200;
  const bar = document.querySelector(".event-bar-row");
  if (bar) {
    const w = bar.clientWidth || bar.getBoundingClientRect().width;
    if (w > 0) return w;
  }
  const container = document.getElementById("event-tabs");
  if (container) {
    const w = container.clientWidth || container.getBoundingClientRect().width;
    if (w > 0) return w;
  }
  return _getViewportWidth();
}

function _computeTopN(events, viewportWidth) {
  if (events.length <= 3) return events.length;
  const TOTAL_PER_TOP = CARD_WIDTH + GAP_WIDTH;
  const fixed = 2 * CARD_WIDTH + GAP_WIDTH;
  const available = viewportWidth - fixed;
  const maxN = Math.max(0, Math.floor(available / TOTAL_PER_TOP));
  return Math.min(maxN, events.length - 1);
}

export function sortEventsForBar(events) {
  if (getSortMode() === "score") {
    return sortEventsByScore(events || [], getScoreKnobs());
  }
  return [...(events || [])].sort((a, b) => {
    const ac = a.article_count || 0;
    const bc = b.article_count || 0;
    if (bc !== ac) return bc - ac;
    return _ts(b.last_article_at) - _ts(a.last_article_at);
  });
}

export function partitionEvents(events, viewportWidth) {
  const vw = viewportWidth ?? _getBarWidth();
  if (!events || !events.length) {
    return { topN: [], minor: [] };
  }
  const sorted = sortEventsForBar(events);
  const N = _computeTopN(events, vw);
  const topN = sorted.slice(0, N);
  const shown = new Set(topN.map((e) => e.id));
  const minor = sorted.filter((e) => !shown.has(e.id));
  return { topN, minor };
}

export function buildNavOrder(events, viewportWidth) {
  const { topN, minor } = partitionEvents(events, viewportWidth);
  return [
    { kind: "inbox" },
    ...topN.map((e) => ({ kind: "event", id: e.id })),
    ...minor.map((e) => ({ kind: "event", id: e.id })),
  ];
}

function _cardMarkup(e, activeId, inboxOpen, group) {
  const cls = [
    "event-tab",
    e.id === activeId && !inboxOpen ? "active" : "",
  ].filter(Boolean).join(" ");
  const total = e.article_count || 0;
  const unread = e.unread_count || 0;
  const allRead = total > 0 && unread === 0;
  const newCount = e.new_since_visit || 0;
  const seen = getSeenEventIds();
  const isNew = !seen.has(e.id);
  const metaLine = total > 0
    ? `${total} article${total === 1 ? "" : "s"}${allRead ? " · all read" : ` · ${unread} unread`}`
    : "";
  const unreadDot = unread > 0
    ? `<span class="unread-dot" title="${unread} unread"></span>`
    : "";
  const newBadge = newCount > 0
    ? `<span class="new-badge" title="${newCount} new since last visit">+${newCount > 99 ? "99+" : newCount}</span>`
    : "";
  const newTag = isNew
    ? `<span class="new-tag" title="New event — click to clear">New</span>`
    : "";
  const statusIcon = e.status === "cooling"
    ? `<span class="status-icon">❄</span>`
    : "";
  const groupAttr = group ? ` data-group="${group}"` : "";
  return `<div class="${cls}" data-event-id="${e.id}"${groupAttr} title="${escapeHtml(e.name)}">
    ${newTag}
    ${unreadDot}${newBadge}
    <div class="name two-line">${statusIcon}${escapeHtml(e.name)}</div>
    ${metaLine ? `<div class="meta">${metaLine}</div>` : ""}
  </div>`;
}

function _inboxMarkup(activeId, inboxOpen, inboxN, inboxU) {
  const cls = inboxOpen ? "active" : "";
  const meta = inboxN > 0
    ? `${inboxN} ungrouped${inboxU > 0 ? ` · ${inboxU} unread` : ""}`
    : "no articles";
  const unreadDot = inboxU > 0
    ? `<span class="unread-dot" title="${inboxU} unread"></span>`
    : "";
  return `<div class="event-tab ${cls}" data-inbox="1" title="Ungrouped articles">
    ${unreadDot}
    <div class="name two-line">Inbox</div>
    <div class="meta">${meta}</div>
  </div>`;
}

function _minorToggleMarkup(totalCount, drawerOpen, activeId, inboxOpen, drawerEventIds) {
  const chev = drawerOpen ? "▴" : "▾";
  const isDesk = isDesktopLayout();
  const label = isDesk
    ? `${totalCount} Minor Event${totalCount === 1 ? "" : "s"}`
    : `${totalCount} Event${totalCount === 1 ? "" : "s"}`;
  const activeInDrawer = !drawerOpen && !inboxOpen && activeId != null &&
    drawerEventIds.has(activeId);
  const cls = ["event-tab", "minor-toggle", activeInDrawer ? "active" : ""]
    .filter(Boolean)
    .join(" ");
  return `<div class="${cls}" data-minor-toggle="1" data-group="drawer" role="button" aria-expanded="${drawerOpen ? "true" : "false"}" title="Show ${totalCount} event${totalCount === 1 ? "" : "s"}">
    <div class="name two-line"><span>${label}</span><span class="minor-toggle-chev">${chev}</span></div>
    <div class="meta">click to ${drawerOpen ? "hide" : "show"}</div>
  </div>`;
}

let _drawerEl = null;

function _ensureDrawerEl() {
  if (_drawerEl && document.body && document.body.contains(_drawerEl)) return _drawerEl;
  _drawerEl = document.createElement("div");
  _drawerEl.className = "minor-drawer";
  document.body.appendChild(_drawerEl);
  return _drawerEl;
}

function _removeDrawerEl() {
  if (_drawerEl && _drawerEl.parentNode) {
    _drawerEl.parentNode.removeChild(_drawerEl);
  }
  _drawerEl = null;
}

function _positionDrawer() {
  if (!_drawerEl) return;
  const toggle = document.querySelector(".event-tab[data-minor-toggle]");
  if (!toggle) return;
  const rect = toggle.getBoundingClientRect();
  const drawerWidth = 192;
  const gap = 6;
  let left = rect.right - drawerWidth;
  if (left < 8) left = 8;
  if (left + drawerWidth > window.innerWidth - 8) {
    left = window.innerWidth - drawerWidth - 8;
  }
  _drawerEl.style.position = "fixed";
  _drawerEl.style.top = (rect.bottom + gap) + "px";
  _drawerEl.style.left = left + "px";
  _drawerEl.style.width = drawerWidth + "px";
}

function _renderDrawer(events, drawerOpen) {
  if (!events || events.length === 0) {
    _removeDrawerEl();
    return;
  }
  const el = _ensureDrawerEl();
  el.setAttribute("data-open", drawerOpen ? "1" : "0");
  el.setAttribute("aria-hidden", drawerOpen ? "false" : "true");
  const cardsHtml = events
    .map((e) => _cardMarkup(e, getActiveEventId(), false, "drawer-item"))
    .join("");
  el.innerHTML = cardsHtml;
  el.querySelectorAll(".event-tab[data-event-id]").forEach((card) => {
    card.addEventListener("click", (e) => {
      e.stopPropagation();
      const id = parseInt(card.dataset.eventId, 10);
      setActiveEventId(id);
      if (_lastCallbacks && typeof _lastCallbacks.onSelectEvent === "function") {
        _lastCallbacks.onSelectEvent(id);
      }
    });
  });
  _positionDrawer();
}

let _lastCallbacks = null;

window.addEventListener("minor-drawer-toggled", () => {
  if (_lastCallbacks) {
    renderEventTabs(
      _lastCallbacks.onSelectEvent,
      _lastCallbacks.onSelectInbox,
      _lastCallbacks.onToggleMinor
    );
  }
});

export function renderEventTabs(onSelectEvent, onSelectInbox, onToggleMinor) {
  _lastCallbacks = { onSelectEvent, onSelectInbox, onToggleMinor };
  const container = document.getElementById("event-tabs");
  if (!container) return;
  const events = getEvents();
  const activeId = getActiveEventId();
  const inboxOpen = getInboxOpen();
  const drawerOpen = getMinorDrawerOpen();
  const { total: inboxN, read: inboxR, unread: inboxU } = getInboxCounts();

  if (!events.length && inboxN === 0) {
    container.innerHTML = `<div class="tabs-empty">No events yet — fetch and regroup some articles.</div>`;
    _removeDrawerEl();
    return;
  }

  const { topN, minor } = partitionEvents(events);
  const isDesk = isDesktopLayout();

  const parts = [];
  parts.push('<div class="event-bar-row">');
  parts.push(_inboxMarkup(activeId, inboxOpen, inboxN, inboxU));
  if (isDesk) {
    for (let i = 0; i < topN.length; i++) {
      const group = i === 0 ? "top-start" : "top";
      parts.push(_cardMarkup(topN[i], activeId, inboxOpen, group));
    }
  }
  const totalCount = isDesk ? minor.length : topN.length + minor.length;
  const showToggle = isDesk ? minor.length > 0 : events.length > 0;
  if (showToggle) {
    const drawerEventIds = new Set(minor.map((e) => e.id));
    parts.push(_minorToggleMarkup(totalCount, drawerOpen, activeId, inboxOpen, drawerEventIds));
  }
  parts.push("</div>");

  container.innerHTML = parts.join("");

  container.querySelectorAll(".event-tab[data-event-id]").forEach((el) => {
    el.addEventListener("click", () => {
      const id = parseInt(el.dataset.eventId, 10);
      setActiveEventId(id);
      onSelectEvent(id);
    });
  });
  container.querySelectorAll(".event-tab[data-inbox]").forEach((el) => {
    el.addEventListener("click", () => onSelectInbox());
  });
  container.querySelectorAll(".event-tab[data-minor-toggle]").forEach((el) => {
    el.addEventListener("click", () => {
      if (typeof onToggleMinor === "function") {
        onToggleMinor();
      } else {
        setMinorDrawerOpen(!getMinorDrawerOpen());
        renderEventTabs(onSelectEvent, onSelectInbox, onToggleMinor);
      }
    });
  });

  const drawerEvents = isDesk ? minor : [...topN, ...minor];
  _renderDrawer(drawerEvents, drawerOpen);
}

export { INBOX_ID };
export function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

let wheelHandlerAttached = false;
let resizeDebounceTimer = null;

export function setupEventTabs() {
  if (wheelHandlerAttached) return;
  const tabs = document.getElementById("event-tabs");
  if (!tabs) return;
  wheelHandlerAttached = true;

  const barRow = tabs.querySelector(".event-bar-row") || tabs;
  barRow.addEventListener(
    "wheel",
    (e) => {
      if (e.deltaY === 0 && e.deltaX === 0) return;
      e.preventDefault();
      barRow.scrollLeft += e.deltaY + e.deltaX;
    },
    { passive: false }
  );

  window.addEventListener("resize", () => {
    if (resizeDebounceTimer) clearTimeout(resizeDebounceTimer);
    resizeDebounceTimer = setTimeout(() => {
      resizeDebounceTimer = null;
      if (_lastCallbacks) {
        renderEventTabs(
          _lastCallbacks.onSelectEvent,
          _lastCallbacks.onSelectInbox,
          _lastCallbacks.onToggleMinor
        );
      }
      if (getMinorDrawerOpen()) _positionDrawer();
    }, 120);
  });

  let scrollFrame = null;
  window.addEventListener("scroll", () => {
    if (!getMinorDrawerOpen()) return;
    if (scrollFrame) return;
    scrollFrame = requestAnimationFrame(() => {
      scrollFrame = null;
      _positionDrawer();
    });
  }, { passive: true });
}
