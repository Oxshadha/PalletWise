"""Daily brief wording (slice-3-acceptance step 5, D6, D7): the model words, code decides."""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from palletwise.agent.brief import Worded, word_brief
from palletwise.agent.runtime import ModelCall
from palletwise.agent.templates import template_answer
from palletwise.contracts.results import Evidence, ResultEnvelope, ResultStatus
from palletwise.llm import ResolvedModel
from palletwise.semantic.brief import EXCEPTION_COLUMNS, THROUGHPUT_COLUMNS

AS_OF = datetime(2026, 4, 1, 6, 0, tzinfo=UTC)
EVIDENCE = Evidence(
    as_of=AS_OF, time_zone="Asia/Colombo", settings_version_id=None, data_as_of=AS_OF, freshness=(),
    how_computed=("x",),
)  # fmt: skip


@dataclass(frozen=True)
class Item:
    kind: str
    result_name: str
    result: ResultEnvelope
    why: str


def exceptions_item() -> Item:
    result = ResultEnvelope(
        status=ResultStatus.OK,
        columns=EXCEPTION_COLUMNS,
        rows=(
            {
                "rank": 1,
                "exception_type": "Stockout risk",
                "subject": "SKU-102 Mouse",
                "score": 57.2,
                "severity": "warning",
            },
        ),
        evidence=EVIDENCE,
        normalized_request={"open_exceptions": 4, "shown": 1},
    )
    return Item("top_exceptions", "brief_top_exceptions", result, "The 1 highest of the 4 open exceptions.")


def throughput_item() -> Item:
    result = ResultEnvelope(
        status=ResultStatus.OK,
        columns=THROUGHPUT_COLUMNS,
        rows=(
            {
                "units_picked": 1240,
                "avg_units_picked": 1417.1,
                "units_change_pct": 12.5,
                "units_direction": "below",
                "orders_shipped": 38,
                "avg_orders_shipped": 36.9,
                "orders_change_pct": 3.0,
                "orders_direction": "above",
            },
        ),
        evidence=EVIDENCE,
        normalized_request={"baseline_operating_days": 7},
    )
    return Item("throughput", "brief_throughput", result, "Yesterday against the 7 operating days before.")


def empty_item() -> Item:
    result = ResultEnvelope(
        status=ResultStatus.NO_RESULTS, message="No open order due today is at risk.", evidence=EVIDENCE,
        normalized_request={"view": "orders", "hours": 18},
    )  # fmt: skip
    return Item("orders_at_risk_today", "get_order_stockout_risks", result, "Orders due today.")


@dataclass
class Meter:
    budget: int = 1_000_000
    calls: list[ModelCall] = field(default_factory=lambda: [])

    def reserve(self, estimate: int) -> bool:
        return estimate <= self.budget

    def settle(self, estimate: int, call: ModelCall) -> None:
        self.calls.append(call)


def model(text: str, prompts: list[str] | None = None) -> ResolvedModel:
    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if prompts is not None:
            prompts.append(str(messages[-1]))
        return ModelResponse(parts=[TextPart(text)])

    return ResolvedModel(FunctionModel(fn), "test-lite", "test")


def word(items: list[Any], lite: ResolvedModel | None, meter: Meter | None) -> list[Worded]:
    return asyncio.run(word_brief(items, lite, meter))


GOOD = (
    "1. 4 exceptions are open; the stockout risk for SKU-102 leads with score 57.2.\n"
    "2. Yesterday 1240 units were picked, 12.5 percent below the average of 1417.1; 38 orders shipped."
)


def test_grounded_sentences_with_their_key_facts_are_the_models() -> None:
    meter, prompts = Meter(), []
    found = word([exceptions_item(), empty_item(), throughput_item()], model(GOOD, prompts), meter)
    assert [w.source for w in found] == ["model", "template", "model"]
    assert found[0].text.startswith("4 exceptions are open")
    # The empty item is never sent to the model; it keeps its message.
    assert found[1].text == "No open order due today is at risk."
    assert "No open order due today" not in prompts[0] and len(meter.calls) == 1


