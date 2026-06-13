// frontend/js/countdowns.js
import { runFetch, runRegroup } from "./apiService.js";

const REFRESH_MS = 60 * 60 * 1000;
const REGROUP_MS = 60 * 60 * 1000;
const REGROUP_OFFSET_MS = 30 * 60 * 1000;

let refreshDeadline = Date.now() + REFRESH_MS;
let regroupDeadline = Date.now() + REGROUP_OFFSET_MS;
let running = { refresh: false, regroup: false };
let timerHandle = null;
let onCompleteCallbacks = { refresh: [], regroup: [] };

function formatMs(ms) {
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
  const refreshMs = refreshDeadline - now;
  const regroupMs = regroupDeadline - now;

  const refreshEl = document.getElementById("chip-refresh-time");
  const regroupEl = document.getElementById("chip-regroup-time");
  const refreshChip = document.getElementById("chip-refresh");
  const regroupChip = document.getElementById("chip-regroup");

  if (refreshEl) refreshEl.textContent = running.refresh ? "running…" : formatMs(refreshMs);
  if (regroupEl) regroupEl.textContent = running.regroup ? "running…" : formatMs(regroupMs);
  if (refreshChip) {
    refreshChip.classList.toggle("running", running.refresh);
    refreshChip.classList.toggle("due", !running.refresh && refreshMs < 60_000 && refreshMs >= 0);
  }
  if (regroupChip) {
    regroupChip.classList.toggle("running", running.regroup);
    regroupChip.classList.toggle("due", !running.regroup && regroupMs < 60_000 && regroupMs >= 0);
  }
}

async function fireRefresh(triggeredManually) {
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
    refreshDeadline = Date.now() + REFRESH_MS;
    tick();
  }
}

async function fireRegroup(triggeredManually) {
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
    regroupDeadline = Date.now() + REGROUP_MS;
    tick();
  }
}

function checkDeadlines() {
  const now = Date.now();
  if (!running.refresh && now >= refreshDeadline) fireRefresh(false);
  if (!running.regroup && now >= regroupDeadline) fireRegroup(false);
}

export function startCountdowns({ onRefreshComplete, onRegroupComplete } = {}) {
  if (onRefreshComplete) onCompleteCallbacks.refresh.push(onRefreshComplete);
  if (onRegroupComplete) onCompleteCallbacks.regroup.push(onRegroupComplete);

  if (timerHandle) return;
  tick();
  timerHandle = setInterval(() => {
    tick();
    checkDeadlines();
  }, 1000);

  const refreshChip = document.getElementById("chip-refresh");
  const regroupChip = document.getElementById("chip-regroup");
  if (refreshChip) {
    refreshChip.style.cursor = "pointer";
    refreshChip.addEventListener("click", () => fireRefresh(true));
  }
  if (regroupChip) {
    regroupChip.style.cursor = "pointer";
    regroupChip.addEventListener("click", () => fireRegroup(true));
  }
}

export function resetRefreshTimer() {
  refreshDeadline = Date.now() + REFRESH_MS;
  tick();
}

export function resetRegroupTimer() {
  regroupDeadline = Date.now() + REGROUP_MS;
  tick();
}
