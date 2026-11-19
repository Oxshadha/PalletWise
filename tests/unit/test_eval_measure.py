"""ADR 0001 measurements: facts, milestones, stages, call dispositions, gates and interleaving."""

import math

import pytest

from evals.measure import (
    GATES,
    check_gates,
    combined_status,
    facts_missing,
    invalid_reason,
    milestones,
    model_calls,
    normalize_fact_text,
    pct,
    stages,
)
from evals.runner import config_names, run_order
from palletwise.agent.runtime import RuntimeConfig

# ---------------------------------------------------------------- facts


@pytest.mark.parametrize(
    ("fact", "text"),
    [
        ("1,419,241.20", "SKU-147 with a stock value of 1419241.20"),
        ("1419241.2", "worth 1,419,241.20 in total"),
        ("6,657", "with 6657 on-hand items"),
        ("12.2 days", "The Beverages category has 12.20 days of cover."),
        ("SKU-102", "sku-102 is stored in 2 locations"),
    ],
)
def test_a_fact_matches_whatever_the_number_format(fact: str, text: str) -> None:
    assert facts_missing([fact], text) == []


def test_a_different_number_or_identifier_is_still_missing() -> None:
    assert facts_missing(["6,657", "SKU-102"], "6,658 units of SKU-1020") == ["6,657"]
    assert facts_missing(["12 days"], "12.5 days") == ["12 days"]


def test_identifiers_keep_their_shape() -> None:
    assert normalize_fact_text("Bin A1-05-02 holds SKU-147") == "bin a1-05-02 holds sku-147"


# ---------------------------------------------------------------- milestones and stages


def events(*items: tuple[str, float, dict]) -> list[dict]:  # type: ignore[type-arg]
    return [{"type": kind, "_t": t, **data} for kind, t, data in items]


MODEL_RUN = events(
    ("RUN_STARTED", 0.3, {"path": "C"}),
    ("TOOL_CALL_START", 2.4, {"tool_call_id": "1-q"}),
    ("TOOL_CALL_END", 2.6, {"tool_call_id": "1-q"}),
    ("pw.result", 2.6, {"result": {"highlights": []}}),
    ("TOOL_CALL_START", 4.0, {"tool_call_id": "2-q"}),
    ("TOOL_CALL_END", 4.5, {"tool_call_id": "2-q"}),
    ("pw.result", 4.5, {"result": {"highlights": [{"target_id": "b"}]}}),
    ("TEXT_MESSAGE_CONTENT", 7.0, {"delta": "text"}),
    ("RUN_FINISHED", 7.1, {"text_source": "model"}),
)


def test_each_milestone_is_its_own_event() -> None:
    m = milestones(MODEL_RUN, total_s=7.3)
    assert m["first_progress_s"] is None  # the baseline sends no progress: never counted as an answer
    assert m["first_highlight_s"] == 4.5  # the first result with highlights, not the first result
    assert m["first_headline_s"] == 7.0 and m["complete_s"] == 7.1 and m["has_highlights"]


def test_a_run_that_errors_completes_at_its_error() -> None:
    m = milestones(events(("RUN_STARTED", 0.2, {}), ("RUN_ERROR", 45.2, {})), total_s=45.3)
    assert m["complete_s"] == 45.2 and m["first_headline_s"] is None and not m["has_highlights"]


def test_stages_split_the_time() -> None:
    s = stages(MODEL_RUN, complete_s=7.1)
    assert s["prechecks_s"] == 0.3
    assert s["until_first_tool_s"] == 2.1
    assert s["tools_s"] == 0.7
    assert s["between_tools_s"] == 1.4  # the second model turn
    assert s["until_text_s"] == 2.5  # wording and its checks after the last tool
    assert s["after_text_s"] == 0.1


def test_stages_without_tools() -> None:
    s = stages(events(("RUN_STARTED", 0.2, {}), ("TEXT_MESSAGE_CONTENT", 1.0, {})), complete_s=1.1)
    assert s["tools_s"] is None and s["until_text_s"] == 0.8


# ---------------------------------------------------------------- model calls


def row(stage: str, outcome: str = "OK", cost: float = 0.0001, estimated: bool = False) -> tuple:  # type: ignore[type-arg]
    return ("AGENT", stage, "gemini-flash", 100, 20, 0, cost, 900, outcome, estimated)


def test_the_answer_text_call_the_user_saw_is_accepted_and_earlier_ones_discarded() -> None:
    calls = model_calls([row("TOOL_CHOICE"), row("ANSWER_TEXT"), row("ANSWER_TEXT")], "model")
    assert [c["disposition"] for c in calls] == ["accepted", "discarded", "accepted"]


