// frontend/js/reader.js
import { getArticle, markRead, markUnread } from "./apiService.js";
import { isRead, markRead as stateMarkRead, markUnread as stateMarkUnread } from "./state.js";

let currentArticle = null;

export function setupReader() {
  const main = document.querySelector(".app-main");
  const close = document.getElementById("btn-close-reader");
  const toggle = document.getElementById("btn-toggle-read");

  window.addEventListener("open-reader", async (e) => {
    const id = e.detail.articleId;
    await openArticle(id);
  });

  close.addEventListener("click", () => closeReader());
  toggle.addEventListener("click", async () => {
    if (!currentArticle) return;
    if (isRead(currentArticle.id)) {
      await markUnread(currentArticle.id);
      stateMarkUnread(currentArticle.id);
      toggle.textContent = "Mark read";
    } else {
      await markRead(currentArticle.id);
      stateMarkRead(currentArticle.id);
      toggle.textContent = "Mark unread";
    }
    const bubble = document.querySelector(`.bubble[data-article-id="${currentArticle.id}"]`);
    if (bubble) bubble.classList.toggle("read", isRead(currentArticle.id));
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
  main.classList.add("has-reader");

  let article;
  try {
    article = await getArticle(id);
  } catch (e) {
    body.innerHTML = `<div class="pane-empty">Error: ${e.message}</div>`;
    return;
  }
  currentArticle = article;

  source.textContent = `${article.publisher_name || ""} · ${article.published_date ? new Date(article.published_date).toLocaleString() : ""}`;
  orig.href = article.url;

  const html = article.full_html_content || `<pre>${escapeHtml(article.scraped_text_content || article.rss_description || "")}</pre>`;
  body.innerHTML = `
    <h1>${escapeHtml(article.title || "(untitled)")}</h1>
    <div class="reader-content">${html}</div>
  `;
  toggle.textContent = isRead(article.id) ? "Mark unread" : "Mark read";

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
  }
}

function closeReader() {
  const main = document.querySelector(".app-main");
  const pane = document.getElementById("reader-pane");
  pane.hidden = true;
  main.classList.remove("has-reader");
  currentArticle = null;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
