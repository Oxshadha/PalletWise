"""Telemetry privacy, startup checks, logs and metrics (evals spec 11 to 13, tests of section 16)."""

import json
import logging
import re
from typing import Any

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import Decision

from palletwise.settings import TelemetrySettings
from palletwise.telemetry import metrics
from palletwise.telemetry.logs import JsonFormatter
from palletwise.telemetry.privacy import Masker, SanitizingExporter
from palletwise.telemetry.setup import KEEP_ATTRIBUTE, KeepCopilotRuns, TelemetryConfigError, check
from palletwise.telemetry.tracing import tracer

TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.c2lnbmF0dXJl"


def exported(content: bool = False) -> tuple[Any, InMemorySpanExporter]:
    """A private provider whose spans pass the sanitizing exporter, as in production."""
    sink = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(
        SimpleSpanProcessor(SanitizingExporter(sink, Masker(b"salt"), content=content))
    )
    return provider.get_tracer("test"), sink


def test_attributes_not_on_the_allowlist_are_not_exported() -> None:
    test_tracer, sink = exported()
    with test_tracer.start_as_current_span("tool.execute") as span:
        span.set_attribute("pw.tool.name", "find_product_locations")
        span.set_attribute("pw.user.subject", "manager-1")
        span.set_attribute("db.statement", "SELECT * FROM pw.bin WHERE code = 'A1'")
    [out] = sink.get_finished_spans()
    assert dict(out.attributes or {}) == {"pw.tool.name": "find_product_locations"}


def test_an_email_in_an_allowed_attribute_is_exported_as_a_hash() -> None:
    test_tracer, sink = exported()
    with test_tracer.start_as_current_span("policy.decision") as span:
        span.set_attribute("pw.policy.deny_reason", "denied for jane.doe@example.com")
    [out] = sink.get_finished_spans()
    value = str((out.attributes or {})["pw.policy.deny_reason"])
    assert "jane.doe" not in value and "example.com" not in value
    assert value == f"denied for {Masker(b'salt').hash('jane.doe@example.com')}"


def test_tokens_exception_messages_and_sql_values_never_leave() -> None:
    test_tracer, sink = exported()
    with pytest.raises(ValueError), test_tracer.start_as_current_span("db.query") as span:
        span.set_attribute("pw.db.statement", f"Bearer {TOKEN}")
        raise ValueError("duplicate key value (sku)=(SKU-102) session=abc")
    [out] = sink.get_finished_spans()
    text = json.dumps(
        {
            "attributes": dict(out.attributes or {}),
            "events": [dict(e.attributes or {}) for e in out.events],
            "status": out.status.description,
        }
    )
    assert TOKEN not in text and "SKU-102" not in text and "session=abc" not in text
    assert out.status.description == "ValueError"
    assert [dict(e.attributes or {}) for e in out.events] == [{"exception.type": "ValueError"}]


def test_identifiers_and_dates_are_not_mistaken_for_phone_numbers() -> None:
    test_tracer, sink = exported()
    with test_tracer.start_as_current_span("tool.execute") as span:
        span.set_attribute("pw.run.id", "12345678-1234-1234-1234-123456789012")
        span.set_attribute("pw.data_as_of", "2026-04-01T06:00:00+05:30")
    [out] = sink.get_finished_spans()
    assert (out.attributes or {})["pw.run.id"] == "12345678-1234-1234-1234-123456789012"
    assert (out.attributes or {})["pw.data_as_of"] == "2026-04-01T06:00:00+05:30"


def test_prompt_content_is_exported_only_when_capture_is_on() -> None:
    for content in (False, True):
        test_tracer, sink = exported(content=content)
        with test_tracer.start_as_current_span("gen_ai.chat") as span:
            span.add_event("gen_ai.content.prompt", {"gen_ai.prompt": "Where is SKU-102?"})
        [out] = sink.get_finished_spans()
        assert bool(out.events) is content


def test_content_capture_is_refused_in_production() -> None:
    with pytest.raises(TelemetryConfigError, match="PW_TRACE_CONTENT"):
        check(TelemetrySettings(environment="production", trace_content=True, mask_salt="s"))
    with pytest.raises(TelemetryConfigError, match="MASK_SALT"):
        check(TelemetrySettings(environment="production"))
    check(TelemetrySettings(environment="production", mask_salt="s"))
    check(TelemetrySettings(trace_content=True))  # development on synthetic data


