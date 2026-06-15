// frontend/script.js
import {
  listEvents, stats, runGrouping, listReadArticleIds, listUngroupedArticles,
} from "./js/apiService.js";
import {
  setEvents, getEvents, setReadIds, setActiveEventId,
  getActiveEventId, setInboxOpen, getInboxOpen, setInboxCounts,
} from "./js/state.js";
import { renderEventTabs } from "./js/eventTabs.js";
import { renderActiveEventPane, renderInboxPane } from "./js/timeline.js";
import { setupReader } from "./js/reader.js";
import { setupSearch } from "./js/search.js";
import { loadTheme, setupThemeButton } from "./js/theme.js";
import { startCountdowns } from "./js/countdowns.js";
import { setupMobileMenu, renderMobileMenu } from "./js/mobileMenu.js";
import { registerServiceWorker } from "./js/pwa.js";
import { setupSwipeNav } from "./js/swipeNav.js";
import { selectEventTab, selectInboxTab } from "./js/tabActions.js";

async function refreshEvents() {
  let all = [];
  try {
    all = await listEvents({ minArticles: 2, status: "active" });
    setEvents(all);
  } catch (e) {
    setStatus("error", `load failed: ${e.message}`);
    return;
  }
  renderEventTabs(onTabSelect, onInboxSelect);

  const activeId = getActiveEventId();
  if (getInboxOpen()) {
    await renderInboxPane();
  } else if (activeId && all.some(e => e.id === activeId)) {
    await renderActiveEventPane(activeId);
  } else if (all.length > 0) {
    setActiveEventId(all[0].id);
    renderEventTabs(onTabSelect, onInboxSelect);
    await renderActiveEventPane(all[0].id);
  } else {
    setInboxOpen(true);
    renderEventTabs(onTabSelect, onInboxSelect);
    await renderInboxPane();
  }
}

async function onTabSelect(eventId) {
  await selectEventTab(eventId);
}

async function onInboxSelect() {
  await selectInboxTab();
}

function setStatus(kind, text) {
  const dot = document.getElementById("status-dot");
  const t = document.getElementById("status-text");
  dot.className = "dot " + (kind === "ok" ? "ok" : kind === "warn" ? "warn" : kind === "error" ? "error" : "");
  t.textContent = text;
}

async function refreshStats() {
  try {
    const s = await stats();
    const coolingPart = s.events_cooling > 0 ? ` · ${s.events_cooling} cooling` : "";
    setStatus(
      "ok",
      `${s.articles_total} articles · ${s.articles_ungrouped} in inbox · ${s.events_active} active${coolingPart}`
    );
  } catch (e) {
    setStatus("error", e.message);
  }
}

async function refreshReadIds() {
  try {
    const ids = await listReadArticleIds();
    setReadIds(ids);
  } catch (e) {
    console.warn("read ids fetch failed:", e);
  }
}

async function refreshInboxCounts() {
  try {
    const articles = await listUngroupedArticles();
    const total = articles.length;
    const read = articles.filter(a => a.is_read).length;
    const unread = total - read;
    setInboxCounts(total, read, unread);
    renderEventTabs(onTabSelect, onInboxSelect);
  } catch (e) {
    console.warn("inbox counts fetch failed:", e);
  }
}

async function afterTimerFiredRefresh() {
  await refreshEvents();
  await refreshReadIds();
  await refreshInboxCounts();
  try { await runGrouping(); } catch (_) { /* ok if no LLM key */ }
  await refreshEvents();
  await refreshInboxCounts();
  await refreshStats();
}

async function afterTimerFiredRegroup() {
  await refreshEvents();
  await refreshInboxCounts();
  await refreshReadIds();
  await refreshStats();
}

async function handleSwipeNav(direction) {
  const events = getEvents() || [];
  const allTabs = [...events.map(e => ({ kind: "event", id: e.id })), { kind: "inbox" }];
  if (allTabs.length < 2) return;
  const currentEventId = getActiveEventId();
  const inboxOpen = getInboxOpen();
  let currentIdx;
  if (inboxOpen) {
    currentIdx = allTabs.length - 1;
  } else if (currentEventId) {
    currentIdx = allTabs.findIndex(t => t.kind === "event" && t.id === currentEventId);
    if (currentIdx < 0) currentIdx = 0;
  } else {
    currentIdx = 0;
  }
  let nextIdx;
  if (direction === "next") {
    nextIdx = (currentIdx + 1) % allTabs.length;
  } else {
    nextIdx = (currentIdx - 1 + allTabs.length) % allTabs.length;
  }
  const target = allTabs[nextIdx];
  if (target.kind === "inbox") {
    onInboxSelect();
  } else {
    onTabSelect(target.id);
  }
}

async function bootstrap() {
  loadTheme();
  setupThemeButton();
  setupReader();
  setupSearch();
  setupMobileMenu(() => renderMobileMenu({ onRunAfterRefresh: afterTimerFiredRefresh }));
  registerServiceWorker();

  const main = document.querySelector(".app-main");
  setupSwipeNav(main);
  window.addEventListener("swipe-nav", (e) => {
    const direction = e.detail && e.detail.direction;
    if (!direction) return;
    handleSwipeNav(direction);
  });

  await startCountdowns({
    onRefreshComplete: afterTimerFiredRefresh,
    onRegroupComplete: afterTimerFiredRegroup,
  });

  window.addEventListener("article-moved", async () => {
    await refreshEvents();
    await refreshInboxCounts();
    await refreshReadIds();
    await refreshStats();
  });

  window.addEventListener("article-removed", async (e) => {
    await refreshEvents();
    await refreshInboxCounts();
    await refreshReadIds();
    await refreshStats();
  });

  window.addEventListener("read-state-changed", () => {
    renderEventTabs(onTabSelect, onInboxSelect);
  });

  window.addEventListener("reader-closed", async () => {
    await refreshReadIds();
    await refreshEvents();
    await refreshInboxCounts();
  });

  await refreshReadIds();
  await refreshInboxCounts();
  await refreshEvents();
  await refreshStats();
}

bootstrap();
