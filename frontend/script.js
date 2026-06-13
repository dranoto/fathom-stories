// frontend/script.js
import { listEvents, stats, runGrouping, listReadArticleIds } from "./js/apiService.js";
import { setEvents, setActiveEventId, setReadIds, getActiveEventId } from "./js/state.js";
import { renderEventTabs } from "./js/eventTabs.js";
import { renderActiveEventPane } from "./js/timeline.js";
import { setupReader } from "./js/reader.js";

const REFRESH_MS = 30000;

async function refreshEvents() {
  try {
    const [active, cooling] = await Promise.all([
      listEvents("active"),
      listEvents("cooling"),
    ]);
    const all = [...active, ...cooling].sort((a, b) => {
      const ta = a.last_article_at ? new Date(a.last_article_at).getTime() : 0;
      const tb = b.last_article_at ? new Date(b.last_article_at).getTime() : 0;
      return tb - ta;
    });
    setEvents(all);
    renderEventTabs(onTabSelect);

    const activeId = getActiveEventId();
    if (activeId && all.some(e => e.id === activeId)) {
      await renderActiveEventPane(activeId);
    } else if (all.length > 0) {
      setActiveEventId(all[0].id);
      await renderActiveEventPane(all[0].id);
    } else {
      document.getElementById("event-pane").innerHTML =
        `<div class="pane-empty">No active or cooling events. Run <code>fetch</code> + <code>group</code> in CLI, or click "Run grouping" below.</div>`;
    }
  } catch (e) {
    setStatus("error", `load failed: ${e.message}`);
  }
}

async function onTabSelect(eventId) {
  renderEventTabs(onTabSelect);
  await renderActiveEventPane(eventId);
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
    setStatus("ok", `${s.articles_total} articles · ${s.events_active} active · ${s.events_cooling} cooling · ${s.proposals_pending} proposals pending`);
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
