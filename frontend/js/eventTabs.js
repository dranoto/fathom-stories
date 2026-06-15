// frontend/js/eventTabs.js
import { getEvents, setActiveEventId, getActiveEventId, getInboxOpen, getInboxCounts } from "./state.js";

const INBOX_ID = "__inbox__";

export function renderEventTabs(onSelectEvent, onSelectInbox) {
  const container = document.getElementById("event-tabs");
  const events = getEvents();
  const activeId = getActiveEventId();
  const inboxOpen = getInboxOpen();
  const { total: inboxN, read: inboxR, unread: inboxU } = getInboxCounts();

  if (!events.length && inboxN === 0) {
    container.innerHTML = `<div class="tabs-empty">No events yet — fetch and regroup some articles.</div>`;
    return;
  }

  const tabsHtml = events
    .map(e => {
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
        <div class="name">${statusIcon}${escapeHtml(e.name)}</div>
        ${metaLine ? `<div class="meta">${metaLine}</div>` : ""}
      </div>`;
    })
    .join("");

  const inboxCls = inboxOpen ? "active" : "";
  const inboxMeta = inboxN > 0
    ? `${inboxN} ungrouped${inboxU > 0 ? ` · ${inboxU} unread` : ""}`
    : "no articles";
  const inboxUnreadDot = inboxU > 0
    ? `<span class="unread-dot" title="${inboxU} unread"></span>`
    : "";
  const inboxHtml = `<div class="event-tab ${inboxCls}" data-inbox="1" title="Ungrouped articles">
    ${inboxUnreadDot}
    <div class="name">Inbox</div>
    <div class="meta">${inboxMeta}</div>
  </div>`;

  container.innerHTML = tabsHtml + inboxHtml;

  container.querySelectorAll(".event-tab[data-event-id]").forEach(el => {
    el.addEventListener("click", () => {
      const id = parseInt(el.dataset.eventId, 10);
      setActiveEventId(id);
      onSelectEvent(id);
    });
  });
  container.querySelectorAll(".event-tab[data-inbox]").forEach(el => {
    el.addEventListener("click", () => onSelectInbox());
  });
}

export { INBOX_ID };
export function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
