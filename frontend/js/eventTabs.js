// frontend/js/eventTabs.js
import { getEvents, setActiveEventId, getActiveEventId } from "./state.js";

export function renderEventTabs(onSelect) {
  const container = document.getElementById("event-tabs");
  const events = getEvents();
  if (!events.length) {
    container.innerHTML = `<div class="tabs-empty">No events yet — run <code>python -m app.cli fetch</code> and <code>python -m app.cli group</code> in the CLI, or click Refresh.</div>`;
    return;
  }
  const activeId = getActiveEventId();
  container.innerHTML = events
    .map(e => {
      const cls = [
        "event-tab",
        e.id === activeId ? "active" : "",
        e.status === "cooling" ? "cooling" : "",
      ].filter(Boolean).join(" ");
      const badge = e.article_count > 0 ? `<span class="badge">${e.article_count}</span>` : "";
      const statusLabel = e.status === "archived" ? " (archived)" : "";
      return `<div class="${cls}" data-event-id="${e.id}">
        <span class="name">${escapeHtml(e.name)}</span>${badge}${statusLabel}
      </div>`;
    })
    .join("");

  container.querySelectorAll(".event-tab").forEach(el => {
    el.addEventListener("click", () => {
      const id = parseInt(el.dataset.eventId, 10);
      setActiveEventId(id);
      onSelect(id);
    });
  });
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
