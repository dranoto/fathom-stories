// frontend/script.js
import {
  listEvents, stats, runGrouping, runRegroup, listReadArticleIds,
  listUngroupedArticles,
} from "./js/apiService.js";
import {
  setEvents, setActiveEventId, setReadIds, getActiveEventId,
  setInboxOpen, getInboxOpen,
} from "./js/state.js";
import { renderEventTabs } from "./js/eventTabs.js";
import { renderActiveEventPane, renderInboxPane } from "./js/timeline.js";
import { setupReader } from "./js/reader.js";

const REFRESH_MS = 30000;

async function refreshEvents() {
  let all = [];
  try {
    all = await listEvents({ minArticles: 2 });
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
    await renderActiveEventPane(all[0].id);
  } else {
    setInboxOpen(true);
    renderEventTabs(onTabSelect, onInboxSelect);
    await renderInboxPane();
  }
}

async function onTabSelect(eventId) {
  setInboxOpen(false);
  renderEventTabs(onTabSelect, onInboxSelect);
  await renderActiveEventPane(eventId);
}

async function onInboxSelect() {
  setInboxOpen(true);
  renderEventTabs(onTabSelect, onInboxSelect);
  await renderInboxPane();
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
    setStatus(
      "ok",
      `${s.articles_total} articles · ${s.articles_ungrouped} in inbox · ${s.events_active} active · ${s.events_cooling} cooling`
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

async function bootstrap() {
  setupReader();
  document.getElementById("btn-refresh").addEventListener("click", async () => {
    await refreshEvents();
    await refreshReadIds();
    await refreshStats();
  });
  document.getElementById("btn-regroup-top").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    const orig = btn.textContent;
    btn.textContent = "Regrouping…";
    try {
      await runRegroup();
      btn.textContent = "Reloading…";
      setTimeout(() => window.location.reload(), 400);
    } catch (err) {
      btn.disabled = false;
      btn.textContent = orig;
      alert("Regroup failed: " + err.message);
    }
  });
  setInterval(async () => {
    await refreshEvents();
    await refreshReadIds();
  }, REFRESH_MS);
  setInterval(refreshStats, 60000);

  await refreshReadIds();
  await refreshEvents();
  await refreshStats();
}

bootstrap();
