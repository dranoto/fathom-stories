// frontend/js/state.js
let events = [];
let activeEventId = null;
let activeEventDetail = null;
let readArticleIds = new Set();
let inboxOpen = false;
let ungroupedArticles = [];
let inboxTotal = 0;
let inboxUnread = 0;
let inboxRead = 0;

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
export function getInboxCounts() { return { total: inboxTotal, read: inboxRead, unread: inboxUnread }; }
export function setInboxCounts(total, read, unread) {
  inboxTotal = total;
  inboxRead = read;
  inboxUnread = unread;
}

export function patchEventUnreadCount(articleId, eventId, isRead) {
  const delta = isRead ? -1 : 1;
  if (eventId == null) {
    for (let i = 0; i < ungroupedArticles.length; i++) {
      if (ungroupedArticles[i].id === articleId) {
        ungroupedArticles[i].is_read = isRead;
        break;
      }
    }
    inboxUnread = Math.max(0, inboxUnread + delta);
    inboxRead = Math.max(0, inboxRead - delta);
    return;
  }
  for (let i = 0; i < events.length; i++) {
    if (events[i].id === eventId) {
      const cur = events[i];
      cur.unread_count = Math.max(0, (cur.unread_count || 0) + delta);
      cur.read_count = Math.max(0, (cur.read_count || 0) - delta);
      break;
    }
  }
  if (
    activeEventDetail &&
    activeEventDetail.id === eventId &&
    Array.isArray(activeEventDetail.articles)
  ) {
    for (let i = 0; i < activeEventDetail.articles.length; i++) {
      if (activeEventDetail.articles[i].id === articleId) {
        activeEventDetail.articles[i].is_read = isRead;
        break;
      }
    }
  }
}

export function dispatchReadStateChanged(articleId, eventId, isRead) {
  window.dispatchEvent(new CustomEvent("read-state-changed", {
    detail: { articleId, eventId, isRead },
  }));
}

export function dispatchReaderClosed() {
  window.dispatchEvent(new CustomEvent("reader-closed"));
}
