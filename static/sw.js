// inphAI's service worker: keeps the app's own files (the shell) so it opens fast and installs
// as an app. It never caches /api or /media: chats, images and videos always come from the server.
const VERSION = "__VERSION__";
const CACHE = `inphai-${VERSION}`;
const SHELL = ["/", "/static/style.css", "/static/markdown.js", "/static/manifest.webmanifest", "/static/icons/icon.svg"];

self.addEventListener("install", event => {
    // A shell file that fails (signed out: "/" redirects) must not stop the install.
    event.waitUntil(caches.open(CACHE).then(cache => Promise.all(SHELL.map(url =>
        fetch(url, { credentials: "same-origin" }).then(response => {
            if (response.ok && !response.redirected) return cache.put(url, response);
        }).catch(() => {})))).then(() => self.skipWaiting()));
});

self.addEventListener("activate", event => {
    event.waitUntil(caches.keys().then(keys => Promise.all(keys
        .filter(key => key.startsWith("inphai-") && key !== CACHE).map(key => caches.delete(key))))
        .then(() => self.clients.claim()));
});

function keep(request, response) {
    if (response.ok && !response.redirected && response.type === "basic") {
        const copy = response.clone();
        caches.open(CACHE).then(cache => cache.put(request, copy));
    }
    return response;
}

self.addEventListener("fetch", event => {
    const request = event.request;
    const url = new URL(request.url);
    if (request.method !== "GET" || url.origin !== location.origin) return;
    if (request.mode === "navigate" && url.pathname === "/") {
        // The page itself: the server first (a sign-in redirect must win), the cached copy offline.
        event.respondWith(fetch(request).then(response => keep("/", response))
            .catch(() => caches.match("/").then(cached => cached || Response.error())));
    } else if (url.pathname.startsWith("/static/")) {
        // The files are versioned by this worker: a new build installs a new worker and a new cache.
        event.respondWith(caches.match(request, { ignoreSearch: true })
            .then(cached => cached || fetch(request).then(response => keep(request, response))));
    }
});

self.addEventListener("notificationclick", event => {
    event.notification.close();
    const target = event.notification.data && event.notification.data.url || "/";
    event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(windows => {
        const open = windows.find(client => new URL(client.url).pathname === "/");
        // Never navigate a page that's open: it may be streaming a reply. It switches view itself.
        if (open) return open.focus().then(client => client.postMessage({ type: "open", url: target }));
        return self.clients.openWindow(target);
    }));
});
