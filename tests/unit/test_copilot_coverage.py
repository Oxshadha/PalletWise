"""Every tool is wired into the copilot (Slice 2 step 5): templates, suggestions, starters and the
fast path, with arguments that the tools accept (FR-COP-07: a suggestion never leads to a refusal).
"""

import pytest

from palletwise.agent import fast_path, suggestions, templates
from palletwise.tools.catalog import v1_tools

TOOLS = {t.name: t for t in v1_tools()}


def test_every_tool_has_a_path_b_template_and_suggestion_rule() -> None:
    assert set(TOOLS) <= set(templates.TEMPLATES)
    assert set(TOOLS) <= set(suggestions.RULES)


@pytest.mark.parametrize(
    "suggestion",
    [*suggestions.STARTERS, suggestions.ORDERS_AT_RISK, suggestions.REPLENISH_FIRST,
     suggestions.PRODUCTS_SHORT, suggestions.CAPACITY, suggestions.ZONES, suggestions.SLOW_IN_PRIME],
    ids=lambda s: s.label,
)  # fmt: skip
def test_fixed_suggestions_name_built_tools_with_valid_arguments(suggestion: suggestions.Suggestion) -> None:
    assert suggestion.tool in TOOLS
    TOOLS[suggestion.tool].input_model.model_validate(suggestion.arguments)


@pytest.mark.parametrize(
    ("question", "tool", "arguments"),
    [
        ("Which items are running low on stock?", "get_low_stock", {}),
        ("Which items need replenishment before today's first dispatch window?", "get_order_stockout_risks",
         {"view": "replenish_before_first_dispatch"}),
        (f"Which of today{chr(0x2019)}s orders are at risk?", "get_order_stockout_risks", {"view": "orders"}),
        ("Which outlet orders are delayed?", "get_delayed_orders", {}),
        ("Show me the top 5 fastest-moving items", "get_top_movers", {"limit": 5}),
        ("Which zones have the highest stock levels?", "get_zone_summary", {}),
        ("Where are we close to capacity?", "get_capacity_hotspots", {}),
        ("Are slow movers sitting in the fast-pick zone?", "get_slow_in_prime", {}),
    ],
)  # fmt: skip
def test_persona_questions_route_on_the_fast_path(
    question: str, tool: str, arguments: dict[str, object]
) -> None:
    route = fast_path.route(question)
    assert route is not None and (route.tool, route.arguments) == (tool, arguments)
    TOOLS[tool].input_model.model_validate(route.arguments)


@pytest.mark.parametrize(
    "question",
    [
        "Which items are running low on stock in zone B?",  # a condition the fixed question lacks
        "Which items are not running low on stock?",
        "Show me the top 10 fastest-moving items",  # another number: the model reads it
        "Where are we close to capacity in cold storage?",
    ],
)
def test_qualified_questions_go_to_the_model(question: str) -> None:
    assert fast_path.route(question) is None
