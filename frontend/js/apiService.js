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

export async function searchArticles({ keyword, limit = 20 } = {}) {
  const params = new URLSearchParams();
  if (keyword) params.set("keyword", keyword);
  if (limit) params.set("limit", String(limit));
  return handleFetch(`/api/events/search/articles?${params}`);
}

export async function listFeeds() {
  return handleFetch(`/api/feeds`);
}

export async function addFeed({ url, name, fetch_interval_minutes }) {
  return handleFetch(`/api/feeds`, {
    method: "POST",
    body: JSON.stringify({ url, name, fetch_interval_minutes }),
  });
}

export async function removeFeed(id) {
  return handleFetch(`/api/feeds/${id}`, { method: "DELETE" });
}

export async function pauseFeed(id) {
  return handleFetch(`/api/feeds/${id}/pause`, { method: "POST" });
}

export async function unpauseFeed(id) {
  return handleFetch(`/api/feeds/${id}/pause`, { method: "DELETE" });
}

export async function refreshFeed(id) {
  return handleFetch(`/api/feeds/${id}/refresh`, { method: "POST" });
}

export async function markEventVisited(eventId) {
  return handleFetch(`/api/events/${eventId}/visit`, { method: "POST" });
}

export async function fetchEventChatHistory(eventId) {
  return handleFetch(`/api/events/${eventId}/chat-history`);
}

export async function persistEventChatTurn(eventId, payload) {
  return handleFetch(`/api/events/${eventId}/chat/persist`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function streamEventChat(eventId, payload, { onMeta, onDelta, onError, onDone, signal } = {}) {
  const url = `/api/events/${eventId}/chat`;
  return fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify(payload),
    signal,
  }).then(async (res) => {
    if (!res.ok || !res.body) {
      const text = await res.text().catch(() => "");
      const err = new Error(`${res.status} ${res.statusText}: ${text}`);
      if (onError) onError(err);
      throw err;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    let currentEvent = "message";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let sep;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        const raw = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        const lines = raw.split("\n");
        let dataLines = [];
        for (const line of lines) {
          if (line.startsWith("event:")) {
            currentEvent = line.slice(6).trim();
          } else if (line.startsWith("data:")) {
            dataLines.push(line.slice(5).trim());
          }
        }
        if (!dataLines.length) continue;
        const dataStr = dataLines.join("\n");
        let parsed = null;
        try { parsed = JSON.parse(dataStr); } catch { parsed = dataStr; }
        if (currentEvent === "meta" && onMeta) onMeta(parsed);
        else if (currentEvent === "delta" && onDelta && parsed && typeof parsed.text === "string") onDelta(parsed.text);
        else if (currentEvent === "error") {
          const err = new Error((parsed && parsed.message) || "stream error");
          if (onError) onError(err);
          throw err;
        } else if (currentEvent === "done" && onDone) onDone(parsed);
      }
    }
  });
}
