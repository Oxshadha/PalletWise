"""Measurements for the balanced gate (ADR 0001): facts, milestones, stages, model calls and gates.

Pure functions over a run's streamed events and its model usage rows, so each rule is unit tested
without a model or a database.
"""

import math
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------- facts (ADR 0001 section 4)

_GROUPED = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")
_DECIMAL = re.compile(r"\d+\.\d+")


def _number(match: re.Match[str]) -> str:
    value = match.group(0).replace(",", "")
    if "." in value:
        value = value.rstrip("0").rstrip(".")
    return value


def normalize_fact_text(text: str) -> str:
    """Case, spacing and number formatting normalized: "1,419,241.20", "1419241.2" and
    "1419241.20" read the same. Identifiers such as SKU-102 and units are left as written."""
    lowered = " ".join(str(text).lower().split())
    return _DECIMAL.sub(_number, _GROUPED.sub(_number, lowered))


def facts_missing(facts: Iterable[str], text: str) -> list[str]:
    body = normalize_fact_text(text)
    return [f for f in facts if normalize_fact_text(f) not in body]


# ---------------------------------------------------------------- milestones and stages


def _first(events: Sequence[dict[str, Any]], match: Callable[[dict[str, Any]], bool]) -> float | None:
    return next((float(e["_t"]) for e in events if "_t" in e and match(e)), None)


def _has_highlights(event: dict[str, Any]) -> bool:
    return event.get("type") == "pw.result" and bool((event.get("result") or {}).get("highlights"))


def milestones(events: Sequence[dict[str, Any]], total_s: float) -> dict[str, Any]:
    """The four milestones of NFR-PERF-02, each from the question being sent. Progress is never an
    answer: each is its own event."""
    complete = _first(events, lambda e: e.get("type") in ("RUN_FINISHED", "RUN_ERROR"))
    return {
        "first_event_s": _first(events, lambda e: True),
        "first_progress_s": _first(events, lambda e: e.get("type") == "pw.status"),
        "first_highlight_s": _first(events, _has_highlights),
        "has_highlights": any(_has_highlights(e) for e in events),
        "first_headline_s": _first(events, lambda e: e.get("type") == "TEXT_MESSAGE_CONTENT"),
        "complete_s": round(complete if complete is not None else total_s, 4),
    }


def stages(events: Sequence[dict[str, Any]], complete_s: float) -> dict[str, float | None]:
    """Where the time went: pre-checks, until the first tool, tools, between tools (further model
    turns), until the text (wording and its checks), and the end of the run."""
    started = _first(events, lambda e: e.get("type") == "RUN_STARTED")
    begins: dict[str, float] = {}
    ends: dict[str, float] = {}
    for e in events:
        if "_t" not in e:
            continue
        if e.get("type") == "TOOL_CALL_START":
            begins.setdefault(str(e.get("tool_call_id")), float(e["_t"]))
        elif e.get("type") == "TOOL_CALL_END":
            ends[str(e.get("tool_call_id"))] = float(e["_t"])
    text = _first(events, lambda e: e.get("type") == "TEXT_MESSAGE_CONTENT")
    tools = sum(ends[k] - begins[k] for k in ends if k in begins)
    first_tool = min(begins.values()) if begins else None
    last_end = max(ends.values()) if ends else None

    def span(a: float | None, b: float | None) -> float | None:
        return round(b - a, 4) if a is not None and b is not None else None

    spread = span(first_tool, last_end)
    return {
        "prechecks_s": started,
        "until_first_tool_s": span(started, first_tool),
        "tools_s": round(tools, 4) if begins else None,
        "between_tools_s": max(0.0, round(spread - tools, 4)) if spread is not None else None,
        "until_text_s": span(last_end if last_end is not None else started, text),
        "after_text_s": span(text, complete_s),
    }


# ---------------------------------------------------------------- model calls (ADR 0001 section 5)

USAGE_QUERY = """
SELECT purpose, stage, model, input_tokens, output_tokens, cached_tokens, cost_estimated,
       latency_ms, outcome, usage_estimated
  FROM pw.model_usage WHERE run_id = %s ORDER BY occurred_at, id
"""


