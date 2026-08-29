"""Test fixtures: a local mock HTTP server with programmable responses.

Usage:
    mock_server.scenario = [(200, b"ok", {}), (429, b"slow", {"Retry-After": "1"}), ...]
    base url: mock_server.url
"""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


@pytest.fixture
def mock_server():
    """Local HTTP server with a scripted response list."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            srv: _MockServer = self.server  # type: ignore
            srv.requests.append({
                "path": self.path,
                "headers": dict(self.headers),
                "ts": time.monotonic(),
            })
            if srv.scenario:
                code, body, headers = srv.scenario.pop(0)
            else:
                code, body, headers = 200, b"default", {}
            self.send_response(code)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):  # noqa: N802 - silence access log
            pass

    class _MockServer(HTTPServer):
        scenario: list[tuple[int, bytes, dict]]
        requests: list[dict]
        url: str

    srv = _MockServer(("127.0.0.1", 0), Handler)
    srv.scenario = []
    srv.requests = []
    srv.url = f"http://127.0.0.1:{srv.server_address[1]}"
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=2)