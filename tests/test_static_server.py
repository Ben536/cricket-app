"""
The Pi's UI server must answer every request with a real HTTP response.

2026-08 review T1.12: `log_message` indexed args[2], but `send_error()` logs
with two args, so every 404 raised IndexError before the response was
written and the client saw a connection reset ("curl: (52) Empty reply").
A PWA whose cached index.html referenced a purged asset then fell to the
service worker's 'Offline' response and white-screened.
"""

from __future__ import annotations

import functools
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from static_server import SPAHandler, CACHE_IMMUTABLE, CACHE_REVALIDATE  # noqa: E402


@pytest.fixture
def served(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>shell</html>")
    (dist / "assets" / "index-abc123.js").write_text("console.log(1)")
    (dist / "manifest.json").write_text("{}")

    handler = functools.partial(SPAHandler, directory=str(dist))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def get(url):
    with urlopen(url, timeout=5) as resp:
        return resp.status, resp.headers, resp.read()


def test_shell_is_served_and_never_cached_blindly(served):
    status, headers, body = get(f"{served}/")
    assert status == 200 and b"shell" in body
    assert headers["Cache-Control"] == CACHE_REVALIDATE


def test_hashed_assets_are_immutable(served):
    status, headers, _ = get(f"{served}/assets/index-abc123.js")
    assert status == 200
    assert headers["Cache-Control"] == CACHE_IMMUTABLE


def test_spa_route_falls_back_to_the_shell(served):
    status, _, body = get(f"{served}/session/history")
    assert status == 200 and b"shell" in body


def test_missing_asset_is_a_real_404_not_a_connection_reset(served):
    for path in ("/assets/index-OLDHASH.js", "/favicon.ico"):
        with pytest.raises(HTTPError) as exc:
            get(f"{served}{path}")
        assert exc.value.code == 404
        # The response body was actually written
        assert exc.value.read()


def test_query_string_does_not_break_routing(served):
    status, _, body = get(f"{served}/?server=10.42.0.1:5002")
    assert status == 200 and b"shell" in body
