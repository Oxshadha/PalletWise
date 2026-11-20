"""Prometheus metrics (evals spec 12). Labels never carry users, tenants or free text.

Workers expose /metrics on their health port; the API on an internal port the edge does not route.
"""

from collections.abc import Callable, Iterator, Mapping

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

REGISTRY = CollectorRegistry(auto_describe=True)

LATENCY_BUCKETS = (0.1, 0.25, 0.5, 1, 2, 3, 5, 8, 13, 21, 34, 60)

copilot_runs = Counter("pw_copilot_runs", "Copilot runs", ["path", "outcome"], registry=REGISTRY)
time_to_first_highlight = Histogram(
    "pw_copilot_time_to_first_highlight_seconds",
    "Time from question to the first map highlight",
    ["path"],
    buckets=LATENCY_BUCKETS,
    registry=REGISTRY,
)
time_to_first_text = Histogram(
    "pw_copilot_time_to_first_text_seconds",
    "Time from question to the first answer text",
    ["path"],
    buckets=LATENCY_BUCKETS,
    registry=REGISTRY,
)
run_duration = Histogram(
    "pw_copilot_run_duration_seconds",
    "Copilot run duration",
    ["path", "outcome"],
    buckets=LATENCY_BUCKETS,
    registry=REGISTRY,
)
wording_cache = Counter(
    "pw_wording_cache", "Answer-wording cache lookups and stores", ["outcome"], registry=REGISTRY
)
llm_tokens = Counter("pw_llm_tokens", "Model tokens", ["model", "direction"], registry=REGISTRY)
llm_cost = Counter("pw_llm_cost_estimated", "Estimated model cost in USD", ["model"], registry=REGISTRY)
tool_calls = Counter(
    "pw_tool_calls", "Tool calls through the executor", ["tool", "outcome"], registry=REGISTRY
)
policy_denials = Counter("pw_policy_denials", "Policy denials", ["reason"], registry=REGISTRY)
guardrail_triggers = Counter("pw_guardrail_triggers", "Guardrail triggers", ["kind"], registry=REGISTRY)
feedback = Counter("pw_feedback", "Answer feedback", ["rating"], registry=REGISTRY)
ingest_records = Counter("pw_ingest_records", "Records ingested", ["record_type"], registry=REGISTRY)
export_failures = Counter(
    "pw_telemetry_export_failures", "Span export batches that failed", ["exporter"], registry=REGISTRY
)
ingest_lag = Gauge(
    "pw_ingest_lag_seconds",
    "Newest source record time minus the site clock; positive means ingestion is behind",
    registry=REGISTRY,
)

# Bounded label values: anything unexpected is folded into "other".
GUARDRAIL_KINDS = frozenset({"input", "grounding", "entity", "classifier"})
DIRECTIONS = frozenset({"input", "output", "cached"})


def bounded(value: str | None, allowed: frozenset[str] | None = None, limit: int = 40) -> str:
    """A label value that cannot grow without bound or carry free text."""
    if not value:
        return "none"
    if allowed is not None:
        return value if value in allowed else "other"
    return value[:limit]


PoolStats = Callable[[], Mapping[str, Mapping[str, int]]]
_pool_sources: list[PoolStats] = []


def register_pool_source(source: PoolStats) -> None:
    """The database layer reports its pools here, so telemetry never imports a driver."""
    if source not in _pool_sources:
        _pool_sources.append(source)


class _PoolCollector(Collector):
    def collect(self) -> Iterator[GaugeMetricFamily]:
        family = GaugeMetricFamily(
            "pw_db_pool_connections", "Database pool connections", labels=["role", "state"]
        )
        for source in _pool_sources:
            for role, states in source().items():
                for state, count in states.items():
                    family.add_metric([role, state], count)
        yield family


REGISTRY.register(_PoolCollector())


def exposition() -> bytes:
    """The Prometheus text format of every metric in this process."""
    return generate_latest(REGISTRY)


_serving: set[int] = set()


def serve_metrics(port: int) -> None:
    """A daemon /metrics server, for processes whose main port is public (the API). Once per port."""
    import logging

    from prometheus_client import start_http_server

    if port in _serving:
        return
    try:
        start_http_server(port, registry=REGISTRY)
        _serving.add(port)
    except OSError as exc:
        logging.getLogger(__name__).warning("metrics port %s unavailable: %s", port, exc)
