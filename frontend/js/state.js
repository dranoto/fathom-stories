// frontend/js/state.js
let events = [];
let activeEventId = null;
let activeEventDetail = null;
let readArticleIds = new Set();
let inboxOpen = false;
let ungroupedArticles = [];

export function getEvents() { return events; }
export function setEvents(v) { events = v; }
export function getActiveEventId() { return activeEventId; }
export function setActiveEventId(id) { activeEventId = id; }
export function getActiveEventDetail() { return activeEventDetail; }
export function setActiveEventDetail(v) { activeEventDetail = v; }
export function getReadArticleIds() { return readArticleIds; }
export function isRead(articleId) { return readArticleIds.has(articleId); }
export function markRead(articleId) { readArticleIds.add(articleId); }
export function markUnread(articleId) { readArticleIds.delete(articleId); }
export function setReadIds(ids) { readArticleIds = new Set(ids); }
export function getInboxOpen() { return inboxOpen; }
export function setInboxOpen(v) { inboxOpen = v; }
export function getUngroupedArticles() { return ungroupedArticles; }
export function setUngroupedArticles(v) { ungroupedArticles = v; }
