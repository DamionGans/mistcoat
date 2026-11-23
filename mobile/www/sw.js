// Offline support: cache the app shell so Mistcoat works with no connection.
// Bump VERSION whenever any cached file changes.
const VERSION = "mistcoat-v0.1.0-watermark1";
const SHELL = [
  "./", "index.html", "platform.js", "styles.css", "platform.css", "app.js", "worker.js", "manifest.webmanifest",
  "engine/imaging.js", "engine/gpu.js", "engine/passes.js", "engine/presets.js", "engine/quality.js",
  "engine/pipeline.js", "engine/metadata.js", "engine/color.js", "engine/watermark.js",
  "icon-192.png", "icon-512.png", "icon-maskable-512.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) =>
    Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

// Network first, cache as fallback: online users always get the current
// version; offline users get the last one they loaded.
self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET" || new URL(e.request.url).origin !== location.origin) return;
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(VERSION).then((c) => c.put(e.request, copy));
        }
        return res;
      })
      .catch(() => caches.match(e.request).then((hit) => hit || caches.match("index.html"))),
  );
});