def test_a_template_answer_discards_every_text_call_but_keeps_their_cost() -> None:
    calls = model_calls([row("TOOL_CHOICE"), row("ANSWER_TEXT"), row("ANSWER_TEXT")], "template")
    assert [c["disposition"] for c in calls] == ["accepted", "discarded", "discarded"]
    assert sum(c["cost"] for c in calls) == pytest.approx(0.0003)


def test_cancelled_and_failed_calls_count() -> None:
    calls = model_calls(
        [row("UNKNOWN", "CANCELLED", 0.002, True), row("UNKNOWN", "ERROR"), row("TOOL_CHOICE")], "model"
    )
    assert [c["disposition"] for c in calls] == ["cancelled", "failed", "accepted"]
    assert calls[0]["usage_estimated"] and sum(c["cost"] for c in calls) == pytest.approx(0.0022)


# ---------------------------------------------------------------- gates


def test_a_missing_milestone_is_the_worst_value() -> None:
    assert pct([1.0, 2.0, math.inf], 0.95) == math.inf
    assert pct([1.0, 2.0, 3.0, 4.0], 0.5) == 2.0
    assert pct([], 0.5) is None


def passing_metrics() -> dict:  # type: ignore[type-arg]
    lat = {
        "first_progress_p95": 0.5,
        "first_highlight_p95": 2.0,
        "first_headline_p95": 3.0,
        "complete_p95": 6.0,
    }
    return {
        "tool_accuracy": 1.0,
        "parameter_accuracy": 1.0,
        "highlight_f1": 1.0,
        "outcome_accuracy": 1.0,
        "correct_decline_rate": 1.0,
        "injection_resistance": 1.0,
        "grounding_violations": 0,
        "facts_present": 1.0,
        "latency": {"model": lat, "fast": {"first_highlight_p95": 0.4, "complete_p95": 2.0}},
        "cost": {"per_answer_p95_usd": 0.002},
    }


def test_all_three_groups_must_pass_together() -> None:
    metrics = passing_metrics()
    assert combined_status(check_gates(metrics)) == "pass"
    metrics["cost"]["per_answer_p95_usd"] = 0.0031
    assert combined_status(check_gates(metrics)) == "fail: cost.per_answer_p95_usd"


def test_facts_below_100_percent_fail_quality() -> None:
    metrics = passing_metrics()
    metrics["facts_present"] = 0.99
    assert "facts_present" in combined_status(check_gates(metrics))


def test_a_gate_without_runs_is_reported_never_passed_silently() -> None:
    metrics = passing_metrics()
    metrics["latency"]["fast"] = None
    gates = check_gates(metrics)
    assert {g["verdict"] for g in gates if g["metric"].startswith("latency.fast")} == {"no data"}
    assert combined_status(gates).startswith("pass (no data: latency.fast")


def test_the_gates_are_the_approved_ones() -> None:
    by_metric = {g.metric: (g.op, g.threshold) for g in GATES}
    assert by_metric["latency.model.first_progress_p95"] == ("<=", 1.0)
    assert by_metric["latency.model.first_highlight_p95"] == ("<=", 3.0)
    assert by_metric["latency.model.first_headline_p95"] == ("<=", 4.0)
    assert by_metric["latency.model.complete_p95"] == ("<=", 8.0)
    assert by_metric["cost.per_answer_p95_usd"] == ("<=", 0.003)
    assert by_metric["facts_present"] == (">=", 1.0)
    assert by_metric["grounding_violations"] == ("==", 0)


# ---------------------------------------------------------------- comparison workload


def test_configurations_alternate_who_goes_first() -> None:
    assert [run_order(2, n) for n in range(4)] == [[0, 1], [1, 0], [0, 1], [1, 0]]


def test_an_a_a_comparison_gets_distinct_names() -> None:
    assert config_names([RuntimeConfig(), RuntimeConfig()]) == ["baseline-a", "baseline-b"]
    assert config_names([RuntimeConfig()]) == ["baseline"]


def test_a_run_the_provider_mostly_rejected_is_invalid_not_a_measurement() -> None:
    # The first baseline: 122 of 201 calls refused in about 5 ms by a model client on a closed loop.
    assert (
        invalid_reason({"rejected_calls": 122, "calls": 201})
        == "the provider rejected 122 of 201 model calls"
    )
    assert invalid_reason({"rejected_calls": 1, "calls": 100}) is None
    assert invalid_reason({"rejected_calls": 0, "calls": 0}) is None
