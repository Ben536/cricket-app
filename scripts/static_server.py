#!/usr/bin/env python3
"""
Static file server for the CricketRadar PWA (cricket-ui.service, port 5173).

Serves the built frontend (dist/) and handles SPA routing: an extension-less
path that is not a file returns index.html.

Two things this server has to get right that the stock handler does not:

1. A 404 must be a real HTTP response. The previous log_message override
   indexed args[2], but send_error() calls log_error with only two args, so
   every 404 raised IndexError BEFORE the response was written and the
   client got a connection reset. A PWA holding a cached index.html that
   references an asset a deploy purged then fell to the service worker's
   'Offline' response and white-screened.

2. Cache headers. Vite content-hashes everything under /assets/, so those
   are immutable; index.html (the shell) must always be revalidated or a
   phone can keep serving a shell whose bundles no longer exist. A 404 for
   an /assets/ path is NOT immutable - a transient miss during a deploy
   must not be cached for a year.
"""

from __future__ import annotations

import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 5173
DIST_DIR = Path(__file__).parent.parent / "dist"

CACHE_IMMUTABLE = "public, max-age=31536000, immutable"
CACHE_REVALIDATE = "no-cache"


class SPAHandler(SimpleHTTPRequestHandler):
    """Serve dist/ with SPA fallback and correct cache headers."""

    # HTTP/1.1 lets a phone reuse one connection for the shell + assets.
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, directory: str | None = None, **kwargs):
        self._cache_control = CACHE_REVALIDATE
        super().__init__(*args, directory=directory or str(DIST_DIR), **kwargs)

    def _resolve(self) -> bool:
        """Apply SPA routing to self.path and decide the cache policy.
        Returns False if the request must be rejected."""
        self._cache_control = CACHE_REVALIDATE
        request_path = self.path.split("?", 1)[0]
        if "\x00" in request_path or "%00" in request_path.lower():
            self.send_error(400, "Bad request")
            return False
        path = self.translate_path(self.path)
        exists = os.path.exists(path) and os.path.isfile(path)
        if not exists:
            # SPA route (no extension) -> shell. A missing file WITH an
            # extension (e.g. a purged /assets/*.js) must 404 honestly so the
            # service worker can react, not be answered with HTML.
            if not os.path.splitext(request_path)[1]:
                self.path = "/index.html"
        elif request_path.startswith("/assets/"):
            self._cache_control = CACHE_IMMUTABLE
        return True

    def do_GET(self):
        if self._resolve():
            return super().do_GET()

    def do_HEAD(self):
        if self._resolve():
            return super().do_HEAD()

    def end_headers(self):
        self.send_header("Cache-Control", self._cache_control)
        super().end_headers()

    def send_error(self, code, message=None, explain=None):
        # Errors are never cacheable, whatever path they were for
        self._cache_control = CACHE_REVALIDATE
        super().send_error(code, message, explain)

    def log_message(self, format, *args):
        """Log to stdout for journald. `format % args` handles every arity
        the base class uses (requests log 3 args, errors log 2)."""
        try:
            print(f"[static] {format % args}", flush=True)
        except Exception:
            print(f"[static] {format} {args}", flush=True)


def main():
    os.chdir(DIST_DIR)
    # Threading: the previous single-threaded TCPServer served every request
    # serially, so a slow client held the whole UI for everyone else.
    with ThreadingHTTPServer(("0.0.0.0", PORT), SPAHandler) as httpd:
        httpd.daemon_threads = True
        print(f"Serving {DIST_DIR} on http://0.0.0.0:{PORT}", flush=True)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down...")


if __name__ == "__main__":
    main()
