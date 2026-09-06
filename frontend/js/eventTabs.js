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

const NEW_STRIP_MAX_AGE_HOURS = 24;

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

export function partitionNewAndUpdated(events) {
  if (!events || !events.length) return { fresh: [], rest: [] };
  const seen = getSeenEventIds();
  const now = Date.now();
  const fresh = [];
  const rest = [];
  for (const e of events) {
    const newSinceVisit = Number(e && e.new_since_visit) || 0;
    const createdAt = _ts(e && e.created_at);
    const ageHours = createdAt > 0 ? (now - createdAt) / 3600000 : Infinity;
    const isNeverSeen = !seen.has(e.id);
    const isFreshByAge = ageHours <= NEW_STRIP_MAX_AGE_HOURS;
    const isNew = newSinceVisit > 0 || (isNeverSeen && isFreshByAge);
    if (isNew) fresh.push(e);
    else rest.push(e);
  }
  fresh.sort((a, b) => {
    const na = Number(a.new_since_visit) || 0;
    const nb = Number(b.new_since_visit) || 0;
    if (nb !== na) return nb - na;
    const ca = _ts(a.created_at);
    const cb = _ts(b.created_at);
    if (cb !== ca) return cb - ca;
    return _ts(b.last_article_at) - _ts(a.last_article_at);
  });
  return { fresh, rest };
}

export function partitionEvents(events, viewportWidth) {
  const vw = viewportWidth ?? _getBarWidth();
  if (!events || !events.length) {
    return { topN: [], minor: [] };
  }
  const { rest } = partitionNewAndUpdated(events);
  const sorted = sortEventsForBar(rest);
  const reservedCards = rest.length > 0 ? 2 : 1;
  const visibleCapacity = Math.max(
    0,
    Math.floor((vw - (reservedCards * CARD_WIDTH) - GAP_WIDTH) / (CARD_WIDTH + GAP_WIDTH)),
  );
  const N = Math.min(rest.length, visibleCapacity);
  const topN = sorted.slice(0, N);
  const shown = new Set(topN.map((e) => e.id));
  const minor = sorted.filter((e) => !shown.has(e.id));
  return { topN, minor };
}

export function buildNavOrder(events, viewportWidth) {
  const { fresh } = partitionNewAndUpdated(events || []);
  const { topN, minor } = partitionEvents(events || [], viewportWidth);
  return [
    { kind: "inbox" },
    ...fresh.map((e) => ({ kind: "event", id: e.id })),
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
  const sourceLine = e.publisher_label
    ? `<div class="sources">${escapeHtml(e.publisher_label)}</div>`
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
    ${sourceLine}
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

function _minorToggleMarkup(events, drawerOpen, activeId, inboxOpen) {
  const totalCount = events.length;
  const unreadCount = events.reduce((sum, event) => sum + (Number(event && event.unread_count) || 0), 0);
  const updatedCount = events.reduce((sum, event) => sum + (Number(event && event.new_since_visit) || 0), 0);
  const drawerEventIds = new Set(
    events
      .map((event) => event && event.id)
      .filter((id) => Number.isInteger(id))
  );
  const chev = drawerOpen ? "▴" : "▾";
  const isDesk = isDesktopLayout();
  const label = isDesk
    ? `${totalCount} More ${totalCount === 1 ? "Story" : "Stories"}`
    : `${totalCount} ${totalCount === 1 ? "Story" : "Stories"}`;
  const activityParts = [];
  if (updatedCount > 0) activityParts.push(`${updatedCount} new`);
  if (unreadCount > 0) activityParts.push(`${unreadCount} unread`);
  const activity = activityParts.length > 0
    ? activityParts.join(" · ")
    : drawerOpen ? "click to hide" : "click to show";
  const activeInDrawer = !drawerOpen && !inboxOpen && activeId != null &&
    drawerEventIds.has(activeId);
  const cls = ["event-tab", "minor-toggle", activeInDrawer ? "active" : ""]
    .filter(Boolean)
    .join(" ");
  const action = drawerOpen ? "Hide" : "Show";
  const title = `${action} ${totalCount} ${totalCount === 1 ? "story" : "stories"}${unreadCount > 0 ? ` with ${unreadCount} unread article${unreadCount === 1 ? "" : "s"}` : ""}`;
  return `<div class="${cls}" data-minor-toggle="1" data-group="drawer" role="button" tabindex="0" aria-expanded="${drawerOpen ? "true" : "false"}" aria-controls="minor-drawer" title="${title}">
    <div class="name two-line"><span>${label}</span><span class="minor-toggle-chev">${chev}</span></div>
    <div class="meta">${activity}</div>
  </div>`;
}

function _newStripHeaderMarkup(count) {
  if (!count) return "";
  return `<div class="new-strip-header" aria-hidden="true">
    <span class="new-strip-label">New &amp; updated</span>
    <span class="new-strip-count">${count}</span>
  </div>`;
}

let _drawerEl = null;

function _ensureDrawerEl() {
  if (_drawerEl && document.body && document.body.contains(_drawerEl)) return _drawerEl;
  _drawerEl = document.createElement("div");
  _drawerEl.id = "minor-drawer";
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
let _resizeTimer = null;

function _rerenderAfterResize() {
  if (_resizeTimer !== null) window.clearTimeout(_resizeTimer);
  _resizeTimer = window.setTimeout(() => {
    _resizeTimer = null;
    if (_lastCallbacks) {
      renderEventTabs(
        _lastCallbacks.onSelectEvent,
        _lastCallbacks.onSelectInbox,
        _lastCallbacks.onToggleMinor,
      );
    }
  }, 120);
}

window.addEventListener("resize", _rerenderAfterResize);
window.addEventListener("orientationchange", _rerenderAfterResize);

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

  const { fresh } = partitionNewAndUpdated(events);
  const { topN, minor } = partitionEvents(events);
  const isDesk = isDesktopLayout();

  const parts = [];

  if (fresh.length > 0) {
    parts.push('<div class="new-strip">');
    parts.push(_newStripHeaderMarkup(fresh.length));
    parts.push('<div class="new-strip-row">');
    for (const e of fresh) {
      parts.push(_cardMarkup(e, activeId, inboxOpen, "new-strip-item"));
    }
    parts.push("</div></div>");
  }

  parts.push('<div class="event-bar-row">');
  parts.push(_inboxMarkup(activeId, inboxOpen, inboxN, inboxU));
  if (isDesk) {
    for (let i = 0; i < topN.length; i++) {
      const group = i === 0 ? "top-start" : "top";
      parts.push(_cardMarkup(topN[i], activeId, inboxOpen, group));
    }
  }
  const drawerEvents = isDesk ? minor : [...topN, ...minor];
  if (drawerEvents.length > 0) {
    parts.push(_minorToggleMarkup(drawerEvents, drawerOpen, activeId, inboxOpen));
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
    const toggleDrawer = () => {
      if (typeof onToggleMinor === "function") {
        onToggleMinor();
      } else {
        setMinorDrawerOpen(!getMinorDrawerOpen());
        renderEventTabs(onSelectEvent, onSelectInbox, onToggleMinor);
      }
    };
    el.addEventListener("click", toggleDrawer);
    el.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      toggleDrawer();
    });
  });

  _renderDrawer(drawerEvents, drawerOpen);
}

export { INBOX_ID, _minorToggleMarkup, _cardMarkup };
export function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
