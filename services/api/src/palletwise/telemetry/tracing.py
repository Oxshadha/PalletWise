"""Tracing helpers. Spans are no-ops until the OpenTelemetry SDK is configured (evals spec 10)."""

import secrets
from collections.abc import Sequence

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


def record_policy_decision(
    span: trace.Span, action: str, scope: str, allow: bool, reasons: Sequence[str]
) -> None:
    """Attributes of a policy.decision span and the denial counter, shared by every caller."""
    from palletwise.telemetry import metrics

    span.set_attribute("pw.policy.action", action)
    span.set_attribute("pw.policy.scope", scope)
    span.set_attribute("pw.policy.decision", "ALLOW" if allow else "DENY")
    if not allow:
        reason = metrics.bounded(reasons[0] if reasons else "unspecified", limit=60)
        span.set_attribute("pw.policy.deny_reason", reason)
        metrics.policy_denials.labels(reason=reason).inc()
