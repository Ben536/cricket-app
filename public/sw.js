/**
 * CricketRadar Service Worker
 *
 * Offline caching for the PWA (phone at the nets, flaky or no WiFi).
 *
 * Strategy:
 * - HTML/navigation: NETWORK-FIRST. Serving stale-while-revalidate HTML was
 *   the "white screen after deploy" bug: cached HTML referenced hashed JS
 *   bundles that the new deploy had purged. Fresh HTML when online, cached
 *   HTML only when offline.
 * - Hashed build assets (/assets/*): CACHE-FIRST. Vite content-hashes them,
 *   so a cached copy is immutable - no revalidation needed.
 * - Everything else same-origin: stale-while-revalidate.
 * - Cross-origin (fonts CDN etc.): cache opaque responses too, so the
 *   render-blocking Google Fonts stylesheet doesn't stall first paint on a
 *   dead network at the nets.
 */

const CACHE_NAME = 'cricketradar-v2';

// App shell cached on install
const STATIC_ASSETS = [
  '/',
  '/index.html',
  '/manifest.json',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) =>
      Promise.all(
        cacheNames
          .filter((name) => name !== CACHE_NAME)
          .map((name) => caches.delete(name))
      )
    )
  );
  self.clients.claim();
});

async function cachePut(request, response) {
  try {
    const cache = await caches.open(CACHE_NAME);
    await cache.put(request, response);
  } catch {
    // Quota/opaque errors are non-fatal
  }
}

/** Network-first: fresh when online, cache as fallback. */
async function networkFirst(request) {
  try {
    const response = await fetch(request);
    if (response.ok) cachePut(request, response.clone());
    return response;
  } catch {
    const cached = await caches.match(request);
    if (cached) return cached;
    if (request.mode === 'navigate') {
      const shell = await caches.match('/index.html');
      if (shell) return shell;
    }
    return new Response('Offline', { status: 503 });
  }
}

/** Cache-first: for immutable content-hashed assets. */
async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;
  try {
    const response = await fetch(request);
    // Cache OK responses AND opaque cross-origin ones (fonts CDN)
    if (response.ok || response.type === 'opaque') {
      cachePut(request, response.clone());
    }
    return response;
  } catch {
    return new Response('Offline', { status: 503 });
  }
}

/** Stale-while-revalidate: fast, refreshed in the background. */
async function staleWhileRevalidate(request) {
  const cached = await caches.match(request);
  const refresh = fetch(request)
    .then((response) => {
      if (response.ok || response.type === 'opaque') {
        cachePut(request, response.clone());
      }
      return response;
    })
    .catch(() => cached || new Response('Offline', { status: 503 }));
  return cached || refresh;
}

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);

  // Never intercept the WebSocket upgrade or dynamic API data
  if (url.pathname.startsWith('/api/')) return;

  if (request.mode === 'navigate' || url.pathname === '/index.html') {
    event.respondWith(networkFirst(request));
  } else if (url.origin === self.location.origin && url.pathname.startsWith('/assets/')) {
    event.respondWith(cacheFirst(request));
  } else if (url.origin !== self.location.origin) {
    // Fonts/CDN: cache-first so offline first paint doesn't stall
    event.respondWith(cacheFirst(request));
  } else {
    event.respondWith(staleWhileRevalidate(request));
  }
});
