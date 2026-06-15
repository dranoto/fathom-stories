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

  setupReaderSwipeDismiss(document.getElementById("reader-pane"));
}

const SWIPE_DISMISS_THRESHOLD = 0.30;
const SWIPE_DISMISS_VELOCITY = 0.5;

function setupReaderSwipeDismiss(pane) {
  if (!pane) return;
  let tracking = null;

  function isOnHeaderOrFooter(target) {
    return target.closest(".reader-header") || target.closest(".reader-footer") || target.closest(".reader-event-picker");
  }

  pane.addEventListener("touchstart", (e) => {
    if (pane.hidden) return;
    if (e.touches.length !== 1) return;
    if (!isOnHeaderOrFooter(e.target)) return;
    const t = e.touches[0];
    tracking = { startY: t.clientY, startT: Date.now(), lastY: t.clientY, lastT: Date.now() };
  }, { passive: true });

  pane.addEventListener("touchmove", (e) => {
    if (!tracking) return;
    const t = e.touches[0];
    const dy = t.clientY - tracking.startY;
    if (Math.abs(dy) < 5) return;
    pane.classList.add("swiping");
    pane.style.transform = `translateY(${dy}px)`;
    tracking.lastY = t.clientY;
    tracking.lastT = Date.now();
  }, { passive: true });

  function endDrag(e) {
    if (!tracking) return;
    const t = (e.changedTouches && e.changedTouches[0]) || null;
    const endY = t ? t.clientY : tracking.lastY;
    const dy = endY - tracking.startY;
    const dt = Math.max(1, tracking.lastT - tracking.startT);
    const velocity = Math.abs(dy) / dt;
    const height = pane.offsetHeight || window.innerHeight;
    const distanceRatio = Math.abs(dy) / height;
    const dismiss = distanceRatio > SWIPE_DISMISS_THRESHOLD || velocity > SWIPE_DISMISS_VELOCITY;
    pane.classList.remove("swiping");
    if (dismiss) {
      pane.classList.add("swipe-dismissing");
      const exitDir = dy >= 0 ? 1 : -1;
      pane.style.transform = `translateY(${exitDir * 100}vh)`;
      pane.style.opacity = "0";
      setTimeout(() => {
        closeReader();
        requestAnimationFrame(() => {
          pane.classList.remove("swipe-dismissing");
          pane.style.transform = "";
          pane.style.opacity = "";
        });
      }, 200);
    } else {
      pane.style.transition = "transform 0.2s ease-out";
      pane.style.transform = "";
      setTimeout(() => {
        pane.style.transition = "";
      }, 200);
    }
    tracking = null;
  }

  pane.addEventListener("touchend", endDrag, { passive: true });
  pane.addEventListener("touchcancel", endDrag, { passive: true });
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
  const moveBtn = document.getElementById("btn-confirm-move");
  if (moveBtn) {
    moveBtn.disabled = false;
    moveBtn.textContent = "Move";
  }
  if (!article.event_id) {
    picker.hidden = false;
    select.innerHTML = `<option value="">— select event —</option>`;
    try {
      const events = await listEvents({ minArticles: 1, status: "active" });
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
        if (!res.already_in) {
          setTimeout(() => {
            window.dispatchEvent(new CustomEvent("navigate-to-event", { detail: { eventId: evId } }));
            window.dispatchEvent(new CustomEvent("open-reader", { detail: { articleId: article.id } }));
          }, 800);
        }
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
  btn.disabled = false;
  btn.textContent = "Remove from event";
  btn.onclick = async () => {
    if (!confirm("Remove this article from the event?\nIf the event would have fewer than 2 articles, the event will be disbanded.")) return;
    btn.disabled = true;
    const orig = btn.textContent;
    btn.textContent = "Removing…";
    try {
      const res = await removeArticleFromEvent(article.event_id, article.id);
      btn.textContent = "Done ✓";
      const targetEventId = article.event_id;
      window.dispatchEvent(new CustomEvent("article-removed", {
        detail: {
          articleId: article.id,
          eventId: article.event_id,
          disbanded: !!res.disbanded,
        },
      }));
      setTimeout(() => {
        window.dispatchEvent(new CustomEvent("navigate-to-event", { detail: { eventId: targetEventId } }));
        window.dispatchEvent(new CustomEvent("open-summary", { detail: { eventId: targetEventId } }));
      }, 600);
    } catch (e) {
      alert("Failed: " + e.message);
      btn.disabled = false;
      btn.textContent = orig;
    }
  };
}

export function closeReader() {
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
      ${summary.key_developments && summary.key_developments.length ? `
        <h2>Key developments</h2>
        <ul>${summary.key_developments.map(k => `<li>${escapeHtml(k)}</li>`).join("")}</ul>
      ` : ""}
      <h2>Progressive update</h2>
      <p>${escapeHtml(summary.progressive_summary || "(none)")}</p>
      ${renderTimelineNarrative(summary.timeline_narrative)}
      ${renderCrossSourceSynthesis(summary.cross_source_synthesis)}
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

function renderTimelineNarrative(value) {
  const heading = `<h2>Timeline narrative</h2>`;
  if (Array.isArray(value)) {
    if (!value.length) return heading + `<p>(none)</p>`;
    const entries = value.slice().reverse().map(entry => {
      const date = entry && entry.date ? `<div class="timeline-date">${escapeHtml(String(entry.date))}</div>` : "";
      const text = entry && entry.text ? `<p>${escapeHtml(String(entry.text))}</p>` : "";
      return `<div class="timeline-entry">${date}${text}</div>`;
    }).join("");
    return heading + entries;
  }
  return heading + `<p>${escapeHtml(value || "(none)")}</p>`;
}

function renderCrossSourceSynthesis(value) {
  const heading = `<h2>Cross-source synthesis</h2>`;
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const bySource = Array.isArray(value.by_source) ? value.by_source : [];
    const bySourceHtml = bySource.length
      ? `<ul class="source-list">${bySource.map(s => {
          const src = s && s.source ? escapeHtml(String(s.source)) : "Unknown";
          const obs = s && s.observation ? escapeHtml(String(s.observation)) : "";
          return `<li class="source-observation"><strong>${src}</strong> — ${obs}</li>`;
        }).join("")}</ul>`
      : "";
    const synth = value.synthesis ? `<p>${escapeHtml(String(value.synthesis))}</p>` : "";
    return heading + bySourceHtml + synth;
  }
  return heading + `<p>${escapeHtml(value || "(none)")}</p>`;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
