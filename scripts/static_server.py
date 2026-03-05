#!/usr/bin/env python3
"""
Simple static file server for CricketRadar PWA.

Serves the built frontend and handles SPA routing
(returns index.html for non-file routes).
"""

import http.server
import os
import socketserver
from pathlib import Path

PORT = 5173
DIST_DIR = Path(__file__).parent.parent / "dist"


class SPAHandler(http.server.SimpleHTTPRequestHandler):
    """Handler that serves index.html for SPA routes."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIST_DIR), **kwargs)

    def do_GET(self):
        # Serve actual files if they exist
        path = self.translate_path(self.path)
        if os.path.exists(path) and os.path.isfile(path):
            return super().do_GET()

        # For SPA routes, serve index.html
        if not os.path.splitext(self.path)[1]:
            self.path = "/index.html"

        return super().do_GET()

    def log_message(self, format, *args):
        """Log to stdout for journald."""
        print(f"[static] {args[0]} {args[1]} {args[2]}")


def main():
    os.chdir(DIST_DIR)

    with socketserver.TCPServer(("0.0.0.0", PORT), SPAHandler) as httpd:
        print(f"Serving {DIST_DIR} on http://0.0.0.0:{PORT}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down...")


if __name__ == "__main__":
    main()
