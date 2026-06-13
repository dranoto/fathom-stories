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
        e.status === "cooling" ? "cooling" : "",
      ].filter(Boolean).join(" ");
      const badge = e.article_count > 0 ? `<span class="badge">${e.article_count}</span>` : "";
      return `<div class="${cls}" data-event-id="${e.id}">
        <span class="name">${escapeHtml(e.name)}</span>${badge}
      </div>`;
    })
    .join("");

  const inboxCls = inboxOpen ? "active" : "";
  const inboxBadge = inboxN > 0
    ? `<span class="badge inbox-badge" title="${inboxU} unread, ${inboxR} read">${inboxU}<span class="sep">/</span>${inboxN}</span>`
    : "";
  const inboxHtml = `<div class="event-tab inbox-tab ${inboxCls}" data-inbox="1">
    <span class="name">Inbox</span>${inboxBadge}
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
