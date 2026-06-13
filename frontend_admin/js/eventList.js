// frontend_admin/js/eventList.js
import {
  listEvents, createEvent, updateEvent, deleteEvent,
  mergeEvents, splitEvent, reviveEvent, getEvent,
} from "./apiService.js";

export async function renderEventList(container) {
  container.innerHTML = `<div class="empty">Loading…</div>`;
  const [active, cooling, archived] = await Promise.all([
    listEvents("active"),
    listEvents("cooling"),
    listEvents("archived"),
  ]);
  const all = [...active, ...cooling, ...archived].sort((a, b) => {
    const sa = { active: 0, cooling: 1, archived: 2 }[a.status] || 3;
    const sb = { active: 0, cooling: 1, archived: 2 }[b.status] || 3;
    if (sa !== sb) return sa - sb;
    const ta = a.last_article_at ? new Date(a.last_article_at).getTime() : 0;
    const tb = b.last_article_at ? new Date(b.last_article_at).getTime() : 0;
    return tb - ta;
  });

  container.innerHTML = `
    <div style="margin-bottom: 16px; display: flex; gap: 8px; align-items: center;">
      <input id="new-event-name" placeholder="New event name…" style="flex: 1;" />
      <button id="btn-create-event" class="btn-primary">Create event</button>
    </div>
    ${all.length === 0 ? `<div class="empty">No events yet.</div>` : all.map(renderCard).join("")}
  `;

  document.getElementById("btn-create-event").addEventListener("click", async () => {
    const name = document.getElementById("new-event-name").value.trim();
    if (!name) return;
    try {
      await createEvent(name);
      await renderEventList(container);
    } catch (e) { alert(e.message); }
  });

  attachHandlers(container);
}

function renderCard(ev) {
  const lastA = ev.last_article_at ? new Date(ev.last_article_at).toLocaleString() : "—";
  return `<div class="list-card" data-event-id="${ev.id}">
    <div>
      <div class="title">
        <span class="status-tag ${ev.status}">${ev.status}</span>
        ${escapeHtml(ev.name)}
      </div>
      <div class="meta">
        <span>${ev.article_count} articles</span>
        <span>last: ${lastA}</span>
        ${ev.summary_version ? `<span>summary v${ev.summary_version}</span>` : ""}
        ${ev.archived_at ? `<span>archived: ${new Date(ev.archived_at).toLocaleDateString()}</span>` : ""}
      </div>
    </div>
    <div class="actions">
      <button data-action="rename">Rename</button>
      <button data-action="set-status">Set status</button>
      <button data-action="merge">Merge into…</button>
      <button data-action="split">Split…</button>
      ${ev.status === "archived" ? `<button data-action="revive">Revive</button>` : ""}
      <button data-action="delete" class="btn-danger">Delete</button>
    </div>
  </div>`;
}

function attachHandlers(container) {
  container.querySelectorAll(".list-card").forEach(card => {
    const id = parseInt(card.dataset.eventId, 10);
    card.querySelectorAll("button[data-action]").forEach(btn => {
      btn.addEventListener("click", async () => {
        const action = btn.dataset.action;
        try {
          if (action === "rename") {
            const newName = prompt("New name:");
            if (!newName) return;
            await updateEvent(id, { name: newName });
          } else if (action === "set-status") {
            const s = prompt("Status (active|cooling|archived):");
            if (!s) return;
            await updateEvent(id, { status: s });
          } else if (action === "merge") {
            const otherId = parseInt(prompt("Other event ID to merge into this one:"), 10);
            if (!otherId) return;
            await mergeEvents(id, otherId, {});
            await updateEvent(otherId, {});  // noop - just to ensure other is gone
          } else if (action === "split") {
            const ev = await getEvent(id);
            const articleIds = (ev.articles || []).map(a => a.id);
            if (articleIds.length < 2) {
              alert("Event needs at least 2 articles to split.");
              return;
            }
            const indices = prompt(
              `Enter article IDs to split into a new event (comma-separated):\n\n` +
              articleIds.map((aid, i) => `${i+1}. [${aid}] ${ev.articles[i].title}`).join("\n")
            );
            if (!indices) return;
            const picks = indices.split(",").map(s => parseInt(s.trim(), 10)).filter(n => articleIds.includes(n));
            if (picks.length === 0) {
              alert("No valid article IDs selected.");
              return;
            }
            const newName = prompt("Name for new event:");
            if (!newName) return;
            await splitEvent(id, { new_event_name: newName, article_ids: picks, note: "" });
          } else if (action === "revive") {
            await reviveEvent(id);
          } else if (action === "delete") {
            if (!confirm("Delete this event? Articles will be moved to uncategorized.")) return;
            await deleteEvent(id);
          }
          await renderEventList(container);
        } catch (e) {
          alert("Error: " + e.message);
        }
      });
    });
  });
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
