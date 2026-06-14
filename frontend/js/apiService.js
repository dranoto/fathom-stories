// frontend/js/apiService.js
const MAIN_API = "";

async function handleFetch(url, options = {}) {
  const res = await fetch(MAIN_API + url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    credentials: "same-origin",
    ...options,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

export async function listEvents({ status, minArticles } = {}) {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (minArticles !== undefined) params.set("min_articles", String(minArticles));
  const qs = params.toString();
  return handleFetch(`/api/events${qs ? "?" + qs : ""}`);
}

export async function getEvent(id) {
  return handleFetch(`/api/events/${id}`);
}

export async function getEventSummary(id) {
  return handleFetch(`/api/events/${id}/summary`);
}

export async function generateEventSummary(id) {
  return handleFetch(`/api/events/${id}/summary`, { method: "POST" });
}

export async function getArticle(id) {
  return handleFetch(`/api/articles/${id}`);
}

export async function markRead(id) {
  return handleFetch(`/api/articles/${id}/read`, { method: "POST" });
}

export async function markUnread(id) {
  return handleFetch(`/api/articles/${id}/read`, { method: "DELETE" });
}

export async function listReadArticleIds() {
  const ids = await handleFetch(`/api/articles/reads/ids`);
  return new Set(ids);
}

export async function listUngroupedArticles() {
  return handleFetch(`/api/articles?ungrouped=true&limit=200`);
}

export async function assignArticleToEvent(eventId, articleId) {
  return handleFetch(`/api/events/${eventId}/articles/${articleId}`, { method: "POST" });
}

export async function removeArticleFromEvent(eventId, articleId) {
  return handleFetch(`/api/events/${eventId}/articles/${articleId}`, { method: "DELETE" });
}

export async function stats() {
  return handleFetch(`/api/events/_stats/all`);
}

export async function runGrouping() {
  return handleFetch(`/api/grouping/run`, { method: "POST" });
}

export async function runRegroup() {
  return handleFetch(`/api/grouping/regroup`, { method: "POST" });
}

export async function runFetch() {
  return handleFetch(`/api/grouping/fetch`, { method: "POST" });
}
