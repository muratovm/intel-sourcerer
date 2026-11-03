"use strict";
// Network first: the server stays the source of truth, and the cache only answers when it can't be reached.
const CACHE = "intel-sourcerer-v1";
const SHELL = ["/reader.css", "/notes-editor.js", "/tag-editor.js", "/reader.js", "/focus.js", "/math.js", "/app.js", "/library.js", "/brand-icon.png", "/vendor/katex/katex.min.js", "/vendor/katex/auto-render.min.js"];
const NEVER = /^\/(guest|temporary|login|setup|account|auth|export|local-media|sw\.js|save)/;
const OFFLINE_API = /^\/api\/(state|article)$/;

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});
function offlineJSON(message) {
  return new Response(JSON.stringify({ error: message }), { status: 503, headers: { "Content-Type": "application/json" } });
}
async function networkFirst(request, url) {
  const cache = await caches.open(CACHE);
  // Every view of a page shares one entry, or each ?open= link would add a copy.
  const key = request.mode === "navigate" ? url.origin + url.pathname : request;
  try {
    const response = await fetch(request);
    if (response.ok && !response.redirected && response.type === "basic") await cache.put(key, response.clone());
    return response;
  } catch {
    const cached = (await cache.match(key)) || (request.mode === "navigate" ? await cache.match(url.origin + "/") : null);
    if (cached) return cached;
    if (url.pathname === "/api/article") return offlineJSON("You're offline, and this article isn't saved on this device yet.");
    if (url.pathname.startsWith("/api/")) return offlineJSON("You're offline.");
    return Response.error();
  }
}
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.origin !== location.origin) return;
  if (event.request.method !== "GET") {
    // Cached pages hold account data, so they never outlive the account that saved them.
    if (["/auth/logout", "/auth/login", "/auth/delete-account"].includes(url.pathname)) caches.delete(CACHE);
    else if (url.pathname.startsWith("/api/"))
      event.respondWith(fetch(event.request).catch(() => offlineJSON("You're offline.")));
    return;
  }
  if (NEVER.test(url.pathname)) return;
  if (url.pathname.startsWith("/api/") && !OFFLINE_API.test(url.pathname)) {
    event.respondWith(fetch(event.request).catch(() => offlineJSON("You're offline.")));
    return;
  }
  event.respondWith(networkFirst(event.request, url));
});
