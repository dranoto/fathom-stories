// frontend/js/tabActions.js
import { markEventVisited } from "./apiService.js";
import { setInboxOpen, getInboxOpen, setActiveEventId, getActiveEventId, getEvents } from "./state.js";
import { closeReader } from "./reader.js";
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
  closeReader();
  markEventVisited(eventId).catch(() => {});
  renderEventTabs(selectEventTab, selectInboxTab);
  scrollActiveTabIntoView();
  await renderActiveEventPane(eventId);
}

export async function selectInboxTab() {
  setInboxOpen(true);
  setActiveEventId(null);
  closeReader();
  renderEventTabs(selectEventTab, selectInboxTab);
  scrollActiveTabIntoView();
  await renderInboxPane();
}

export function getCurrentTabKind() {
  if (getInboxOpen()) return "inbox";
  if (getActiveEventId()) return "event";
  const events = getEvents();
  if (events.length > 0) return "event";
  return "inbox";
}
