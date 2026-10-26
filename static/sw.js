// Minimaler Service Worker: App-Shell aus dem Cache, API immer live.
const CACHE = "growlog-v7";
const SHELL = ["/", "/static/style.css", "/static/app.js"];
self.addEventListener("install", (e) => e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())));
self.addEventListener("activate", (e) => e.waitUntil(
  caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim())));
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.pathname.startsWith("/api/")) return;
  if (url.pathname === "/login") return;
  e.respondWith(fetch(e.request).then((r) => {
    // nur echte Antworten cachen, keine Weiterleitungen zum Login
    if (r.ok && !r.redirected && r.type === "basic") {
      const copy = r.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy));
    }
    return r;
  }).catch(() => caches.match(e.request)));
});
