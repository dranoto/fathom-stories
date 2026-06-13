// frontend/js/timeline.js
import { getActiveEventDetail, isRead, setActiveEventDetail, getUngroupedArticles, setUngroupedArticles } from "./state.js";
import { getEvent, generateEventSummary, listUngroupedArticles, runRegroup } from "./apiService.js";
import { renderEventTabs, escapeHtml as tabsEscape } from "./eventTabs.js";

export async function renderActiveEventPane(eventId) {
  const pane = document.getElementById("event-pane");
  pane.innerHTML = `<div class="pane-empty">Loading…</div>`;

  let detail;
  try {
    detail = await getEvent(eventId);
  } catch (e) {
    pane.innerHTML = `<div class="pane-empty">Error loading event: ${e.message}</div>`;
    return;
  }
  setActiveEventDetail(detail);

  const ev = detail;
  const articles = ev.articles || [];
  const summary = ev.latest_summary;
  const lastSummary = summary && summary.generated_at ? new Date(summary.generated_at) : null;
  const lastArticleAt = ev.last_article_at ? new Date(ev.last_article_at) : null;
  const summaryStale = lastSummary && lastArticleAt && lastArticleAt > lastSummary;

  const html = `
    <div class="event-header">
      <h1>${escapeHtml(ev.name)}</h1>
      <div class="meta">
        <span>${ev.article_count || articles.length} articles</span>
        ${ev.last_article_at ? `<span>last: ${formatRelative(new Date(ev.last_article_at))}</span>` : ""}
        <span>status: ${ev.status}</span>
      </div>
    </div>
    ${renderSummary(summary, summaryStale, eventId)}
    <div class="timeline">
      ${articles.length === 0 ? `<div class="pane-empty">No articles in this event yet.</div>` : articles.map(renderBubble).join("")}
    </div>
  `;
  pane.innerHTML = html;
  attachBubbleHandlers(pane, eventId);
  attachSummaryHandlers(pane, eventId);
}

function renderSummary(summary, stale, eventId) {
  if (!summary) {
    return `<div class="summary-card">
      <div class="actions">
        <button id="btn-gen-summary" class="btn-secondary">Generate summary</button>
        <span class="summary-stale">no summary yet</span>
      </div>
    </div>`;
  }
  return `<div class="summary-card">
    <div class="summary-section">
      <h3>Timeline narrative</h3>
      <div>${escapeHtml(summary.timeline_narrative || "")}</div>
    </div>
    <div class="summary-section">
      <h3>Cross-source synthesis</h3>
      <div>${escapeHtml(summary.cross_source_synthesis || "")}</div>
    </div>
    <div class="summary-section">
      <h3>Progressive update</h3>
      <div>${escapeHtml(summary.progressive_summary || "")}</div>
    </div>
    ${summary.key_developments && summary.key_developments.length ? `
      <div class="summary-section">
        <h3>Key developments</h3>
        <ul class="key-devs">${summary.key_developments.map(k => `<li>${escapeHtml(k)}</li>`).join("")}</ul>
      </div>
    ` : ""}
    <div class="actions">
      <button id="btn-regen-summary" class="btn-secondary">Regenerate</button>
      ${stale ? `<span class="summary-stale">new articles since last summary</span>` : ""}
      <span style="margin-left:auto;color:var(--text-dim);font-size:11px">
        v${(summary.article_count || 0)} articles · ${formatDate(summary.generated_at || new Date())}
      </span>
    </div>
  </div>`;
}

function renderBubble(a) {
  const isLarge = (a.importance_score || 0) >= 0.7;
  const isSmall = (a.importance_score || 0) < 0.3;
  const sizeClass = isLarge ? "size-lg" : isSmall ? "size-sm" : "size-md";
  const readClass = isRead(a.id) ? "read" : "";
  const uncertain = (a.grouping_confidence || 0) < 0.5 ? "uncertain" : "";
  const ts = a.published_date ? formatRelative(new Date(a.published_date)) : "?";
  return `<div class="timeline-row">
    <div class="ts">${escapeHtml(ts)}</div>
    <div class="bubble ${sizeClass} ${readClass} ${uncertain}" data-article-id="${a.id}">
      <div class="bubble-title">${escapeHtml(a.title || "(untitled)")}</div>
      <div class="bubble-meta">
        <span class="imp" title="importance ${(a.importance_score || 0).toFixed(2)}"></span>
        <span>${escapeHtml(a.publisher_name || "")}</span>
      </div>
    </div>
  </div>`;
}

