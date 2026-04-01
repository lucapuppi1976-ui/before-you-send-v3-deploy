const CACHE = 'bys-v2-multilingual-locked-v1';
const ASSETS = [
  './',
  './index.html',
  './styles.css',
  './app.js',
  './manifest.webmanifest',
  './assets/icon-192.png',
  './assets/icon-512.png',
  './assets/chat_demo_final.png',
  './assets/voice_note_demo_27s.wav',
  './locales/it.json',
  './locales/en.json',
  './locales/es.json'
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;
  event.respondWith(
    caches.match(event.request).then(hit => hit || fetch(event.request).then(res => {
      const copy = res.clone();
      caches.open(CACHE).then(cache => cache.put(event.request, copy));
      return res;
    }).catch(() => caches.match('./index.html')))
  );
});
