// frontend/js/eventTabs.js
import { getEvents, setActiveEventId, getActiveEventId, getInboxOpen } from "./state.js";

const INBOX_ID = "__inbox__";

export function renderEventTabs(onSelectEvent, onSelectInbox) {
  const container = document.getElementById("event-tabs");
  const events = getEvents();
  const activeId = getActiveEventId();
  const inboxOpen = getInboxOpen();

  if (!events.length) {
    container.innerHTML = `<div class="tabs-empty">No multi-article events yet — fetch and regroup some articles.</div>`;
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
  const inboxHtml = `<div class="event-tab inbox-tab ${inboxCls}" data-inbox="1">
    <span class="name">Inbox</span>
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
