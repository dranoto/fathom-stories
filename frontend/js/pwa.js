// frontend/js/pwa.js
let deferredPrompt = null;

window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  deferredPrompt = e;
});

window.addEventListener("appinstalled", () => {
  deferredPrompt = null;
});

export function getPwaInstallState() {
  return { canInstall: !!deferredPrompt };
}

export async function installPwa() {
  if (!deferredPrompt) return;
  deferredPrompt.prompt();
  try {
    await deferredPrompt.userChoice;
  } catch (_) {}
  deferredPrompt = null;
}

export async function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  try {
    await navigator.serviceWorker.register("/sw.js", { scope: "/" });
  } catch (e) {
    console.warn("service worker registration failed:", e);
  }
}
