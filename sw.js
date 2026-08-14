/* Offline shell for SG Car Scout. Bump CACHE when you edit index.html. */
var CACHE = "car-scout-v9";
var ASSETS = ["./", "./index.html", "./manifest.webmanifest", "./fx.css", "./fx.js", "./icon-192.png", "./icon-512.png", "./icon-180.png"];

self.addEventListener("install", function (e) {
  e.waitUntil(caches.open(CACHE).then(function (c) { return c.addAll(ASSETS); }).then(function(){ return self.skipWaiting(); }));
});
self.addEventListener("activate", function (e) {
  e.waitUntil(caches.keys().then(function (ks) {
    return Promise.all(ks.filter(function (k) { return k !== CACHE; }).map(function (k) { return caches.delete(k); }));
  }).then(function(){ return self.clients.claim(); }));
});
self.addEventListener("fetch", function (e) {
  var u = new URL(e.request.url);
  if (e.request.method !== "GET" || u.origin !== location.origin) return;
  e.respondWith(
    fetch(e.request).then(function (r) {
      return caches.open(CACHE).then(function (c) { c.put(e.request, r.clone()); return r; });
    }).catch(function () { return caches.match(e.request).then(function(m){ return m || caches.match("./index.html"); }); })
  );
});
