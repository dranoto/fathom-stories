// frontend/js/countdowns.js
import { runFetch, runRegroup } from "./apiService.js";

const POLL_MS = 60_000;

let refreshDeadline = null;
let regroupDeadline = null;
let running = { refresh: false, regroup: false };
let timerHandle = null;
let pollHandle = null;
let onCompleteCallbacks = { refresh: [], regroup: [] };
let lastFetchError = null;

function formatMs(ms) {
  if (ms === null || ms === undefined || isNaN(ms)) return "--:--";
  if (ms < 0) ms = 0;
  const total = Math.floor(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function tick() {
  const now = Date.now();
  const refreshMs = refreshDeadline !== null ? refreshDeadline - now : null;
  const regroupMs = regroupDeadline !== null ? regroupDeadline - now : null;

  const refreshEl = document.getElementById("chip-refresh-time");
  const regroupEl = document.getElementById("chip-regroup-time");
  const refreshChip = document.getElementById("chip-refresh");
  const regroupChip = document.getElementById("chip-regroup");

  if (refreshEl) refreshEl.textContent = running.refresh ? "running…" : formatMs(refreshMs);
  if (regroupEl) regroupEl.textContent = running.regroup ? "running…" : formatMs(regroupMs);
  if (refreshChip) {
    refreshChip.classList.toggle("running", running.refresh);
    refreshChip.classList.toggle("due", !running.refresh && refreshMs !== null && refreshMs < 60_000 && refreshMs >= 0);
    refreshChip.classList.toggle("error", !!lastFetchError);
  }
  if (regroupChip) {
    regroupChip.classList.toggle("running", running.regroup);
    regroupChip.classList.toggle("due", !running.regroup && regroupMs !== null && regroupMs < 60_000 && regroupMs >= 0);
  }
}

async function loadDeadlinesFromServer() {
  try {
    const res = await fetch("/api/grouping/schedule");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (data && data.jobs) {
      if (data.jobs.rss_fetch) {
        refreshDeadline = new Date(data.jobs.rss_fetch).getTime();
      }
      if (data.jobs.regroup_uncategorized) {
        regroupDeadline = new Date(data.jobs.regroup_uncategorized).getTime();
      }
      lastFetchError = null;
    }
  } catch (e) {
    console.warn("countdowns: failed to fetch server schedule", e);
    lastFetchError = e.message;
  }
  tick();
}

async function fireRefresh() {
  if (running.refresh) return;
  running.refresh = true;
  tick();
  try {
    await runFetch();
    for (const cb of onCompleteCallbacks.refresh) await cb();
  } catch (e) {
    console.warn("scheduled refresh failed:", e);
  } finally {
    running.refresh = false;
    await loadDeadlinesFromServer();
  }
}

async function fireRegroup() {
  if (running.regroup) return;
  running.regroup = true;
  tick();
  try {
    await runRegroup();
    for (const cb of onCompleteCallbacks.regroup) await cb();
  } catch (e) {
    console.warn("scheduled regroup failed:", e);
  } finally {
    running.regroup = false;
    await loadDeadlinesFromServer();
  }
}

async function checkDeadlines() {
  const now = Date.now();
  if (refreshDeadline !== null && !running.refresh && now >= refreshDeadline) {
    fireRefresh();
  }
  if (regroupDeadline !== null && !running.regroup && now >= regroupDeadline) {
    fireRegroup();
  }
}

export async function startCountdowns({ onRefreshComplete, onRegroupComplete } = {}) {
  if (onRefreshComplete) onCompleteCallbacks.refresh.push(onRefreshComplete);
  if (onRegroupComplete) onCompleteCallbacks.regroup.push(onRegroupComplete);

  if (timerHandle) return;
  await loadDeadlinesFromServer();
  tick();
  timerHandle = setInterval(() => {
    tick();
    checkDeadlines();
  }, 1000);

  pollHandle = setInterval(loadDeadlinesFromServer, POLL_MS);

  const refreshChip = document.getElementById("chip-refresh");
  const regroupChip = document.getElementById("chip-regroup");
  if (refreshChip) {
    refreshChip.style.cursor = "pointer";
    refreshChip.addEventListener("click", () => fireRefresh());
  }
  if (regroupChip) {
    regroupChip.style.cursor = "pointer";
    regroupChip.addEventListener("click", () => fireRegroup());
  }
}
