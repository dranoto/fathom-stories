// frontend_admin/js/feedback.js
import { listFeedback, listEvents } from "./apiService.js";

export async function renderFeedback(container) {
  container.innerHTML = `<div class="empty">Loading…</div>`;
  const [rows, events] = await Promise.all([
    listFeedback(50),
    listEvents(),
  ]);
  const eventById = new Map(events.map(e => [e.id, e]));
  if (rows.length === 0) {
    container.innerHTML = `<div class="empty">No feedback yet. Corrections you make (moves, merges, splits) appear here and are fed back to the LLM as few-shot examples.</div>`;
    return;
  }
  container.innerHTML = rows.map(r => {
    const fromName = r.original_event_id ? (eventById.get(r.original_event_id)?.name || `[${r.original_event_id}]`) : "—";
    const toName = r.corrected_event_id ? (eventById.get(r.corrected_event_id)?.name || `[${r.corrected_event_id}]`) : "—";
    return `<div class="list-card">
      <div>
        <div class="title"><span class="status-tag">${r.kind}</span> article #${r.article_id}</div>
        <div class="meta">
          <span>${escapeHtml(fromName)} → ${escapeHtml(toName)}</span>
          <span>${new Date(r.created_at).toLocaleString()}</span>
          ${r.note ? `<span>${escapeHtml(r.note)}</span>` : ""}
        </div>
      </div>
    </div>`;
  }).join("");
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