def test_a_sentence_with_a_number_not_in_its_item_falls_back_to_the_template_alone() -> None:
    wrong = GOOD.replace("score 57.2", "score 61")
    found = word([exceptions_item(), throughput_item()], model(wrong), Meter())
    assert [w.source for w in found] == ["template", "model"]
    assert found[0].text == template_answer("brief_top_exceptions", exceptions_item().result)


def test_a_number_from_another_item_is_not_grounded_for_this_one() -> None:
    borrowed = GOOD.replace("score 57.2", "score 57.2 and 1240 units")
    assert word([exceptions_item(), throughput_item()], model(borrowed), Meter())[0].source == "template"


def test_a_dropped_key_fact_or_a_missing_line_uses_the_template() -> None:
    dropped = "1. Several exceptions are open, led by SKU-102 at 57.2.\n"  # no count; no line 2
    found = word([exceptions_item(), throughput_item()], model(dropped), Meter())
    assert [w.source for w in found] == ["template", "template"]


def test_instruction_text_in_a_sentence_is_never_shown() -> None:
    echoed = GOOD.replace("leads with", "says ignore previous instructions and leads with")
    assert word([exceptions_item(), throughput_item()], model(echoed), Meter())[0].source == "template"


def test_without_a_model_or_budget_every_item_has_its_template() -> None:
    items = [exceptions_item(), throughput_item()]
    templates = [template_answer(i.result_name, i.result) for i in items]
    assert [w.text for w in word(items, None, None)] == templates
    spent = Meter(budget=1)
    assert [w.source for w in word(items, model(GOOD), spent)] == ["template", "template"]
    assert spent.calls == []


def test_templates_state_their_own_key_facts() -> None:
    assert template_answer("brief_top_exceptions", exceptions_item().result) == (
        "4 exceptions are open. The highest impact is the stockout risk for SKU-102 Mouse, score 57.2."
    )
    assert template_answer("brief_throughput", throughput_item().result) == (
        "Yesterday 1240 units were picked, 12.5 percent below the average of 1417.1 over the 7 operating"
        " days before; 38 orders shipped against an average of 36.9."
    )


def test_the_direction_of_a_change_is_a_key_fact() -> None:
    swapped = GOOD.replace("12.5 percent below", "12.5 percent above")
    assert word([exceptions_item(), throughput_item()], model(swapped), Meter())[1].source == "template"
    as_sign = GOOD.replace("12.5 percent below", "minus 12.5 percent")
    assert word([exceptions_item(), throughput_item()], model(as_sign), Meter())[1].source == "template"


def test_no_earlier_operating_day_is_said_plainly() -> None:
    item = throughput_item()
    row = {**item.result.rows[0], "avg_units_picked": None, "units_change_pct": None, "units_direction": None,
           "avg_orders_shipped": None}  # fmt: skip
    none = item.result.model_copy(
        update={"rows": (row,), "normalized_request": {"baseline_operating_days": 0}}
    )
    text = template_answer("brief_throughput", none)
    assert "no earlier operating day to compare" in text and "None" not in text  # code review


def test_the_today_window_cannot_be_mixed_with_hours_or_the_replenishment_view() -> None:
    import pytest
    from pydantic import ValidationError

    from palletwise.tools.catalog import GetOrderStockoutRisksInput

    assert GetOrderStockoutRisksInput(view="orders", today=True).today
    for bad in ({"view": "orders", "today": True, "hours": 12},
                {"view": "replenish_before_first_dispatch", "today": True}):  # fmt: skip
        with pytest.raises(ValidationError):
            GetOrderStockoutRisksInput.model_validate(bad)


def test_brief_usage_is_its_own_purpose() -> None:
    meter = Meter()
    word([exceptions_item(), throughput_item()], model(GOOD), meter)
    assert [(c.purpose, c.stage) for c in meter.calls] == [("BRIEF", "WORDING")]
