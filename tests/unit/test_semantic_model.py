"""semantic/model.yaml and the v1 tool input models (semantic spec 2 and 7.5, agent spec 4.1)."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from palletwise.contracts.results import DEFAULT_SEVERITY, HighlightReason, Severity
from palletwise.semantic.model import semantic_model, site_time
from palletwise.tools.catalog import FindProductLocationsInput, GetBinContentsInput, v1_tools

DASHES = (chr(0x2013), chr(0x2014))


def test_model_loads_and_every_result_uses_known_measures() -> None:
    model = semantic_model()
    assert model.version == 1
    for result in model.results.values():
        assert set(result.measures) <= set(model.measures)


def test_explanations_are_filled_and_dash_free() -> None:
    model = semantic_model()
    texts = model.explain("find_product_locations", sku="SKU-102", as_of="06:00, 1 Apr 2026 (Asia/Colombo)")
    assert "SKU-102" in texts[0] and "06:00, 1 Apr 2026" in texts[0] and len(texts) == 4
    everything = [m.how_computed for m in model.measures.values()] + [
        r.how_computed for r in model.results.values()
    ]
    assert not any(d in text for text in everything for d in DASHES)


def test_site_time_is_local() -> None:
    moment = datetime(2026, 4, 1, 0, 30, tzinfo=UTC)
    assert site_time(moment, "Asia/Colombo") == "06:00, 1 Apr 2026 (Asia/Colombo)"


def test_every_tool_has_a_result_explanation() -> None:
    assert {tool.name for tool in v1_tools()} <= set(semantic_model().results)


def test_fixed_severities_follow_the_contract() -> None:
    assert DEFAULT_SEVERITY[HighlightReason.LOCATION] is Severity.INFO
    assert DEFAULT_SEVERITY[HighlightReason.DELAYED_ORDER] is Severity.CRITICAL
    # Case-dependent reasons are set by their rule, never defaulted.
    for reason in (HighlightReason.STOCKOUT_RISK, HighlightReason.CAPACITY, HighlightReason.FESTIVAL_RISK):
        assert reason not in DEFAULT_SEVERITY


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"sku": "SKU-1", "product_query": "mouse"},
        {"sku": "SKU 1"},
        {"sku": "-SKU"},
        {"product_query": "a"},
        {"product_query": "x" * 81},
        {"sku": "SKU-1", "extra": 1},
    ],
)
def test_find_product_locations_input_is_strict(arguments: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        FindProductLocationsInput.model_validate(arguments)


def test_find_product_locations_input_normalizes_whitespace() -> None:
    assert FindProductLocationsInput.model_validate({"sku": "  SKU-102 "}).sku == "SKU-102"
    assert (
        FindProductLocationsInput.model_validate({"product_query": " wireless mouse "}).product_query
        == "wireless mouse"
    )


@pytest.mark.parametrize("code", ["", "A1 B2", "A1;", "-A1", "A" * 33])
def test_bin_code_input_is_strict(code: str) -> None:
    with pytest.raises(ValidationError):
        GetBinContentsInput.model_validate({"bin_code": code})
