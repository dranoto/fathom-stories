// frontend_admin/script.js
import { stats, runGrouping, runRecluster, runLifecycle, listProposals } from "./js/apiService.js";
import { renderEventList } from "./js/eventList.js";
import { renderUngrouped } from "./js/articleSearch.js";
import { renderProposals } from "./js/reclusterPanel.js";
import { renderFeedback } from "./js/feedback.js";

const REFRESH_MS = 15000;

let currentTab = "events";

async function showTab(name) {
  currentTab = name;
  document.querySelectorAll(".atab").forEach(t => {
    t.classList.toggle("active", t.dataset.tab === name);
  });
  const main = document.getElementById("admin-main");
  if (name === "events") await renderEventList(main);
  else if (name === "ungrouped") await renderUngrouped(main);
  else if (name === "proposals") await renderProposals(main);
  else if (name === "feedback") await renderFeedback(main);
}

async function refreshStats() {
  try {
    const s = await stats();
    document.getElementById("admin-stats").innerHTML = `
      <span class="stat"><strong>${s.articles_total}</strong>articles</span>
      <span class="stat"><strong>${s.articles_ungrouped}</strong>ungrouped</span>
      <span class="stat"><strong>${s.events_active}</strong>active</span>
      <span class="stat"><strong>${s.events_cooling}</strong>cooling</span>
      <span class="stat"><strong>${s.events_archived}</strong>archived</span>
      <span class="stat"><strong>${s.proposals_pending}</strong>proposals</span>
      <span class="stat"><strong>${s.feedback_count}</strong>feedback rows</span>
    `;
    document.getElementById("proposal-count").textContent = s.proposals_pending;
  } catch (e) {
    document.getElementById("admin-stats").innerHTML = `<span style="color: var(--error)">API error: ${e.message}</span>`;
  }
}

function toast(text, kind = "") {
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.textContent = text;
  document.getElementById("toasts").appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

async function bootstrap() {
  document.querySelectorAll(".atab").forEach(t => {
    t.addEventListener("click", () => showTab(t.dataset.tab));
  });

  document.getElementById("btn-run-grouping").addEventListener("click", async () => {
    const btn = document.getElementById("btn-run-grouping");
    btn.disabled = true;
    btn.textContent = "Running…";
    try {
      const r = await runGrouping();
      toast(`Grouping: ${JSON.stringify(r)}`, "success");
      await refreshStats();
      if (currentTab === "events" || currentTab === "ungrouped") await showTab(currentTab);
    } catch (e) { toast("Grouping failed: " + e.message, "error"); }
    btn.disabled = false;
    btn.textContent = "Run live grouping";
  });

  document.getElementById("btn-run-recluster").addEventListener("click", async () => {
    const btn = document.getElementById("btn-run-recluster");
    btn.disabled = true;
    btn.textContent = "Running…";
    try {
      const r = await runRecluster(false);
      toast(`Recluster: ${JSON.stringify(r)}`, "success");
      await refreshStats();
      if (currentTab === "proposals") await showTab(currentTab);
    } catch (e) { toast("Recluster failed: " + e.message, "error"); }
    btn.disabled = false;
    btn.textContent = "Run recluster";
  });

  document.getElementById("btn-run-lifecycle").addEventListener("click", async () => {
    const btn = document.getElementById("btn-run-lifecycle");
    btn.disabled = true;
    try {
      const r = await runLifecycle();
      toast(`Lifecycle: ${JSON.stringify(r)}`, "success");
      await refreshStats();
    } catch (e) { toast("Lifecycle failed: " + e.message, "error"); }
    btn.disabled = false;
  });

  await refreshStats();
  await showTab("events");
  setInterval(refreshStats, REFRESH_MS);
}

bootstrap();
