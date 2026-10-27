// Network first for the app shell (navigations, HTML, JS, CSS, locales, free examples), with
// the cache as the offline fallback; cache first only for icons and images. /api/ and /admin/
// are never cached, and neither is any non-OK response. A demo recording (DEMO_VOICE in
// app.js, none for now) would not be precached: the audio element loads it on demand.
const CACHE = 'bys-v5-usability-5';
const PRECACHE = [
  './',
  './index.html',
  './styles.css',
  './app.js',
  './manifest.webmanifest',
  './locales/it.json',
  './locales/en.json',
  './locales/es.json',
  './locales/fr.json',
  './locales/de.json',
  './locales/ru.json',
  './samples/it.json',
  './samples/en.json',
  './samples/es.json',
  './samples/fr.json',
  './samples/de.json',
  './samples/ru.json',
  './assets/icon-192.png',
  './assets/icon-512.png',
  './assets/chat_demo_final.png',
];
const SHELL_RE = /\.(?:html|js|css|json|webmanifest)$/i;
const IMAGE_RE = /\.(?:png|jpe?g|gif|webp|svg|ico)$/i;
// «Share → Before You Send» (share_target in the manifest, Android): what was shared waits here until the page
// takes it (app.js, takeSharedContent), and never leaves the phone before the person taps «Analyze».
const SHARE_CACHE = 'bys-share';
const SHARE_PATH = '/share-target';

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE && key !== SHARE_CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

async function receiveShare(request) {
  try {
    const form = await request.formData();
    const file = form.getAll('file').find((item) => item && typeof item === 'object' && item.size > 0);
    const text = ['title', 'text', 'url']
      .map((name) => form.get(name))
      .filter((value) => typeof value === 'string' && value.trim())
      .join('\n');
    await caches.delete(SHARE_CACHE);
    const cache = await caches.open(SHARE_CACHE);
    const meta = { text, name: file ? file.name : '', type: file ? file.type : '' };
    await cache.put('/__shared/meta', new Response(JSON.stringify(meta), { headers: { 'Content-Type': 'application/json' } }));
    if (file) await cache.put('/__shared/file', new Response(file));
  } catch {
    // Nothing readable was shared: the app opens as usual.
  }
  return Response.redirect(new URL('/?shared=1', self.location.origin).href, 303);
}

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
  const url = new URL(request.url);
  if (request.method === 'POST' && url.origin === self.location.origin && url.pathname === SHARE_PATH) {
    event.respondWith(receiveShare(request));
    return;
  }
  if (request.method !== 'GET') return;
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/') || url.pathname === '/admin' || url.pathname.startsWith('/admin/')) return;

  if (request.mode === 'navigate' || url.pathname === '/' || SHELL_RE.test(url.pathname)) {
    event.respondWith(networkFirst(event));
    return;
  }
  if (request.destination === 'image' || IMAGE_RE.test(url.pathname)) {
    event.respondWith(cacheFirst(event));
  }
  // Everything else (such as audio, fetched with range requests) goes to the network.
});
