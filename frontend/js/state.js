// frontend/js/state.js
let events = [];
let activeEventId = null;
let activeEventDetail = null;
let readArticleIds = new Set();
let inboxOpen = false;
let minorDrawerOpen = false;
let ungroupedArticles = [];
let inboxTotal = 0;
let inboxUnread = 0;
let inboxRead = 0;
let currentArticleId = null;
let regeneratingEventIds = new Set();
let regeneratingTimers = new Map();

const SORT_MODE_KEY = "fathom.sortMode";
const SCORE_KNOBS_KEY = "fathom.scoreKnobs";

const DEFAULT_SORT_MODE = "normal";
const DEFAULT_SCORE_KNOBS = {
  base: 2.0,
  halfLifeHours: 8.0,
  importanceFloor: 0.5,
  magnitudeCap: 6.0,
};

function _readSortMode() {
  try {
    const v = localStorage.getItem(SORT_MODE_KEY);
    return v === "score" || v === "normal" ? v : DEFAULT_SORT_MODE;
  } catch (_) { return DEFAULT_SORT_MODE; }
}
function _readScoreKnobs() {
  try {
    const raw = localStorage.getItem(SCORE_KNOBS_KEY);
    if (!raw) return { ...DEFAULT_SCORE_KNOBS };
    const parsed = JSON.parse(raw);
    const out = { ...DEFAULT_SCORE_KNOBS };
    for (const k of Object.keys(DEFAULT_SCORE_KNOBS)) {
      const n = Number(parsed[k]);
      if (Number.isFinite(n)) out[k] = n;
    }
    return out;
  } catch (_) { return { ...DEFAULT_SCORE_KNOBS }; }
}

let sortMode = _readSortMode();
let scoreKnobs = _readScoreKnobs();

export function getSortMode() { return sortMode; }
export function setSortMode(mode) {
  if (mode !== "score" && mode !== "normal") return;
  if (sortMode === mode) return;
  sortMode = mode;
  try { localStorage.setItem(SORT_MODE_KEY, mode); } catch (_) {}
  window.dispatchEvent(new CustomEvent("sort-mode-changed", { detail: { mode } }));
}
export function getScoreKnobs() { return { ...scoreKnobs }; }
export function setScoreKnobs(next) {
  const merged = { ...scoreKnobs, ...next };
  for (const k of Object.keys(DEFAULT_SCORE_KNOBS)) {
    const n = Number(merged[k]);
    if (Number.isFinite(n)) merged[k] = n;
    else merged[k] = DEFAULT_SCORE_KNOBS[k];
  }
  scoreKnobs = merged;
  try { localStorage.setItem(SCORE_KNOBS_KEY, JSON.stringify(scoreKnobs)); } catch (_) {}
  window.dispatchEvent(new CustomEvent("score-knobs-changed", { detail: { knobs: { ...scoreKnobs } } }));
}
export function resetScoreKnobs() {
  setScoreKnobs({ ...DEFAULT_SCORE_KNOBS });
}
export const SCORE_DEFAULTS = { ...DEFAULT_SCORE_KNOBS };

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
export function getMinorDrawerOpen() { return minorDrawerOpen; }
export function setMinorDrawerOpen(v) {
  if (minorDrawerOpen === v) return;
  minorDrawerOpen = v;
  window.dispatchEvent(new CustomEvent("minor-drawer-toggled", { detail: { open: v } }));
}
export function getUngroupedArticles() { return ungroupedArticles; }
export function setUngroupedArticles(v) { ungroupedArticles = v; }
export function getInboxCounts() { return { total: inboxTotal, read: inboxRead, unread: inboxUnread }; }
export function setInboxCounts(total, read, unread) {
  inboxTotal = total;
  inboxRead = read;
  inboxUnread = unread;
}
export function getCurrentArticleId() { return currentArticleId; }
export function setCurrentArticleId(id) {
  if (currentArticleId === id) return;
  currentArticleId = id;
  window.dispatchEvent(new CustomEvent("current-article-changed", {
    detail: { articleId: id },
  }));
}
export function isEventRegenerating(eventId) { return regeneratingEventIds.has(eventId); }
export function markEventRegenerating(eventId, durationMs = 60000) {
  regeneratingEventIds.add(eventId);
  if (regeneratingTimers.has(eventId)) {
    clearTimeout(regeneratingTimers.get(eventId));
  }
  regeneratingTimers.set(eventId, setTimeout(() => {
    regeneratingEventIds.delete(eventId);
    regeneratingTimers.delete(eventId);
    window.dispatchEvent(new CustomEvent("regenerating-events-changed"));
  }, durationMs));
  window.dispatchEvent(new CustomEvent("regenerating-events-changed"));
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

const SEEN_EVENTS_KEY = "fathom.seenEventIds";
function _readSeenEventIds() {
  try {
    const raw = localStorage.getItem(SEEN_EVENTS_KEY);
    if (!raw) return new Set();
    const arr = JSON.parse(raw);
    return new Set(Array.isArray(arr) ? arr.filter(n => Number.isFinite(n)) : []);
  } catch (_) { return new Set(); }
}
function _writeSeenEventIds(set) {
  try { localStorage.setItem(SEEN_EVENTS_KEY, JSON.stringify([...set])); } catch (_) {}
}

export function getSeenEventIds() { return _readSeenEventIds(); }
export function markEventSeen(eventId) {
  const id = Number(eventId);
  if (!Number.isFinite(id)) return;
  const set = _readSeenEventIds();
  if (set.has(id)) return;
  set.add(id);
  _writeSeenEventIds(set);
  window.dispatchEvent(new CustomEvent("event-seen-changed", { detail: { eventId: id } }));
}
export function resetSeenEventIds() {
  try { localStorage.removeItem(SEEN_EVENTS_KEY); } catch (_) {}
  window.dispatchEvent(new CustomEvent("event-seen-changed", { detail: { reset: true } }));
}
