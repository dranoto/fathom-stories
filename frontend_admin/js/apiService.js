// frontend_admin/js/apiService.js
const API = "";  // same origin — admin frontend served by the admin API on its own port

async function handleFetch(url, options = {}) {
  const res = await fetch(API + url, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status}: ${text}`);
  }
  if (res.status === 204) return null;
  return res.json();
}

export const listEvents = (status) => handleFetch(`/api/events${status ? `?status=${status}` : ""}`);
export const listAllEvents = () => handleFetch(`/api/events`);
export const getEvent = (id) => handleFetch(`/api/events/${id}`);
export const createEvent = (name, description) => handleFetch(`/api/events`, {
  method: "POST", body: JSON.stringify({ name, description }),
});
export const updateEvent = (id, body) => handleFetch(`/api/events/${id}`, {
  method: "PUT", body: JSON.stringify(body),
});
export const deleteEvent = (id) => handleFetch(`/api/events/${id}`, { method: "DELETE" });
export const addArticleToEvent = (eventId, articleId) =>
  handleFetch(`/api/events/${eventId}/articles/${articleId}`, { method: "POST" });
export const removeArticleFromEvent = (eventId, articleId) =>
  handleFetch(`/api/events/${eventId}/articles/${articleId}`, { method: "DELETE" });
export const moveArticle = (eventId, articleId, body) =>
  handleFetch(`/api/events/${eventId}/articles/${articleId}/move`, {
    method: "POST", body: JSON.stringify(body),
  });
export const mergeEvents = (eventId, otherId, note) =>
  handleFetch(`/api/events/${eventId}/merge/${otherId}`, {
    method: "POST", body: JSON.stringify({ other_event_id: otherId, note }),
  });
export const splitEvent = (eventId, body) =>
  handleFetch(`/api/events/${eventId}/split`, {
    method: "POST", body: JSON.stringify(body),
  });
export const reviveEvent = (eventId) =>
  handleFetch(`/api/events/${eventId}/revive`, { method: "POST" });

export const listArticles = (params) => {
  const qs = new URLSearchParams(params).toString();
  return handleFetch(`/api/articles${qs ? "?" + qs : ""}`);
};
export const searchArticles = (keyword) =>
  handleFetch(`/api/events/search/articles?keyword=${encodeURIComponent(keyword)}`);

export const listProposals = (pendingOnly = true) =>
  handleFetch(`/api/admin/proposals?pending_only=${pendingOnly}`);
export const applyProposal = (id) =>
  handleFetch(`/api/admin/proposals/${id}/apply`, { method: "POST" });
export const dismissProposal = (id) =>
  handleFetch(`/api/admin/proposals/${id}`, { method: "DELETE" });

export const listFeedback = (limit = 50) =>
  handleFetch(`/api/admin/feedback?limit=${limit}`);

export const runGrouping = () => handleFetch(`/api/grouping/run`, { method: "POST" });
export const runRecluster = (autoApply = false) =>
  handleFetch(`/api/grouping/recluster?auto_apply=${autoApply}`, { method: "POST" });
export const runLifecycle = () => handleFetch(`/api/grouping/lifecycle`, { method: "POST" });

export const stats = () => handleFetch(`/api/events/_stats/all`);
