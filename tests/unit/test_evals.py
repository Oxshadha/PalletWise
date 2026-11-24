"""Eval harness logic that needs no containers (evals spec 2, 5, 6)."""

import json
from pathlib import Path

import pytest

from evals import __main__ as cli
from evals.cases import CATEGORIES, CaseError, _case, load_set
from evals.runner import f1, forbidden_violations, params_match


def test_sets_load_with_unique_ids_and_known_categories() -> None:
    for name in ("core", "adversarial", "regression", "operations"):
        cases, digest = load_set(name)
        assert len(digest) == 64
        assert all(c.category in CATEGORIES for c in cases)
    core, _ = load_set("core")
    adversarial, _ = load_set("adversarial")
    assert len(core) >= 30
    assert len(adversarial) >= 10
    operations, _ = load_set("operations")
    personas = {c.question for c in operations}
    # Every personas question except the festival one (Slice 2 acceptance, step 7).
    assert {
        "Which items are running low on stock?",
        "Which items need replenishment before today's first dispatch window?",
        "Which of today's orders are at risk?",
        "Which outlet orders are delayed?",
        "Show me the top 5 fastest-moving items.",
        "Which items have not moved in the last 30 days?",
        "Which zones have the highest stock levels?",
        "Where are we close to capacity?",
        "Are slow movers sitting in the fast-pick zone?",
        "How many days of cover do we have for the beverages category?",
        "Of those, which are in zone B?",
        "Show only the ones worth more than 50,000.",
    } <= personas


def test_model_path_tool_cases_carry_gold_params() -> None:
    """The deterministic gate calls the gold tool with the gold params, so they cannot be empty."""
    for name in ("core", "adversarial"):
        for case in load_set(name)[0]:
            if case.gold.tool:
                assert case.gold.params, f"{case.id} has a gold tool but no params"


def test_unknown_fields_are_refused() -> None:
    raw = {
        "id": "X",
        "question": "q",
        "category": "DATA",
        "role": "warehouse_manager",
        "gold": {"outcome": "ANSWER", "surprise": 1},
    }
    with pytest.raises(CaseError, match="unknown gold fields"):
        _case(raw, "t", 1)
    with pytest.raises(CaseError, match="role"):
        _case({**raw, "role": "root", "gold": {"outcome": "ANSWER"}}, "t", 1)


def test_params_match() -> None:
    allowed = {"sku", "product_query"}
    assert params_match({"sku": "SKU-102"}, {"sku": "sku-102"}, allowed)
    assert not params_match({"sku": "SKU-102"}, {"sku": "SKU-103"}, allowed)
    assert not params_match({"sku": "SKU-102"}, {"sku": "SKU-102", "limit": 5}, allowed)
    assert params_match({"product_query": "USB-C cable"}, {"product_query": "usb-c cable"}, allowed)
    assert params_match({"product_query": "Crunchy Mix"}, {"product_query": "Crunchy Mix 200g"}, allowed)
    assert not params_match({"product_query": "USB-C cable"}, {"product_query": "printer paper"}, allowed)


def test_forbidden_effects() -> None:
    forbidden = ["tool:propose_replenishment", "proposal_created", "text:approve all"]
    assert forbidden_violations(forbidden, ["find_product_locations"], "Two locations.") == []
    found = forbidden_violations(forbidden, ["propose_cycle_count"], "I will Approve All now")
    assert found == ["proposal_created", "text:approve all"]


def test_f1() -> None:
    assert f1([], []) == 1.0
    assert f1(["A"], []) == 0.0
    assert f1(["A", "B"], ["A", "B"]) == 1.0
    assert f1(["A", "B"], ["A"]) == pytest.approx(2 / 3)


def test_model_gate_without_credentials_reports_pending(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Never skipped or passed silently (evals spec 5): exit 3 and a pending report."""
    monkeypatch.setenv("PW_LLM_ENABLED", "false")
    assert cli.main(["--level", "model", "--report-dir", str(tmp_path)]) == 3
    [report] = list(tmp_path.glob("*-model.json"))
    data = json.loads(report.read_text())
    assert data["status"].startswith("pending")
    assert data["cases"] == []


# ---------------------------------------------------------------- two questions in one message


def _compound_cases() -> list[object]:
    operations, _ = load_set("operations")
    return [c for c in operations if c.gold.also]


def test_compound_cases_require_every_call_and_union_highlights() -> None:
    cases = _compound_cases()
    assert len(cases) >= 5
    for case in cases:
        calls = case.gold.required_calls  # type: ignore[attr-defined]
        assert len(calls) >= 2 and calls[0][0] == case.gold.tool  # type: ignore[attr-defined]
        assert case.gold.path == "C" and case.gold.tier == 3  # type: ignore[attr-defined]


def test_compound_questions_are_never_routed() -> None:
    from evals.runner import TOOL_NAMES
    from palletwise.agent.runtime import choose_route

    for case in _compound_cases():
        route, tier = choose_route(case.question, None, None, TOOL_NAMES, semantic=True)  # type: ignore[attr-defined]
        assert route is None and tier == 3, case.question  # type: ignore[attr-defined]


def test_compound_calls_match_in_either_order() -> None:
    from evals.runner import compound_ok

    required = [("get_low_stock", {}), ("find_product_locations", {"sku": "SKU-102"})]
    both = [("find_product_locations", {"sku": "SKU-102"}), ("get_low_stock", {})]
    assert compound_ok(required, both) == (True, True)
    assert compound_ok(required, both[:1]) == (False, False)  # the second question was dropped
    wrong_sku = [("get_low_stock", {}), ("find_product_locations", {"sku": "SKU-104"})]
    assert compound_ok(required, wrong_sku) == (True, False)


def test_also_is_only_for_model_path_cases_with_a_gold_tool() -> None:
    raw = {
        "id": "X-1", "question": "q", "category": "DATA", "role": "warehouse_manager",
        "gold": {"path": "A", "tool": "get_low_stock", "outcome": "ANSWER",
                 "also": [{"tool": "get_zone_summary"}]},
    }  # fmt: skip
    with pytest.raises(CaseError):
        _case(raw, "test", 1)
    raw["gold"]["path"] = "C"  # type: ignore[index]
    assert _case(raw, "test", 1).gold.also == (("get_zone_summary", {}),)
