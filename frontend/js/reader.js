// frontend/js/reader.js
import { getArticle, getEvent, listEvents, markRead, markUnread, generateEventSummary, assignArticleToEvent, removeArticleFromEvent } from "./apiService.js";
import {
  isRead,
  markRead as stateMarkRead,
  markUnread as stateMarkUnread,
  patchEventUnreadCount,
  dispatchReadStateChanged,
  dispatchReaderClosed,
} from "./state.js";

let currentArticle = null;
let currentSummary = null;
let currentEventId = null;

export function setupReader() {
  const close = document.getElementById("btn-close-reader");
  const toggle = document.getElementById("btn-toggle-read");
  const picker = document.getElementById("reader-event-picker");
  if (picker) picker.hidden = true;
  const removeBtn = document.getElementById("btn-remove-from-event");
  if (removeBtn) removeBtn.hidden = true;

  window.addEventListener("open-reader", async (e) => {
    const id = e.detail.articleId;
    await openArticle(id);
  });

  window.addEventListener("open-summary", async (e) => {
    const id = e.detail.eventId;
    await openSummary(id);
  });

  close.addEventListener("click", () => closeReader());
  toggle.addEventListener("click", async () => {
    if (currentSummary && currentEventId) {
      await regenerateSummary();
      return;
    }
    if (!currentArticle) return;
    const nextIsRead = !isRead(currentArticle.id);
    if (nextIsRead) {
      await markRead(currentArticle.id);
      stateMarkRead(currentArticle.id);
      toggle.textContent = "Mark unread";
    } else {
      await markUnread(currentArticle.id);
      stateMarkUnread(currentArticle.id);
      toggle.textContent = "Mark read";
    }
    const bubble = document.querySelector(`.bubble[data-article-id="${currentArticle.id}"]`);
    if (bubble) bubble.classList.toggle("read", nextIsRead);
    patchEventUnreadCount(currentArticle.id, currentArticle.event_id, nextIsRead);
    dispatchReadStateChanged(currentArticle.id, currentArticle.event_id, nextIsRead);
  });
}

async function openArticle(id) {
  const main = document.querySelector(".app-main");
  const pane = document.getElementById("reader-pane");
  const body = document.getElementById("reader-body");
  const source = document.getElementById("reader-source");
  const orig = document.getElementById("reader-original");
  const toggle = document.getElementById("btn-toggle-read");

  body.innerHTML = `<div class="pane-empty">Loading…</div>`;
  pane.hidden = false;
  const picker = document.getElementById("reader-event-picker");
  if (picker) picker.hidden = true;
  const removeBtn = document.getElementById("btn-remove-from-event");
  if (removeBtn) removeBtn.hidden = true;
  main.classList.add("has-reader");

  let article;
  try {
    article = await getArticle(id);
  } catch (e) {
    body.innerHTML = `<div class="pane-empty">Error: ${e.message}</div>`;
    return;
  }
  currentArticle = article;
  currentSummary = null;
  currentEventId = null;

  source.textContent = `${article.publisher_name || ""} · ${article.published_date ? new Date(article.published_date).toLocaleString() : ""}`;
  orig.href = article.url;
  orig.style.display = "";

  const html = article.full_html_content || `<pre>${escapeHtml(article.scraped_text_content || article.rss_description || "")}</pre>`;
  body.innerHTML = `
    <h1>${escapeHtml(article.title || "(untitled)")}</h1>
    <div class="reader-content">${html}</div>
  `;
  toggle.textContent = isRead(article.id) ? "Mark unread" : "Mark read";
  setupRemoveButton(article);

  await renderEventPicker(article);

  if (!isRead(article.id)) {
    try {
      await markRead(article.id);
      stateMarkRead(article.id);
      toggle.textContent = "Mark unread";
    } catch (e) {
      console.warn("mark-read failed:", e);
    }
    const bubble = document.querySelector(`.bubble[data-article-id="${article.id}"]`);
    if (bubble) bubble.classList.add("read");
    patchEventUnreadCount(article.id, article.event_id, true);
    dispatchReadStateChanged(article.id, article.event_id, true);
  }
}

async function renderEventPicker(article) {
  const picker = document.getElementById("reader-event-picker");
  const select = document.getElementById("reader-event-select");
  const status = document.getElementById("reader-move-status");
  if (!article.event_id) {
    picker.hidden = false;
    select.innerHTML = `<option value="">— select event —</option>`;
    try {
      const events = await listEvents({ minArticles: 1 });
      for (const ev of events) {
        select.innerHTML += `<option value="${ev.id}">${escapeHtml(ev.name)} (${ev.article_count})</option>`;
      }
    } catch (e) { /* ignore */ }
    status.textContent = "";
    document.getElementById("btn-confirm-move").onclick = async () => {
      const evId = parseInt(select.value, 10);
      if (!evId) {
        status.textContent = "Pick an event first.";
        return;
      }
      const btn = document.getElementById("btn-confirm-move");
      const orig = btn.textContent;
      btn.disabled = true;
      status.textContent = "Moving…";
      try {
        const res = await assignArticleToEvent(evId, article.id);
        if (res.already_in) {
          status.textContent = "Already in that event.";
        } else {
          status.textContent = res.summary_regenerated
            ? "Moved · summary regenerated ✓"
            : "Moved · (summary regen failed, will retry next regroup)";
          window.dispatchEvent(new CustomEvent("article-moved", { detail: { articleId: article.id, eventId: evId } }));
        }
        btn.textContent = "Done ✓";
      } catch (e) {
        status.textContent = "Error: " + e.message;
        btn.disabled = false;
        btn.textContent = orig;
      }
    };
  } else {
    picker.hidden = true;
    status.textContent = "";
  }
}

