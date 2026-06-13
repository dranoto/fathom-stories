// frontend_admin/js/articleSearch.js
import { listArticles, addArticleToEvent, createEvent, moveArticle, listEvents } from "./apiService.js";

export async function renderUngrouped(container) {
  container.innerHTML = `<div class="empty">Loading…</div>`;
  const [articles, allEvents] = await Promise.all([
    listArticles({ ungrouped: true, limit: 200 }),
    listEvents(),
  ]);
  const eventOptions = allEvents
    .filter(e => e.status !== "archived")
    .map(e => `<option value="${e.id}">${escapeHtml(e.name)} (${e.article_count})</option>`)
    .join("");

  container.innerHTML = `
    <div style="margin-bottom: 16px; color: var(--text-dim); font-size: 12px;">
      ${articles.length} uncategorized articles. Assign them to an event, or create a new one.
    </div>
    ${articles.length === 0 ? `<div class="empty">Nothing uncategorized. All articles are in events. ✓</div>` :
      articles.map(a => renderUngroupedRow(a, eventOptions)).join("")}
  `;

  attachHandlers(container, allEvents);
}

function renderUngroupedRow(a, eventOptions) {
  const date = a.published_date ? new Date(a.published_date).toLocaleString() : "—";
  return `<div class="list-card" data-article-id="${a.id}">
    <div>
      <div class="title">${escapeHtml(a.title || "(untitled)")}</div>
      <div class="meta">
        <span>${escapeHtml(a.publisher_name || "")}</span>
        <span>${date}</span>
        <span>importance ${(a.importance_score || 0).toFixed(2)}</span>
        ${a.grouping_confidence !== null ? `<span>LLM confidence ${(a.grouping_confidence || 0).toFixed(2)}</span>` : ""}
      </div>
    </div>
    <div class="actions">
      <select data-role="event-select" data-article-id="${a.id}">
        <option value="">— pick event —</option>
        ${eventOptions}
      </select>
      <input data-role="new-name" placeholder="or new event name…" style="width: 200px;" />
      <button data-role="assign" data-article-id="${a.id}">Assign</button>
    </div>
  </div>`;
}

function attachHandlers(container, allEvents) {
  container.querySelectorAll("button[data-role='assign']").forEach(btn => {
    btn.addEventListener("click", async () => {
      const aid = parseInt(btn.dataset.articleId, 10);
      const card = btn.closest(".list-card");
      const eventId = card.querySelector("[data-role='event-select']").value;
      const newName = card.querySelector("[data-role='new-name']").value.trim();
      try {
        if (eventId) {
          await addArticleToEvent(parseInt(eventId, 10), aid);
        } else if (newName) {
          await createEvent(newName);
          const evs = await listEvents();
          const created = evs.find(e => e.name === newName);
          if (created) await addArticleToEvent(created.id, aid);
        } else {
          alert("Pick an event or type a new event name.");
          return;
        }
        await renderUngrouped(container);
      } catch (e) { alert("Error: " + e.message); }
    });
  });
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
