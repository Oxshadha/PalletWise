"""Tracing helpers. Spans are no-ops until the OpenTelemetry SDK is configured (evals spec 10)."""

import secrets

from opentelemetry import trace

tracer = trace.get_tracer("palletwise")


def current_trace_id() -> str:
    """The active trace id as 32 hex characters, or a new random one outside a trace.

    Callers that need one id across several events (a copilot run) take it once and keep it.
    """
    context = trace.get_current_span().get_span_context()
    if context.is_valid:
        return format(context.trace_id, "032x")
    return secrets.token_hex(16)