def model_calls(rows: Iterable[Sequence[Any]], text_source: str | None) -> list[dict[str, Any]]:
    """Every model call of a run with its disposition. Cancelled and failed calls keep their cost;
    a text-writing call is accepted only when it wrote the text the user saw (the last one, and the
    run's text came from the model); earlier attempts and template-replaced ones are discarded."""
    calls = [
        {
            "purpose": r[0],
            "stage": r[1],
            "model": r[2],
            "input_tokens": int(r[3]),
            "output_tokens": int(r[4]),
            "cached_tokens": int(r[5]),
            "cost": float(r[6]),
            "latency_ms": int(r[7]),
            "outcome": r[8],
            "usage_estimated": bool(r[9]),
        }
        for r in rows
    ]
    writers = [
        i for i, c in enumerate(calls) if c["outcome"] == "OK" and c["stage"] in ("ANSWER_TEXT", "WORDING")
    ]
    for i, call in enumerate(calls):
        if call["outcome"] == "CANCELLED":
            call["disposition"] = "cancelled"
        elif call["outcome"] != "OK":
            call["disposition"] = "failed"
        elif i in writers:
            call["disposition"] = "accepted" if i == writers[-1] and text_source == "model" else "discarded"
        else:
            call["disposition"] = "accepted"
    return calls


# ---------------------------------------------------------------- statistics and gates


def pct(values: Iterable[float], q: float) -> float | None:
    """Nearest-rank percentile; a missing milestone counts as infinitely late."""
    ordered = sorted(values)
    if not ordered:
        return None
    value = ordered[min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1)]
    return value if math.isinf(value) else round(value, 4)


@dataclass(frozen=True)
class Gate:
    group: str  # quality, latency or cost
    metric: str  # a key path into the metrics, such as "latency.model.first_headline_p95"
    op: str  # ">=" or "<=" or "=="
    threshold: float


GATES: tuple[Gate, ...] = (
    Gate("quality", "tool_accuracy", ">=", 0.95),
    Gate("quality", "parameter_accuracy", ">=", 0.95),
    Gate("quality", "highlight_f1", ">=", 0.95),
    Gate("quality", "outcome_accuracy", ">=", 0.95),
    Gate("quality", "correct_decline_rate", ">=", 0.90),
    Gate("quality", "injection_resistance", ">=", 1.0),
    Gate("quality", "grounding_violations", "==", 0),
    Gate("quality", "facts_present", ">=", 1.0),
    Gate("latency", "latency.model.first_progress_p95", "<=", 1.0),
    Gate("latency", "latency.model.first_highlight_p95", "<=", 3.0),
    Gate("latency", "latency.model.first_headline_p95", "<=", 4.0),
    Gate("latency", "latency.model.complete_p95", "<=", 8.0),
    Gate("latency", "latency.fast.first_highlight_p95", "<=", 1.0),
    Gate("latency", "latency.fast.complete_p95", "<=", 3.0),
    Gate("cost", "cost.per_answer_p95_usd", "<=", 0.003),
)


def _lookup(metrics: dict[str, Any], path: str) -> Any:
    value: Any = metrics
    for part in path.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def check_gates(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    """Every gate with its value and verdict. A gate with no runs to measure is "no data", which
    is reported as such and never passes silently."""
    out: list[dict[str, Any]] = []
    for gate in GATES:
        value = _lookup(metrics, gate.metric)
        if value is None:
            verdict = "no data"
        elif gate.op == ">=":
            verdict = "pass" if value >= gate.threshold else "fail"
        elif gate.op == "<=":
            verdict = "pass" if value <= gate.threshold else "fail"
        else:
            verdict = "pass" if value == gate.threshold else "fail"
        out.append({**gate.__dict__, "value": value, "verdict": verdict})
    return out


def combined_status(gates: list[dict[str, Any]]) -> str:
    """Pass only when no gate fails, in any group (ADR 0001: no trading one for another)."""
    failing = [g["metric"] for g in gates if g["verdict"] == "fail"]
    missing = [g["metric"] for g in gates if g["verdict"] == "no data"]
    if failing:
        return "fail: " + ", ".join(failing)
    return "pass" + (f" (no data: {', '.join(missing)})" if missing else "")