function attachBubbleHandlers(pane, eventId) {
  pane.querySelectorAll(".bubble").forEach(el => {
    el.addEventListener("click", () => {
      const id = parseInt(el.dataset.articleId, 10);
      window.dispatchEvent(new CustomEvent("open-reader", { detail: { articleId: id } }));
    });
  });
}

function attachSummaryHandlers(pane, eventId) {
  const gen = pane.querySelector("#btn-gen-summary");
  const regen = pane.querySelector("#btn-regen-summary");
  const handler = async () => {
    const btn = gen || regen;
    btn.disabled = true;
    btn.textContent = "Generating…";
    try {
      await generateEventSummary(eventId);
      await renderActiveEventPane(eventId);
    } catch (e) {
      alert("Summary failed: " + e.message);
      btn.disabled = false;
      btn.textContent = btn.id === "btn-regen-summary" ? "Regenerate" : "Generate summary";
    }
  };
  if (gen) gen.addEventListener("click", handler);
  if (regen) regen.addEventListener("click", handler);
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function formatRelative(d) {
  const diff = Date.now() - d.getTime();
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return "just now";
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h`;
  const days = Math.floor(hr / 24);
  if (days < 30) return `${days}d`;
  return d.toLocaleDateString();
}

function formatDate(d) {
  if (typeof d === "string") d = new Date(d);
  return d.toLocaleString();
}

export async function renderInboxPane() {
  const pane = document.getElementById("event-pane");
  pane.innerHTML = `<div class="pane-empty">Loading inbox…</div>`;

  let articles;
  try {
    articles = await listUngroupedArticles();
  } catch (e) {
    pane.innerHTML = `<div class="pane-empty">Error loading inbox: ${escapeHtml(e.message)}</div>`;
    return;
  }
  setUngroupedArticles(articles);

  const header = `
    <div class="event-header">
      <h1>Inbox</h1>
      <div class="meta">
        <span>${articles.length} ungrouped article${articles.length === 1 ? "" : "s"}</span>
        <span>·</span>
        <button id="btn-regroup" class="btn-secondary">Regroup now</button>
      </div>
    </div>
  `;

  if (articles.length === 0) {
    pane.innerHTML = header + `<div class="pane-empty">All articles are in events. ✓</div>`;
    attachRegroupHandler(pane);
    return;
  }

  const rows = articles
    .map(a => {
      const readClass = isRead(a.id) ? "read" : "";
      const uncertain = (a.grouping_confidence || 0) < 0.5 ? "uncertain" : "";
      const importance = a.importance_score || 0;
      const sizeClass = importance >= 0.7 ? "size-lg" : importance < 0.3 ? "size-sm" : "size-md";
      const proposed = a.proposed_event_name ? `<span class="proposed">${escapeHtml(a.proposed_event_name)}</span>` : "";
      const ts = a.published_date ? formatRelative(new Date(a.published_date)) : "?";
      return `<div class="timeline-row">
        <div class="ts">${escapeHtml(ts)}</div>
        <div class="bubble ${sizeClass} ${readClass} ${uncertain}" data-article-id="${a.id}">
          <div class="bubble-title">${escapeHtml(a.title || "(untitled)")}</div>
          <div class="bubble-meta">
            <span class="imp" title="importance ${importance.toFixed(2)}"></span>
            <span>${escapeHtml(a.publisher_name || "")}</span>
            ${proposed}
          </div>
        </div>
      </div>`;
    })
    .join("");

  pane.innerHTML = header + `<div class="timeline">${rows}</div>`;
  pane.querySelectorAll(".bubble").forEach(el => {
    el.addEventListener("click", () => {
      const id = parseInt(el.dataset.articleId, 10);
      window.dispatchEvent(new CustomEvent("open-reader", { detail: { articleId: id } }));
    });
  });
  attachRegroupHandler(pane);
}

function attachRegroupHandler(pane) {
  const btn = pane.querySelector("#btn-regroup");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    const orig = btn.textContent;
    btn.textContent = "Regrouping…";
    try {
      const result = await runRegroup();
      const r = JSON.stringify(result);
      window.dispatchEvent(new CustomEvent("regroup-done", { detail: { result } }));
      setTimeout(() => window.location.reload(), 800);
    } catch (e) {
      btn.disabled = false;
      btn.textContent = orig;
      alert("Regroup failed: " + e.message);
    }
  });
}
