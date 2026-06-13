// frontend/js/pwa.js
const STORAGE_KEY = "fathom-stories:pwa-dismissed";
const DISMISS_DAYS = 7;

let deferredPrompt = null;

window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  deferredPrompt = e;
  window.dispatchEvent(new CustomEvent("pwa-installable"));
});

window.addEventListener("appinstalled", () => {
  deferredPrompt = null;
  window.dispatchEvent(new CustomEvent("pwa-installed"));
});

function recentlyDismissed() {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (!v) return false;
    const ts = parseInt(v, 10);
    if (!ts) return false;
    return Date.now() - ts < DISMISS_DAYS * 24 * 3600 * 1000;
  } catch (_) { return false; }
}

export function getPwaInstallState() {
  return { canInstall: !!deferredPrompt && !recentlyDismissed() };
}

export async function installPwa() {
  if (!deferredPrompt) return;
  deferredPrompt.prompt();
  try {
    await deferredPrompt.userChoice;
  } catch (_) {}
  deferredPrompt = null;
}

export function dismissPwaInstall() {
  try { localStorage.setItem(STORAGE_KEY, String(Date.now())); } catch (_) {}
}

export async function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  try {
    await navigator.serviceWorker.register("/sw.js", { scope: "/" });
  } catch (e) {
    console.warn("service worker registration failed:", e);
  }
}
