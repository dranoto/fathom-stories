// frontend/js/swBridge.js
function getRegistration() {
  if (!("serviceWorker" in navigator)) return Promise.resolve(null);
  return navigator.serviceWorker.ready.then(reg => reg).catch(() => null);
}

export async function clearRuntimeCache() {
  const reg = await getRegistration();
  if (!reg || !reg.active) return false;
  return new Promise((resolve) => {
    const channel = new MessageChannel();
    const t = setTimeout(() => resolve(false), 1500);
    channel.port1.onmessage = (e) => {
      clearTimeout(t);
      resolve(e.data && e.data.type === "RUNTIME_CACHE_CLEARED");
    };
    reg.active.postMessage({ type: "CLEAR_RUNTIME_CACHE" }, [channel.port2]);
  });
}
