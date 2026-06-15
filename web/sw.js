/*
 * sw.js — service worker that makes the Klipsch web remote installable and
 * offline-capable. It precaches the app shell (HTML/JS/CSS/icons) and runtime-
 * caches the Google Fonts (Material Symbols + Roboto) so a second launch — and
 * an installed PWA launched with no network — renders fully, glyphs included.
 *
 * The BLE control itself never touches the network; this is purely about
 * loading the page assets without a round-trip.
 *
 * Strategy:
 *   - same-origin shell assets  -> cache-first (instant, offline)
 *   - navigations               -> network-first, fall back to cached index.html
 *   - Google Fonts (CSS + woff) -> cache-first into a runtime cache
 *
 * IMPORTANT: bump VERSION whenever any shelled file (index.html, app.js,
 * klipsch.js, styles.css, an icon, or this list) changes — the old cache is
 * keyed by it and is dropped on activate, forcing clients onto the new assets.
 */
"use strict";

const VERSION = "v6";
const SHELL_CACHE = `klipsch-shell-${VERSION}`;
const RUNTIME_CACHE = `klipsch-runtime-${VERSION}`;

// Everything needed to boot the app with no network. Relative URLs so the SW
// works whether the app is served from a domain root or a sub-path.
const SHELL = [
  "./",
  "./index.html",
  "./app.js",
  "./klipsch.js",
  "./styles.css",
  "./manifest.json",
  "./icon.png",
  "./icon-192.png",
  "./icon-512.png",
  "./maskable-512.png",
];

const FONT_ORIGINS = ["https://fonts.googleapis.com", "https://fonts.gstatic.com"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((k) => k !== SHELL_CACHE && k !== RUNTIME_CACHE)
            .map((k) => caches.delete(k))
        )
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  const url = new URL(request.url);

  // Navigations: prefer a fresh page when online (so a new deploy is picked up),
  // but fall back to the cached shell so the app still launches offline.
  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(() => caches.match("./index.html"))
    );
    return;
  }

  // Google Fonts — cache-first into the runtime cache (opaque responses are fine).
  if (FONT_ORIGINS.includes(url.origin)) {
    event.respondWith(cacheFirst(request, RUNTIME_CACHE));
    return;
  }

  // Same-origin shell assets — cache-first.
  if (url.origin === self.location.origin) {
    event.respondWith(cacheFirst(request, SHELL_CACHE));
  }
});

async function cacheFirst(request, cacheName) {
  const cached = await caches.match(request);
  if (cached) return cached;
  try {
    const response = await fetch(request);
    if (response && (response.ok || response.type === "opaque")) {
      const cache = await caches.open(cacheName);
      cache.put(request, response.clone());
    }
    return response;
  } catch (err) {
    // Offline and never cached — nothing we can do; let the request fail.
    return caches.match(request);
  }
}
