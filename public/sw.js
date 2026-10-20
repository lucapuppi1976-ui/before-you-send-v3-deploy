// Network first for the app shell (navigations, HTML, JS, CSS, locales), with the cache as
// the offline fallback; cache first only for icons and images. /api/ and /admin/ are never
// cached, and neither is any non-OK response. The 2.6 MB demo voice note is not precached:
// the audio element loads it on demand.
const CACHE = 'bys-v4-claude-vps-1';
const PRECACHE = [
  './',
  './index.html',
  './styles.css',
  './app.js',
  './manifest.webmanifest',
  './locales/it.json',
  './locales/en.json',
  './locales/es.json',
  './assets/icon-192.png',
  './assets/icon-512.png',
  './assets/chat_demo_final.png',
];
const SHELL_RE = /\.(?:html|js|css|json|webmanifest)$/i;
const IMAGE_RE = /\.(?:png|jpe?g|gif|webp|svg|ico)$/i;

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

function cacheable(response) {
  // 200 only: partial (206) audio ranges and errors are never stored.
  return response && response.status === 200 && response.type === 'basic';
}

async function networkFirst(event) {
  const { request } = event;
  const cache = await caches.open(CACHE);
  try {
    const response = await fetch(request);
    if (cacheable(response)) event.waitUntil(cache.put(request, response.clone()));
    return response;
  } catch (error) {
    const cached = await cache.match(request, { ignoreSearch: request.mode === 'navigate' });
    if (cached) return cached;
    if (request.mode === 'navigate') {
      const shell = (await cache.match('./index.html')) || (await cache.match('./'));
      if (shell) return shell;
    }
    throw error;
  }
}

async function cacheFirst(event) {
  const { request } = event;
  const cache = await caches.open(CACHE);
  const cached = await cache.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (cacheable(response)) event.waitUntil(cache.put(request, response.clone()));
  return response;
}

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/') || url.pathname === '/admin' || url.pathname.startsWith('/admin/')) return;

  if (request.mode === 'navigate' || url.pathname === '/' || SHELL_RE.test(url.pathname)) {
    event.respondWith(networkFirst(event));
    return;
  }
  if (request.destination === 'image' || IMAGE_RE.test(url.pathname)) {
    event.respondWith(cacheFirst(event));
  }
  // Everything else (such as the demo voice note, fetched with range requests) goes to the network.
});
