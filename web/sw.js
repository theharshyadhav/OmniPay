'use strict';
// Service worker: makes OmniPay installable and lets the app shell open on a flaky connection.
// Network first for everything; the cache is only a fallback for the static shell. API calls (/v1/...) are never cached.
const CACHE = 'omnipay-shell-v1';
const SHELL = ['/', '/style.css', '/app.js', '/qr.js', '/scan.js', '/icon.svg', '/manifest.webmanifest',
  '/fonts/Inter-Regular.woff', '/fonts/Inter-Medium.woff', '/fonts/Inter-SemiBold.woff', '/fonts/adventor-regular.woff', '/fonts/adventor-bold.woff'];
self.addEventListener('install', (e) => { e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())); });
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', (e) => {
  const u = new URL(e.request.url);
  if (e.request.method !== 'GET' || u.origin !== self.location.origin || u.pathname.startsWith('/v1/') || u.pathname === '/metrics' || u.pathname === '/healthz') return;
  e.respondWith(fetch(e.request).then((r) => {
    if (r.ok && SHELL.includes(u.pathname)) { const copy = r.clone(); caches.open(CACHE).then((c) => c.put(u.pathname, copy)); }
    return r;
  }).catch(() => caches.match(u.pathname).then((m) => m || caches.match('/'))));
});
