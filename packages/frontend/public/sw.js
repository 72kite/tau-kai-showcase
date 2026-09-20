/**
 * Minimal service worker for the Project Archer PWA shell. Only caches the app shell itself
 * (HTML/JS/CSS/icons) so the installed app opens instantly on a slow/dropped Tailscale
 * connection - it never caches API responses. tau-core's bridge serves live state
 * (transcript, telemetry, approvals, lockdown status); serving any of that stale from a cache
 * would be actively misleading for a home security dashboard, so /api/ and any cross-origin
 * request (the bridge usually runs on a different host:port than the frontend) always goes
 * straight to the network, uncached.
 */

const STATIC_CACHE = 'tau-static-v1'

self.addEventListener('install', () => {
  self.skipWaiting()
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== STATIC_CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  )
})

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url)

  if (event.request.method !== 'GET') return
  if (url.pathname.startsWith('/api/') || url.origin !== self.location.origin) return

  // Stale-while-revalidate: serve the cached shell instantly, refresh the cache in the
  // background for next load.
  event.respondWith(
    caches.open(STATIC_CACHE).then(async (cache) => {
      const cached = await cache.match(event.request)
      const network = fetch(event.request)
        .then((response) => {
          if (response.ok) cache.put(event.request, response.clone())
          return response
        })
        .catch(() => cached)
      return cached || network
    })
  )
})
