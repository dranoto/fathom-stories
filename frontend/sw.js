// fathom-stories service worker
const CACHE_VERSION = 'v3';
const SHELL_CACHE = `fathom-shell-${CACHE_VERSION}`;
const RUNTIME_CACHE = `fathom-runtime-${CACHE_VERSION}`;

const PRECACHE_URLS = [
  '/',
  '/static/manifest.webmanifest',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => cache.addAll(PRECACHE_URLS))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => !k.endsWith(CACHE_VERSION))
          .map((k) => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);

  if (url.pathname === '/' || url.pathname.startsWith('/static/')) {
    event.respondWith(cacheFirst(req, SHELL_CACHE));
    return;
  }
  if (url.pathname.startsWith('/api/articles/') ||
      url.pathname.startsWith('/api/events?') ||
      url.pathname === '/api/events') {
    event.respondWith(networkFirst(req, RUNTIME_CACHE));
    return;
  }
  if (url.pathname.startsWith('/api/events/')) {
    event.respondWith(staleWhileRevalidate(req, RUNTIME_CACHE));
    return;
  }
});

async function cacheFirst(req, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(req);
  if (cached) {
    fetchAndUpdate(req, cache);
    return cached;
  }
  try {
    const resp = await fetch(req);
    if (resp.ok) cache.put(req, resp.clone());
    return resp;
  } catch (e) {
    return new Response('offline', { status: 503, statusText: 'offline' });
  }
}

async function networkFirst(req, cacheName) {
  const cache = await caches.open(cacheName);
  try {
    const resp = await fetch(req);
    if (resp.ok) cache.put(req, resp.clone());
    return resp;
  } catch (e) {
    const cached = await cache.match(req);
    if (cached) return cached;
    return new Response('offline', { status: 503, statusText: 'offline' });
  }
}

async function staleWhileRevalidate(req, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(req);
  const fetchPromise = fetch(req).then((resp) => {
    if (resp.ok) cache.put(req, resp.clone());
    return resp;
  }).catch(() => cached);
  return cached || fetchPromise || new Response('offline', { status: 503 });
}

async function fetchAndUpdate(req, cache) {
  try {
    const resp = await fetch(req);
    if (resp.ok) cache.put(req, resp.clone());
  } catch (e) { /* offline */ }
}

self.addEventListener('message', (event) => {
  const data = event.data || {};
  if (data.type === 'CLEAR_RUNTIME_CACHE') {
    event.waitUntil(
      caches.delete(RUNTIME_CACHE).then(() => {
        if (event.source && event.source.postMessage) {
          event.source.postMessage({ type: 'RUNTIME_CACHE_CLEARED' });
        }
      })
    );
  }
});
