// frontend/js/tabActions.js
import { markEventVisited } from "./apiService.js";
import { setInboxOpen, getInboxOpen, setActiveEventId, getActiveEventId, getEvents, getUngroupedArticles } from "./state.js";
import { renderEventTabs } from "./eventTabs.js";
import { renderActiveEventPane, renderInboxPane } from "./timeline.js";

function scrollActiveTabIntoView() {
  requestAnimationFrame(() => {
    const active = document.querySelector("#event-tabs .event-tab.active");
    if (active && typeof active.scrollIntoView === "function") {
      active.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
    }
  });
}

export async function selectEventTab(eventId) {
  setInboxOpen(false);
  setActiveEventId(eventId);
  markEventVisited(eventId).catch(() => {});
  renderEventTabs(selectEventTab, selectInboxTab);
  scrollActiveTabIntoView();
  await renderActiveEventPane(eventId);
  window.dispatchEvent(new CustomEvent("open-summary", { detail: { eventId } }));
}

export async function selectInboxTab() {
  setInboxOpen(true);
  setActiveEventId(null);
  renderEventTabs(selectEventTab, selectInboxTab);
  scrollActiveTabIntoView();
  await renderInboxPane();
  const articles = getUngroupedArticles();
  if (articles.length > 0) {
    window.dispatchEvent(new CustomEvent("open-reader", { detail: { articleId: articles[0].id } }));
  }
}

export function getCurrentTabKind() {
  if (getInboxOpen()) return "inbox";
  if (getActiveEventId()) return "event";
  const events = getEvents();
  if (events.length > 0) return "event";
  return "inbox";
}

window.addEventListener("navigate-to-event", async (e) => {
  const eventId = e.detail && e.detail.eventId;
  if (!eventId) return;
  setInboxOpen(false);
  setActiveEventId(eventId);
  markEventVisited(eventId).catch(() => {});
  renderEventTabs(selectEventTab, selectInboxTab);
  scrollActiveTabIntoView();
  await renderActiveEventPane(eventId);
});
