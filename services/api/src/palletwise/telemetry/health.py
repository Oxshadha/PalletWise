"""Process plumbing shared by every worker: logging, the readiness endpoint and its probe.

`serve_health` answers GET /health with 200 while the process reports ready and 503 otherwise.
`main` is the `palletwise-healthcheck` probe used by every Python container (compose).
"""

import json
import logging
import os
import sys
import threading
import urllib.request
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from palletwise import clock


def configure_logging() -> None:
    """JSON log lines with trace ids (evals spec 13)."""
    from palletwise.telemetry.logs import configure_logging as configure_json_logging

    configure_json_logging()


def start_process(service: str) -> None:
    """Logs and traces for one process, before it does any work."""
    from palletwise.telemetry.setup import configure_telemetry

    configure_logging()
    configure_telemetry(service)


def health_port(default: int) -> int:
    return int(os.environ.get("PW_HEALTH_PORT", str(default)))


def serve_health(port: int, status: Callable[[], dict[str, object]]) -> ThreadingHTTPServer:
    """Start a daemon HTTP server; `status()` must return a dict with a boolean `ready`."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/metrics":
                from palletwise.telemetry.metrics import exposition

                payload = exposition()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
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


def idle_worker(name: str, default_port: int, status: dict[str, object]) -> int:
    """Run a worker that only reports health until its slice adds work."""
    start_process(name)
    serve_health(health_port(default_port), lambda: status)
    logging.getLogger(name).info("%s started", name)
    while True:
        clock.sleep(3600)


def main() -> int:
    """Container health probe: exit 0 when the local /health answers 200."""
    url = f"http://127.0.0.1:{health_port(8000)}{os.environ.get('PW_HEALTH_PATH', '/health')}"
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            return 0 if resp.status == 200 else 1
    except OSError:
        return 1


if __name__ == "__main__":
    sys.exit(main())
