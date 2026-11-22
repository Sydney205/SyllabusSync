/* SyllabusSync service worker.
 *
 * Served from /sw.js (so it controls the whole site). The server replaces
 * __VERSION__ with a hash of the static files, so every deploy installs a
 * fresh worker and clears old caches.
 *
 * Strategy:
 *   - Public pages (/):     network first, saved copy when offline.
 *   - /api/events:          network first, saved copy when offline.
 *   - /static + fonts:      cache first, refreshed in the background.
 *   - Admin, login, POSTs:  never cached (always live).
 */
const VERSION = "__VERSION__";
const STATIC_CACHE = `ss-static-${VERSION}`;
const PAGE_CACHE = `ss-pages-${VERSION}`;
const PRECACHE = __PRECACHE__;
const NEVER_CACHE = ["/admin", "/login", "/logout", "/events", "/telegram", "/health", "/sw.js"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) =>
      // Add one by one so a single failure doesn't abort the install.
      Promise.all(PRECACHE.map((url) => cache.add(url).catch(() => null)))
    ).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k.startsWith("ss-") && k !== STATIC_CACHE && k !== PAGE_CACHE)
            .map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

async function networkFirst(request, cacheName, fallbackUrl) {
  const cache = await caches.open(cacheName);
  try {
    const response = await fetch(request);
    if (response.ok) cache.put(request, response.clone());
    return response;
  } catch (err) {
    const cached = (await cache.match(request)) || (await cache.match(request, { ignoreSearch: true }));
    if (cached) return cached;
    if (fallbackUrl) {
      const fallback = await caches.match(fallbackUrl);
      if (fallback) return fallback;
    }
    throw err;
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(STATIC_CACHE);
  const cached = await cache.match(request);
  const refresh = fetch(request)
    .then((response) => {
      if (response.ok || response.type === "opaque") cache.put(request, response.clone());
      return response;
    })
    .catch(() => cached);
  return cached || refresh;
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  const url = new URL(request.url);

  if (url.origin === self.location.origin) {
    if (NEVER_CACHE.some((p) => url.pathname === p || url.pathname.startsWith(p + "/"))) {
      if (request.mode === "navigate") {
        // Admin pages need the network; show the offline page instead of a browser error.
        event.respondWith(fetch(request).catch(() => caches.match("/offline")));
      }
      return;
    }
    if (request.mode === "navigate") {
      event.respondWith(networkFirst(request, PAGE_CACHE, "/offline"));
      return;
    }
    if (url.pathname.startsWith("/static/") || url.pathname === "/manifest.webmanifest") {
      event.respondWith(cacheFirst(request));
      return;
    }
    if (url.pathname === "/api/events") {
      event.respondWith(networkFirst(request, PAGE_CACHE));
    }
    return;
  }

  if (url.hostname === "fonts.googleapis.com" || url.hostname === "fonts.gstatic.com") {
    event.respondWith(cacheFirst(request));
  }
});
