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

function _ts(v) {
  if (!v) return 0;
  const t = new Date(v).getTime();
  return Number.isFinite(t) ? t : 0;
}

export function partitionEvents(events) {
  if (!events || !events.length) {
    return { top3: [], mostRecent: null, minor: [] };
  }
  const sortedByCount = [...events].sort((a, b) => {
    const ac = a.article_count || 0;
    const bc = b.article_count || 0;
    if (bc !== ac) return bc - ac;
    return _ts(b.last_article_at) - _ts(a.last_article_at);
  });
  if (events.length <= 3) {
    return { top3: sortedByCount.slice(0, 3), mostRecent: null, minor: [] };
  }
  const mostRecent = events.reduce((acc, e) => (_ts(e.created_at) > _ts(acc.created_at) ? e : acc), events[0]);
  const top3 = [];
  for (const e of sortedByCount) {
    if (e.id === mostRecent.id) continue;
    top3.push(e);
    if (top3.length === 3) break;
  }
  const shown = new Set([...top3.map((e) => e.id), mostRecent.id]);
  const minor = events.filter((e) => !shown.has(e.id));
  return { top3, mostRecent, minor };
}

function _cardMarkup(e, activeId, inboxOpen) {
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
  return `<div class="${cls}" data-event-id="${e.id}" title="${escapeHtml(e.name)}">
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
  return `<div class="${cls}" data-event-id="${e.id}" title="${escapeHtml(e.name)}">
    <span class="new-tag" title="Newest event by creation date">New</span>
    ${unreadDot}${newBadge}
    <div class="name two-line">${escapeHtml(e.name)}</div>
    ${metaLine ? `<div class="meta">${metaLine}</div>` : ""}
  </div>`;
}

function _minorToggleMarkup(minor, drawerOpen) {
  const chev = drawerOpen ? "▴" : "▾";
  return `<div class="event-tab minor-toggle" data-minor-toggle="1" role="button" aria-expanded="${drawerOpen ? "true" : "false"}" title="Show ${minor.length} more event${minor.length === 1 ? "" : "s"}">
    <div class="name two-line"><span>${minor.length} Minor Event${minor.length === 1 ? "" : "s"}</span><span class="minor-toggle-chev">${chev}</span></div>
    <div class="meta">click to ${drawerOpen ? "hide" : "show"}</div>
  </div>`;
}

function _drawerMarkup(minor, drawerOpen) {
  const cards = minor.map((e) => _cardMarkup(e, getActiveEventId(), false)).join("");
  return `<div class="minor-drawer" data-open="${drawerOpen ? "1" : "0"}" aria-hidden="${drawerOpen ? "false" : "true"}">${cards}</div>`;
}

export function renderEventTabs(onSelectEvent, onSelectInbox) {
  const container = document.getElementById("event-tabs");
  if (!container) return;
  const events = getEvents();
  const activeId = getActiveEventId();
  const inboxOpen = getInboxOpen();
  const drawerOpen = getMinorDrawerOpen();
  const { total: inboxN, read: inboxR, unread: inboxU } = getInboxCounts();

  if (!events.length && inboxN === 0) {
    container.innerHTML = `<div class="tabs-empty">No events yet — fetch and regroup some articles.</div>`;
    return;
  }

  const { top3, mostRecent, minor } = partitionEvents(events);

  const parts = [];
  parts.push('<div class="event-bar-row">');
  parts.push(_inboxMarkup(activeId, inboxOpen, inboxN, inboxU));
  for (const e of top3) {
    parts.push(_cardMarkup(e, activeId, inboxOpen));
  }
  if (mostRecent) {
    parts.push(_mostRecentMarkup(mostRecent, activeId, inboxOpen));
  }
  if (minor.length > 0) {
    parts.push(_minorToggleMarkup(minor, drawerOpen));
  }
  parts.push("</div>");
  if (minor.length > 0) {
    parts.push(_drawerMarkup(minor, drawerOpen));
  }

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
      setMinorDrawerOpen(!getMinorDrawerOpen());
    });
  });
}

export { INBOX_ID };
export function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

let wheelHandlerAttached = false;

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
}
