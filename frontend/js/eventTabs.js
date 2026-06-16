// frontend/js/eventTabs.js
import {
  getEvents,
  setActiveEventId,
  getActiveEventId,
  getInboxOpen,
  getInboxCounts,
  getMinorDrawerOpen,
  setMinorDrawerOpen,
} from "./state.js";

const INBOX_ID = "__inbox__";

const CARD_WIDTH = 180;
const GAP_WIDTH = 8;
const CONTAINER_PADDING = 24;
const MANDATORY_CARDS = 3;

function _ts(v) {
  if (!v) return 0;
  const t = new Date(v).getTime();
  return Number.isFinite(t) ? t : 0;
}

function _getViewportWidth() {
  if (typeof window === "undefined") return 1200;
  return window.innerWidth || document.documentElement.clientWidth || 1200;
}

function _computeTopN(events, viewportWidth) {
  if (events.length <= 3) return events.length;
  const totalPerTop = CARD_WIDTH + GAP_WIDTH;
  const fixed = CONTAINER_PADDING + MANDATORY_CARDS * CARD_WIDTH + (MANDATORY_CARDS - 1) * GAP_WIDTH;
  const available = viewportWidth - fixed;
  const maxN = Math.max(0, Math.floor(available / totalPerTop));
  return Math.min(maxN, events.length - 1);
}

export function partitionEvents(events, viewportWidth) {
  const vw = viewportWidth ?? _getViewportWidth();
  if (!events || !events.length) {
    return { topN: [], mostRecent: null, minor: [] };
  }
  const sortedByCount = [...events].sort((a, b) => {
    const ac = a.article_count || 0;
    const bc = b.article_count || 0;
    if (bc !== ac) return bc - ac;
    return _ts(b.last_article_at) - _ts(a.last_article_at);
  });
  if (events.length <= 3) {
    return { topN: sortedByCount.slice(0, 3), mostRecent: null, minor: [] };
  }
  const mostRecent = events.reduce((acc, e) => (_ts(e.created_at) > _ts(acc.created_at) ? e : acc), events[0]);
  const N = _computeTopN(events, vw);
  const topN = [];
  for (const e of sortedByCount) {
    if (e.id === mostRecent.id) continue;
    topN.push(e);
    if (topN.length >= N) break;
  }
  const shown = new Set([...topN.map((e) => e.id), mostRecent.id]);
  const minor = events.filter((e) => !shown.has(e.id));
  return { topN, mostRecent, minor };
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
  const metaLine = total > 0
    ? `${total} article${total === 1 ? "" : "s"}${allRead ? " · all read" : ` · ${unread} unread`}`
    : "";
  const unreadDot = unread > 0
    ? `<span class="unread-dot" title="${unread} unread"></span>`
    : "";
  const newBadge = newCount > 0
    ? `<span class="new-badge" title="${newCount} new since last visit">+${newCount > 99 ? "99+" : newCount}</span>`
    : "";
  const statusIcon = e.status === "cooling"
    ? `<span class="status-icon">❄</span>`
    : "";
  const groupAttr = group ? ` data-group="${group}"` : "";
  return `<div class="${cls}" data-event-id="${e.id}"${groupAttr} title="${escapeHtml(e.name)}">
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

function _mostRecentMarkup(e, activeId, inboxOpen) {
  const cls = [
    "event-tab",
    "most-recent",
    e.id === activeId && !inboxOpen ? "active" : "",
  ].filter(Boolean).join(" ");
  const total = e.article_count || 0;
  const unread = e.unread_count || 0;
  const allRead = total > 0 && unread === 0;
  const newCount = e.new_since_visit || 0;
  const metaLine = total > 0
    ? `${total} article${total === 1 ? "" : "s"}${allRead ? " · all read" : ` · ${unread} unread`}`
    : "";
  const unreadDot = unread > 0
    ? `<span class="unread-dot" title="${unread} unread"></span>`
    : "";
  const newBadge = newCount > 0
    ? `<span class="new-badge" title="${newCount} new since last visit">+${newCount > 99 ? "99+" : newCount}</span>`
    : "";
  return `<div class="${cls}" data-event-id="${e.id}" data-group="most-recent" title="${escapeHtml(e.name)}">
    <span class="new-tag" title="Newest event by creation date">New</span>
    ${unreadDot}${newBadge}
    <div class="name two-line">${escapeHtml(e.name)}</div>
    ${metaLine ? `<div class="meta">${metaLine}</div>` : ""}
  </div>`;
}

function _minorToggleMarkup(minor, drawerOpen) {
  const chev = drawerOpen ? "▴" : "▾";
  return `<div class="event-tab minor-toggle" data-minor-toggle="1" data-group="drawer" role="button" aria-expanded="${drawerOpen ? "true" : "false"}" title="Show ${minor.length} more event${minor.length === 1 ? "" : "s"}">
    <div class="name two-line"><span>${minor.length} Minor Event${minor.length === 1 ? "" : "s"}</span><span class="minor-toggle-chev">${chev}</span></div>
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

function _renderDrawer(minor, drawerOpen) {
  if (minor.length === 0) {
    _removeDrawerEl();
    return;
  }
  const el = _ensureDrawerEl();
  el.setAttribute("data-open", drawerOpen ? "1" : "0");
  el.setAttribute("aria-hidden", drawerOpen ? "false" : "true");
  el.innerHTML = minor.map((e) => _cardMarkup(e, getActiveEventId(), false, "drawer-item")).join("");

  const activeCards = el.querySelectorAll(".event-tab.active");
  if (activeCards.length > 0) {
    console.log("drawer active cards:", Array.from(activeCards).map((c) => c.dataset.eventId));
  }

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

  const { topN, mostRecent, minor } = partitionEvents(events);

  const parts = [];
  parts.push('<div class="event-bar-row">');
  parts.push(_inboxMarkup(activeId, inboxOpen, inboxN, inboxU));
  for (let i = 0; i < topN.length; i++) {
    const group = i === 0 ? "top-start" : "top";
    parts.push(_cardMarkup(topN[i], activeId, inboxOpen, group));
  }
  if (mostRecent) {
    parts.push(_mostRecentMarkup(mostRecent, activeId, inboxOpen));
  }
  if (minor.length > 0) {
    parts.push(_minorToggleMarkup(minor, drawerOpen));
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

  _renderDrawer(minor, drawerOpen);
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
