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
