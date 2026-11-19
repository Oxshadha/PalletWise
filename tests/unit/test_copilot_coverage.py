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
        ("Show me the top 5 movers", "get_top_movers", {"limit": 5}),
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
        "Show me the top 10 movers in zone B",  # a number and a zone
        "Which items are running low on stock this week?",  # a date
        "Which items are running low on stock in beverages?",  # a category
        "Which outlet orders are delayed since Monday?",
        "Which of today's orders are at risk for Keells?",  # an outlet
        "Which items are running low on stock and have no reserve?",  # a second condition
        "Show me the top 5 movers by value",
        "Where are we close to capacity above 95 percent?",
    ],
)
def test_qualified_questions_go_to_the_model(question: str) -> None:
    assert fast_path.route(question) is None


def test_the_question_just_answered_is_not_suggested_again() -> None:
    from palletwise.contracts.results import Evidence, ResultEnvelope, ResultStatus

    evidence = Evidence(as_of="2026-04-01T00:30:00Z", time_zone="Asia/Colombo", settings_version_id=None,
                        data_as_of=None, freshness=(), how_computed=())  # type: ignore[arg-type]  # fmt: skip
    result = ResultEnvelope(
        status=ResultStatus.OK, rows=({"sku": "SKU-1"},), evidence=evidence,
        normalized_request={"tool": "get_order_stockout_risks", "view": "replenish_before_first_dispatch"},
    )  # fmt: skip
    found = suggestions.suggest("get_order_stockout_risks", result, set(TOOLS))
    assert suggestions.REPLENISH_FIRST not in found  # the view just answered
    assert suggestions.ORDERS_AT_RISK in found  # another view of the same tool is a real next question


def _sample(columns: tuple[object, ...]) -> dict[str, object]:
    from datetime import UTC, datetime
    from decimal import Decimal

    values = {"integer": 7, "number": Decimal("2.5"), "boolean": False,
              "datetime": datetime(2026, 4, 1, 3, tzinfo=UTC)}  # fmt: skip
    row: dict[str, object] = {}
    for c in columns:
        key, kind = c.key, c.type  # type: ignore[attr-defined]
        row[key] = values.get(kind, f"{key.upper()[:3]}-1")
    return row


@pytest.mark.parametrize(
    ("tool", "request_extra", "columns"),
    [
        ("get_low_stock", {}, "LOW_STOCK_COLUMNS"),
        ("get_top_movers", {"days": 30, "limit": 10}, "TOP_MOVER_COLUMNS"),
        ("get_dead_stock", {}, "DEAD_STOCK_COLUMNS"),
        ("get_slow_in_prime", {}, "SLOW_IN_PRIME_COLUMNS"),
        ("get_zone_summary", {}, "ZONE_COLUMNS"),
        ("get_capacity_hotspots", {}, "HOTSPOT_COLUMNS"),
        ("get_delayed_orders", {"since_days": 0}, "DELAYED_COLUMNS"),
        ("get_order_stockout_risks", {"view": "products"}, "RISK_PRODUCT_COLUMNS"),
        ("get_order_stockout_risks", {"view": "orders"}, "RISK_ORDER_COLUMNS"),
        ("get_order_stockout_risks", {"view": "replenish_before_first_dispatch"}, "REPLENISH_COLUMNS"),
    ],
)
def test_every_template_renders_a_grounded_answer_without_a_model(
    tool: str, request_extra: dict[str, object], columns: str
) -> None:
    """Gemini unavailable or the budget spent: path B answers from the result alone (agent spec 3.2)."""
    from datetime import UTC, datetime

    from palletwise.agent.guardrails import grounding_problems
    from palletwise.contracts.results import Evidence, ResultEnvelope, ResultStatus
    from palletwise.semantic import rules

    cols = getattr(rules, columns)
    evidence = Evidence(as_of=datetime(2026, 4, 1, 0, 30, tzinfo=UTC), time_zone="Asia/Colombo",
                        settings_version_id=None, data_as_of=None, freshness=(), how_computed=())  # fmt: skip
    result = ResultEnvelope(
        status=ResultStatus.OK, columns=cols, rows=(_sample(cols), _sample(cols)), evidence=evidence,
        normalized_request={"tool": tool, **request_extra},
    )  # fmt: skip
    text = templates.template_answer(tool, result)
    assert text and text != "Here is the result."
    assert not any(d in text for d in (chr(0x2013), chr(0x2014)))
    assert grounding_problems(text, [result], "") == [], text