function setupRemoveButton(article) {
  const btn = document.getElementById("btn-remove-from-event");
  if (!btn) return;
  if (!article.event_id) {
    btn.hidden = true;
    return;
  }
  btn.hidden = false;
  btn.onclick = async () => {
    if (!confirm("Remove this article from the event?\nIf the event would have fewer than 2 articles, the event will be disbanded.")) return;
    btn.disabled = true;
    const orig = btn.textContent;
    btn.textContent = "Removing…";
    try {
      const res = await removeArticleFromEvent(article.event_id, article.id);
      btn.textContent = "Done ✓";
      window.dispatchEvent(new CustomEvent("article-removed", {
        detail: {
          articleId: article.id,
          eventId: article.event_id,
          disbanded: !!res.disbanded,
        },
      }));
      setTimeout(() => closeReader(), 600);
    } catch (e) {
      alert("Failed: " + e.message);
      btn.disabled = false;
      btn.textContent = orig;
    }
  };
}

function closeReader() {
  const main = document.querySelector(".app-main");
  const pane = document.getElementById("reader-pane");
  pane.hidden = true;
  main.classList.remove("has-reader");
  currentArticle = null;
  currentSummary = null;
  currentEventId = null;
  const picker = document.getElementById("reader-event-picker");
  if (picker) picker.hidden = true;
  const removeBtn = document.getElementById("btn-remove-from-event");
  if (removeBtn) {
    removeBtn.hidden = true;
    removeBtn.disabled = false;
  }
  dispatchReaderClosed();
}

async function openSummary(eventId) {
  const main = document.querySelector(".app-main");
  const pane = document.getElementById("reader-pane");
  const body = document.getElementById("reader-body");
  const source = document.getElementById("reader-source");
  const orig = document.getElementById("reader-original");
  const toggle = document.getElementById("btn-toggle-read");

  body.innerHTML = `<div class="pane-empty">Loading summary…</div>`;
  pane.hidden = false;
  main.classList.add("has-reader");
  const removeBtn = document.getElementById("btn-remove-from-event");
  if (removeBtn) removeBtn.hidden = true;
  currentArticle = null;
  currentEventId = eventId;

  let event;
  try {
    event = await getEvent(eventId);
  } catch (e) {
    body.innerHTML = `<div class="pane-empty">Error: ${e.message}</div>`;
    return;
  }

  const summary = event.latest_summary;
  currentSummary = summary;
  source.textContent = `${event.name} · Event Summary`;
  orig.href = "#";
  orig.style.display = "none";
  const picker = document.getElementById("reader-event-picker");
  if (picker) picker.hidden = true;

  if (!summary) {
    body.innerHTML = `
      <h1>${escapeHtml(event.name)} — Event Summary</h1>
      <div class="reader-content">
        <p>No summary yet. The summary is auto-generated after grouping and auto-updated when new articles join the event.</p>
        <button id="btn-force-gen" class="btn-secondary">Generate now</button>
      </div>
    `;
    toggle.textContent = "Generate";
    const btn = body.querySelector("#btn-force-gen");
    if (btn) {
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        btn.textContent = "Generating…";
        try {
          await generateEventSummary(eventId);
          await openSummary(eventId);
        } catch (e) {
          alert("Failed: " + e.message);
          btn.disabled = false;
          btn.textContent = "Generate now";
        }
      });
    }
    return;
  }

  body.innerHTML = `
    <h1>${escapeHtml(event.name)} — Event Summary</h1>
    <div class="reader-meta">v${summary.article_count || 0} articles · ${formatDate(summary.generated_at)}</div>
    <div class="reader-content">
      <h2>Timeline narrative</h2>
      <p>${escapeHtml(summary.timeline_narrative || "(none)")}</p>
      <h2>Cross-source synthesis</h2>
      <p>${escapeHtml(summary.cross_source_synthesis || "(none)")}</p>
      <h2>Progressive update</h2>
      <p>${escapeHtml(summary.progressive_summary || "(none)")}</p>
      ${summary.key_developments && summary.key_developments.length ? `
        <h2>Key developments</h2>
        <ul>${summary.key_developments.map(k => `<li>${escapeHtml(k)}</li>`).join("")}</ul>
      ` : ""}
    </div>
  `;
  toggle.textContent = "Regenerate";
  toggle.style.display = "";
  orig.style.display = "none";
}

async function regenerateSummary() {
  if (!currentEventId) return;
  const toggle = document.getElementById("btn-toggle-read");
  const orig = toggle.textContent;
  toggle.disabled = true;
  toggle.textContent = "Regenerating…";
  try {
    await generateEventSummary(currentEventId);
    await openSummary(currentEventId);
  } catch (e) {
    alert("Regenerate failed: " + e.message);
    toggle.disabled = false;
    toggle.textContent = orig;
  }
}

function formatDate(d) {
  if (!d) return "";
  if (typeof d === "string") d = new Date(d);
  return d.toLocaleString();
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
