// frontend/js/timeline.js
import { getActiveEventDetail, getActiveEventId, getCurrentArticleId, isEventRegenerating, isRead, setActiveEventDetail, getUngroupedArticles, setUngroupedArticles, setInboxCounts } from "./state.js";
import { getEvent, listUngroupedArticles, runRegroup } from "./apiService.js";
import { renderEventTabs, escapeHtml as tabsEscape } from "./eventTabs.js";

window.addEventListener("current-article-changed", (e) => {
  const id = e.detail && e.detail.articleId;
  document.querySelectorAll(".bubble.current-article").forEach(el => el.classList.remove("current-article"));
  if (id != null) {
    const target = document.querySelector(`.bubble[data-article-id="${id}"]`);
    if (target) target.classList.add("current-article");
  }
});

window.addEventListener("regenerating-events-changed", () => {
  const activeId = getActiveEventId();
  if (activeId != null) {
    renderActiveEventPane(activeId);
  }
});

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
  const expiresAt = ev.expires_at ? new Date(ev.expires_at) : null;

  const summaryBubble = renderSummaryBubble(summary, summaryStale, eventId);
  const articleBubbles = articles.length === 0
    ? `<div class="pane-empty">No articles in this event yet.</div>`
    : articles.map(renderBubble).join("");

  const html = `
    <div class="event-header">
      <h1>${escapeHtml(ev.name)}</h1>
      ${expiresAt ? `
        <div class="expiry-row">
          <span class="expiry-chip" data-expires-at="${expiresAt.toISOString()}" title="Event auto-archives at ${escapeHtml(expiresAt.toLocaleString())}">
            <span class="expiry-label">expires in</span>
            <span class="expiry-time">--</span>
          </span>
        </div>
      ` : ""}
      <div class="meta">
        <span>${ev.article_count || articles.length} articles</span>
        ${ev.last_article_at ? `<span>last: ${formatRelative(new Date(ev.last_article_at))}</span>` : ""}
        <span>status: ${ev.status}</span>
      </div>
    </div>
    <div class="timeline">
      ${summaryBubble}
      ${articleBubbles}
    </div>
  `;
  pane.innerHTML = html;
  attachBubbleHandlers(pane, eventId);
  attachSummaryBubbleHandler(pane, eventId);
  if (expiresAt) startExpiryCountdown(pane, expiresAt);
}

function renderSummaryBubble(summary, stale, eventId) {
  const version = summary ? (summary.article_count || 0) : 0;
  const dotTitle = summary ? `v${version}` : "pending generation";
  const regenerating = isEventRegenerating(eventId);
  const regenChip = regenerating
    ? `<span class="summary-regen-chip" title="Summary is regenerating in the background"><span class="summary-regen-dot"></span>regenerating</span>`
    : "";
  return `<div class="timeline-row summary-row">
    <div class="ts">summary</div>
    <div class="bubble summary-bubble size-lg" data-summary-id="${eventId}">
      <div class="bubble-title-row">
        <div class="bubble-title">Event Summary</div>
        ${regenChip}
      </div>
      <div class="bubble-summary-actions">
        <span class="imp" style="background:var(--info)" title="${dotTitle}"></span>
        <button class="summary-chat-btn" data-action="chat-with-event" data-event-id="${eventId}" title="Chat with this event (coming soon)">Chat with event</button>
      </div>
    </div>
  </div>`;
}

function renderBubble(a) {
  const isLarge = (a.importance_score || 0) >= 0.7;
  const isSmall = (a.importance_score || 0) < 0.3;
  const sizeClass = isLarge ? "size-lg" : isSmall ? "size-sm" : "size-md";
  const readClass = isRead(a.id) ? "read" : "";
  const uncertain = (a.grouping_confidence || 0) < 0.5 ? "uncertain" : "";
  const currentClass = getCurrentArticleId() === a.id ? "current-article" : "";
  const ts = a.published_date ? formatRelative(new Date(a.published_date)) : "?";
  return `<div class="timeline-row">
    <div class="ts">${escapeHtml(ts)}</div>
    <div class="bubble ${sizeClass} ${readClass} ${uncertain} ${currentClass}" data-article-id="${a.id}">
      <div class="bubble-title">${escapeHtml(a.title || "(untitled)")}</div>
      <div class="bubble-meta">
        <span class="imp" title="importance ${(a.importance_score || 0).toFixed(2)}"></span>
        <span>${escapeHtml(a.publisher_name || "")}</span>
      </div>
    </div>
  </div>`;
}

function attachBubbleHandlers(pane, eventId) {
  pane.querySelectorAll(".bubble[data-article-id]").forEach(el => {
    el.addEventListener("click", () => {
      const id = parseInt(el.dataset.articleId, 10);
      window.dispatchEvent(new CustomEvent("open-reader", { detail: { articleId: id } }));
    });
  });
}

function attachSummaryBubbleHandler(pane, eventId) {
  const bubble = pane.querySelector(".bubble[data-summary-id]");
  if (!bubble) return;
  bubble.addEventListener("click", () => {
    window.dispatchEvent(new CustomEvent("open-summary", { detail: { eventId } }));
  });
  const chatBtn = pane.querySelector(".summary-chat-btn");
  if (chatBtn) {
    chatBtn.addEventListener("click", (e) => {
      e.stopPropagation();
    });
  }
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

let _expiryTimer = null;

function startExpiryCountdown(pane, expiresAt) {
  if (_expiryTimer) {
    clearInterval(_expiryTimer);
    _expiryTimer = null;
  }
  const chip = pane.querySelector(".expiry-chip");
  const timeEl = chip && chip.querySelector(".expiry-time");
  if (!chip || !timeEl) return;

  function formatMs(ms) {
    if (ms === null || ms === undefined || isNaN(ms)) return "--:--";
    if (ms <= 0) return "due";
    const total = Math.floor(ms / 1000);
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    if (h >= 1) return `${h}h ${String(m).padStart(2, "0")}m`;
    if (m >= 1) return `${m}m ${String(s).padStart(2, "0")}s`;
    return `${s}s`;
  }

  function tick() {
    const now = Date.now();
    const ms = expiresAt.getTime() - now;
    timeEl.textContent = formatMs(ms);
    chip.classList.toggle("due", ms > 0 && ms < 60 * 60 * 1000);
    chip.classList.toggle("expired", ms <= 0);
  }

  tick();
  _expiryTimer = setInterval(tick, 1000);
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
  setInboxCounts(articles.length, articles.filter(a => a.is_read).length, articles.filter(a => !a.is_read).length);

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
      const currentClass = getCurrentArticleId() === a.id ? "current-article" : "";
      const proposed = a.proposed_event_name ? `<span class="proposed">${escapeHtml(a.proposed_event_name)}</span>` : "";
      const ts = a.published_date ? formatRelative(new Date(a.published_date)) : "?";
      return `<div class="timeline-row">
        <div class="ts">${escapeHtml(ts)}</div>
        <div class="bubble ${sizeClass} ${readClass} ${uncertain} ${currentClass}" data-article-id="${a.id}">
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