def test_trace_content_in_a_production_environment_stops_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    from palletwise.telemetry.setup import configure_telemetry

    monkeypatch.setenv("PW_TRACE_CONTENT", "true")
    monkeypatch.setenv("PW_TELEMETRY_ENVIRONMENT", "production")
    monkeypatch.setenv("PW_TELEMETRY_MASK_SALT", "s")
    with pytest.raises(TelemetryConfigError):
        configure_telemetry("palletwise-api")


def test_copilot_runs_are_always_sampled_and_others_by_ratio() -> None:
    sampler = KeepCopilotRuns(0.0)
    assert sampler.should_sample(None, 1, "copilot.run").decision is Decision.RECORD_AND_SAMPLE
    keep = sampler.should_sample(None, 1, "http.server", attributes={KEEP_ATTRIBUTE: True})
    assert keep.decision is Decision.RECORD_AND_SAMPLE
    assert sampler.should_sample(None, 1, "http.server").decision is Decision.DROP


def test_log_lines_are_json_with_trace_ids_and_no_secrets() -> None:
    formatter = JsonFormatter()
    with tracer.start_as_current_span("http.server") as span:
        record = logging.LogRecord(
            "palletwise.test", logging.INFO, __file__, 1,
            "refresh for jane@example.com with Bearer %s", (TOKEN,), None,
        )  # fmt: skip
        record.route = "/api/copilot/runs"
        line = json.loads(formatter.format(record))
        context = span.get_span_context()
    assert line["trace_id"] == format(context.trace_id, "032x")
    assert line["span_id"] == format(context.span_id, "016x")
    assert line["level"] == "info" and line["route"] == "/api/copilot/runs"
    assert TOKEN not in line["message"] and "jane@example.com" not in line["message"]


SPEC_METRICS = {
    "pw_copilot_runs_total": ("path", "outcome"),
    "pw_copilot_time_to_first_highlight_seconds": ("path",),
    "pw_copilot_time_to_first_text_seconds": ("path",),
    "pw_copilot_run_duration_seconds": ("path", "outcome"),
    "pw_llm_tokens_total": ("model", "direction"),
    "pw_llm_cost_estimated_total": ("model",),
    "pw_tool_calls_total": ("tool", "outcome"),
    "pw_policy_denials_total": ("reason",),
    "pw_guardrail_triggers_total": ("kind",),
    "pw_feedback_total": ("rating",),
    "pw_ingest_records_total": ("record_type",),
    "pw_ingest_lag_seconds": (),
    "pw_db_pool_connections": ("role", "state"),
}


def test_metrics_expose_every_spec_metric_with_bounded_labels() -> None:
    import palletwise.db.connection  # noqa: F401  (registers the pool source)

    metrics.copilot_runs.labels(path="C", outcome="ANSWER").inc()
    metrics.tool_calls.labels(tool="find_product_locations", outcome="OK").inc()
    text = metrics.exposition().decode()
    for name in SPEC_METRICS:
        assert f"# TYPE {name.removesuffix('_total')}" in text, name
    for name, labels in SPEC_METRICS.items():
        for sample in re.findall(rf"^{name}(?:_bucket|_sum|_count)?\{{([^}}]*)\}}", text, re.M):
            keys = {pair.split("=")[0] for pair in sample.split(",") if pair}
            assert keys <= {*labels, "le"}, (name, keys)
    for forbidden in ("tenant", "user", "subject", "question"):
        assert f"{forbidden}=" not in text


def test_label_values_are_bounded() -> None:
    assert metrics.bounded("x" * 500) == "x" * 40
    assert metrics.bounded("free text", metrics.GUARDRAIL_KINDS) == "other"
    assert metrics.bounded(None) == "none"


def test_worker_health_ports_serve_metrics() -> None:
    import urllib.request

    from palletwise.telemetry.health import serve_health

    server = serve_health(0, lambda: {"ready": True})
    try:
        port = server.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5) as response:
            body = response.read().decode()
        assert response.status == 200 and "pw_tool_calls_total" in body
    finally:
        server.shutdown()
