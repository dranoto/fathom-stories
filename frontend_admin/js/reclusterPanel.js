// frontend_admin/js/reclusterPanel.js
import { listProposals, applyProposal, dismissProposal, listEvents } from "./apiService.js";

export async function renderProposals(container) {
  container.innerHTML = `<div class="empty">Loading…</div>`;
  const [proposals, events] = await Promise.all([
    listProposals(true),
    listEvents(),
  ]);
  const eventById = new Map(events.map(e => [e.id, e]));

  if (proposals.length === 0) {
    container.innerHTML = `<div class="empty">No pending proposals. Run <code>recluster</code> from the header button.</div>`;
    return;
  }

  container.innerHTML = proposals.map(p => renderProposal(p, eventById)).join("");

  container.querySelectorAll("button[data-role='apply']").forEach(btn => {
    btn.addEventListener("click", async () => {
      const id = parseInt(btn.dataset.id, 10);
      try {
        await applyProposal(id);
        await renderProposals(container);
      } catch (e) { alert("Error: " + e.message); }
    });
  });
  container.querySelectorAll("button[data-role='dismiss']").forEach(btn => {
    btn.addEventListener("click", async () => {
      const id = parseInt(btn.dataset.id, 10);
      if (!confirm("Dismiss this proposal?")) return;
      try {
        await dismissProposal(id);
        await renderProposals(container);
      } catch (e) { alert("Error: " + e.message); }
    });
  });
}

function renderProposal(p, eventById) {
  const payload = p.payload || {};
  const eventName = (id) => {
    const ev = eventById.get(id);
    return ev ? `${ev.name} [${id}]` : `[${id}]`;
  };
  let body = "";
  switch (p.kind) {
    case "merge":
      body = `Merge ${eventName(payload.event_a_id)} INTO ${eventName(payload.event_b_id)}<br><em>${escapeHtml(payload.reason || "")}</em>`;
      break;
    case "split":
      body = `Split ${eventName(payload.event_id)} → new event <strong>${escapeHtml(payload.suggested_new_name || "")}</strong> (${(payload.anchor_article_ids || []).length} articles)`;
      break;
    case "cool":
      body = `Mark ${eventName(payload.event_id)} as <strong>cooling</strong>`;
      break;
    case "revive":
      body = `Revive archived event ${eventName(payload.event_id)}`;
      break;
    case "new":
      body = `Create new event <strong>${escapeHtml(payload.name || "")}</strong> from ${(payload.anchor_article_ids || []).length} articles`;
      break;
    default:
      body = `<pre>${escapeHtml(JSON.stringify(payload, null, 2))}</pre>`;
  }

  return `<div class="proposal-card kind-${p.kind}">
    <div><strong>${p.kind.toUpperCase()}</strong> · proposed ${new Date(p.created_at).toLocaleString()}</div>
    <div style="margin-top: 6px;">${body}</div>
    <div class="actions">
      <button data-role="apply" data-id="${p.id}" class="btn-primary">Apply</button>
      <button data-role="dismiss" data-id="${p.id}" class="btn-danger">Dismiss</button>
    </div>
  </div>`;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
