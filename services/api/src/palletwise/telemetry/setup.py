"""OpenTelemetry for one process (evals spec 10 and 11).

Every process calls `configure_telemetry` once at startup. Without an export endpoint spans are
still created (trace ids reach audit and error responses) but go nowhere.
"""

import base64
import logging
import os
from collections.abc import Sequence

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.sampling import (
    Decision,
    ParentBased,
    Sampler,
    SamplingResult,
    TraceIdRatioBased,
)
from opentelemetry.trace import Link, SpanKind
from opentelemetry.util.types import Attributes

from palletwise.settings import TelemetrySettings
from palletwise.telemetry.privacy import Masker, SanitizingExporter

log = logging.getLogger(__name__)

# Set by the HTTP middleware on requests that start or drive a copilot run; never exported.
KEEP_ATTRIBUTE = "pw.sample.keep"
ALWAYS_KEPT_SPANS = frozenset({"copilot.run"})
_content_capture = False
_provider: TracerProvider | None = None
_masker = Masker(os.urandom(32))  # replaced by configure_telemetry


class TelemetryConfigError(RuntimeError):
    pass


class KeepCopilotRuns(Sampler):
    """Root sampler: copilot runs always, other traces at the configured ratio."""

    def __init__(self, ratio: float) -> None:
        self._ratio = TraceIdRatioBased(ratio)

    def should_sample(
        self,
        parent_context: Context | None,
        trace_id: int,
        name: str,
        kind: SpanKind | None = None,
        attributes: Attributes = None,
        links: Sequence[Link] | None = None,
        trace_state: trace.TraceState | None = None,
    ) -> SamplingResult:
        if name in ALWAYS_KEPT_SPANS or (attributes or {}).get(KEEP_ATTRIBUTE):
            return SamplingResult(Decision.RECORD_AND_SAMPLE, attributes, trace_state)
        return self._ratio.should_sample(parent_context, trace_id, name, kind, attributes, links, trace_state)

    def get_description(self) -> str:
        return f"KeepCopilotRuns({self._ratio.get_description()})"


def check(settings: TelemetrySettings) -> None:
    """Refuses unsafe configurations at startup (evals spec 11)."""
    production = settings.environment.lower() == "production"
    if production and settings.trace_content:
        raise TelemetryConfigError("PW_TRACE_CONTENT=true is refused in a production environment")
    if production and not settings.mask_salt:
        raise TelemetryConfigError("PW_TELEMETRY_MASK_SALT is required in a production environment")


def _exporters(settings: TelemetrySettings) -> list[SpanExporter]:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    found: list[SpanExporter] = []
    if settings.otlp_endpoint:
        found.append(OTLPSpanExporter(endpoint=settings.otlp_endpoint))
    if settings.langfuse_host and settings.langfuse_public_key and settings.langfuse_secret_key:
        pair = f"{settings.langfuse_public_key}:{settings.langfuse_secret_key}".encode()
        found.append(
            OTLPSpanExporter(
                endpoint=f"{settings.langfuse_host.rstrip('/')}/api/public/otel/v1/traces",
                headers={"Authorization": f"Basic {base64.b64encode(pair).decode()}"},
            )
        )
    return found


def configure_telemetry(
    service: str,
    settings: TelemetrySettings | None = None,
    *,
    exporters: Sequence[SpanExporter] | None = None,
) -> TracerProvider:
    """Installs the tracer provider for this process, once.

    `exporters` replaces the configured exporters (tests).
    """
    global _provider
    settings = settings or TelemetrySettings()
    check(settings)
    if _provider is not None:
        return _provider
    # Without a salt (development only: check() refuses production) hashes correlate within this
    # process, not across processes or restarts.
    salt = settings.mask_salt.encode() if settings.mask_salt else os.urandom(32)
    global _masker
    masker = _masker = Masker(salt)
    sampler = ParentBased(root=KeepCopilotRuns(settings.sample_ratio))
    provider = TracerProvider(resource=Resource.create({"service.name": service}), sampler=sampler)
    targets = list(exporters) if exporters is not None else _exporters(settings)
    for exporter in targets:
        wrapped = SanitizingExporter(exporter, masker, content=settings.trace_content)
        processor = SimpleSpanProcessor(wrapped) if exporters is not None else BatchSpanProcessor(wrapped)
        provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    _provider = provider
    global _content_capture
    _content_capture = settings.trace_content
    if settings.trace_content:
        log.warning("prompt and response capture is on: development use with synthetic data only")
    log.info("telemetry ready: %d exporter(s), sample ratio %s", len(targets), settings.sample_ratio)
    return provider


def user_hash(subject: str) -> str:
    """The salted hash that stands for a user in telemetry (pw.user.hash); never the subject."""
    return _masker.hash(subject)


def content_capture() -> bool:
    """Whether model prompts and responses may be recorded as span events in this process."""
    return _content_capture
