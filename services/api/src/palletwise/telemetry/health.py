"""Minimal readiness endpoint for worker processes (engineering baseline 3 and 7.1).

Serves GET /health with 200 while the process reports ready and 503 otherwise.
"""

import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def serve_health(port: int, status: Callable[[], dict[str, object]]) -> ThreadingHTTPServer:
    """Start a daemon HTTP server; `status()` must return a dict with a boolean `ready`."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/health":
                self.send_response(404)
                self.end_headers()
                return
            body = status()
            payload = json.dumps(body).encode()
            self.send_response(200 if body.get("ready") else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return  # keep worker logs for work, not health probes

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)  # noqa: S104
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
