"""The http.server span and one log line per request (evals spec 10 and 13).

The span carries the route template (never the raw path, which holds ids and codes) and the status.
Requests that start or drive a copilot run are always sampled.
"""

import logging
import time
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from opentelemetry.trace import SpanKind, Status, StatusCode

from palletwise.telemetry import metrics
from palletwise.telemetry.setup import KEEP_ATTRIBUTE
from palletwise.telemetry.tracing import tracer

log = logging.getLogger("palletwise.api.request")

ALWAYS_SAMPLED_PREFIX = "/api/copilot/runs"
QUIET_ROUTES = frozenset({"/api/health"})

Next = Callable[[Request], Awaitable[Response]]


async def request_span(request: Request, call_next: Next) -> Response:
    keep = request.url.path.startswith(ALWAYS_SAMPLED_PREFIX)
    started = time.perf_counter()
    with tracer.start_as_current_span(
        "http.server",
        kind=SpanKind.SERVER,
        attributes={"http.request.method": request.method, KEEP_ATTRIBUTE: keep},
    ) as span:
        try:
            response = await call_next(request)
        except Exception as exc:
            span.set_attribute("error.type", type(exc).__name__)
            span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            log.exception("request failed", extra={"method": request.method, "route": _route(request)})
            raise
        route = _route(request)
        span.set_attribute("http.route", route)
        span.set_attribute("http.response.status_code", response.status_code)
        if response.status_code >= 500:
            span.set_status(Status(StatusCode.ERROR))
        duration_ms = round((time.perf_counter() - started) * 1000)
        if response.status_code >= 500:
            level = logging.ERROR
        elif route in QUIET_ROUTES:  # container health probes every few seconds
            level = logging.DEBUG
        else:
            level = logging.INFO
        log.log(
            level,
            "%s %s %s",
            request.method,
            route,
            response.status_code,
            extra={"method": request.method, "route": route, "status": response.status_code,
                   "duration_ms": duration_ms},
        )  # fmt: skip
        return response


def _route(request: Request) -> str:
    """The matched route template, or a fixed label for unmatched paths (bounded, no raw ids)."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return metrics.bounded(path, limit=120) if path else "unmatched"
