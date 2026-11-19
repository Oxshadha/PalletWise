"""The copilot runtime with deterministic models (agent spec 2, 3, 6, 9.1; NFR-REL-03, NFR-CST-02)."""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from palletwise.agent.events import RunEvent
from palletwise.agent.runtime import (
    BUDGET_TEXT,
    NO_MODEL_TEXT,
    AgentProfile,
    CopilotRuntime,
    ModelCall,
    RunRequest,
    RunState,
)
from palletwise.agent.suggestions import Suggestion
from palletwise.contracts.results import (
    Column,
    EntityRef,
    Evidence,
    Highlight,
    HighlightReason,
    ResultEnvelope,
    ResultStatus,
    Severity,
    StreamFreshness,
)
from palletwise.llm import ResolvedModel
from palletwise.tools.catalog import v1_tools
from palletwise.tools.executor import Outcome, RunContext, RunStopped, ToolCallResult

AS_OF = datetime(2026, 4, 1, 0, 30, tzinfo=UTC)
BIN_A, BIN_B = uuid4(), uuid4()


def locations_result() -> ResultEnvelope:
    return ResultEnvelope(
        status=ResultStatus.OK,
        columns=(
            Column(key="bin_code", label="Location", type="text"),
            Column(key="on_hand", label="On hand", type="integer"),
        ),
        rows=(
            {"bin_code": "A8-01-02", "on_hand": 7, "available": 7},
            {"bin_code": "B1-02-02", "on_hand": 1, "available": 1},
        ),
        highlights=tuple(
            Highlight(target_type="bin", target_id=b, reason=HighlightReason.LOCATION, severity=Severity.INFO)
            for b in (BIN_A, BIN_B)
        ),
        evidence=Evidence(
            as_of=AS_OF,
            time_zone="Asia/Colombo",
            settings_version_id=None,
            data_as_of=AS_OF,
            freshness=(StreamFreshness(stream="MASTER", data_as_of=AS_OF),),
            how_computed=("x",),
        ),
        entities=(
            EntityRef(type="product", id=uuid4(), label="SKU-102"),
            EntityRef(type="bin", id=BIN_A, label="A8-01-02"),
            EntityRef(type="bin", id=BIN_B, label="B1-02-02"),
        ),
        normalized_request={"tool": "find_product_locations", "sku": "SKU-102"},
    )


def contents_result() -> ResultEnvelope:
    return locations_result().model_copy(
        update={
            "rows": ({"sku": "SKU-102", "name": "Wireless Mouse", "on_hand": 7},),
            "normalized_request": {"tool": "get_bin_contents", "bin_code": "A8-01-02"},
        }
    )


@dataclass
class FakeGateway:
    outcome: Outcome = Outcome.OK
    raises: Exception | None = None
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=lambda: [])

    def execute(self, run: RunContext, name: str, arguments: dict[str, Any]) -> ToolCallResult:
        self.calls.append((name, arguments))
        if self.raises:
            raise self.raises
        if self.outcome is not Outcome.OK:
            return ToolCallResult(self.outcome, name, message="not permitted")
        result = locations_result() if name == "find_product_locations" else contents_result()
        return ToolCallResult(Outcome.OK, name, result)


@dataclass
class FakeMeter:
    budget: int = 1_000_000
    reserved: int = 0
    settled: list[ModelCall] = field(default_factory=lambda: [])

    def reserve(self, estimate: int) -> bool:
        if self.reserved + estimate > self.budget:
            return False
        self.reserved += estimate
        return True

    def settle(self, estimate: int, call: ModelCall) -> None:
        self.reserved -= estimate
        self.settled.append(call)


class FakeProvider:
    def __init__(self, **models: FunctionModel | None) -> None:
        self._models = models

    def model(self, logical: str) -> ResolvedModel | None:
        model = self._models.get(logical.replace("-", "_"))
        return ResolvedModel(model, logical, "test") if model else None


def say(*texts: str) -> FunctionModel:
    """A model that answers with each text in turn."""
    remaining = list(texts)

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(remaining.pop(0) if len(remaining) > 1 else remaining[0])])

    return FunctionModel(fn)


def tool_then_say(
    text: str, tool: str = "find_product_locations", args: dict[str, Any] | None = None
) -> FunctionModel:
    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        returned = any(isinstance(p, ToolReturnPart) for m in messages for p in getattr(m, "parts", []))
        if not returned:
            return ModelResponse(parts=[ToolCallPart(tool, args or {"sku": "SKU-102"})])
        return ModelResponse(parts=[TextPart(text)])

    return FunctionModel(fn)


def request(question: str, suggestion: Suggestion | None = None) -> RunRequest:
    run = RunContext(
        run_id=uuid4(),
        trace_id="ab" * 16,
        subject="u",
        role="warehouse_manager",
        tenant=uuid4(),
        sites=(uuid4(),),
        site=uuid4(),
    )
    agent = AgentProfile("copilot", "1", "gemini-flash", None, "system.v1", 5)
    return RunRequest(question=question, run=run, agent=agent, suggestion=suggestion)


def run(
    question: str,
    provider: FakeProvider,
    gateway: FakeGateway | None = None,
    meter: FakeMeter | None = None,
    suggestion: Suggestion | None = None,
) -> tuple[list[RunEvent], RunState, FakeGateway, FakeMeter]:
    gateway, meter, state = gateway or FakeGateway(), meter or FakeMeter(), RunState()
    runtime = CopilotRuntime(v1_tools(), gateway, provider, run_timeout=10)

    async def collect() -> list[RunEvent]:
        return [e async for e in runtime.run(request(question, suggestion), meter, state)]

    return asyncio.run(collect()), state, gateway, meter


def types(events: list[RunEvent]) -> list[str]:
    return [e.type for e in events]


def text_of(events: list[RunEvent]) -> str:
    return "".join(e.data["delta"] for e in events if e.type == "TEXT_MESSAGE_CONTENT")


# ---------------------------------------------------------------- paths A and B


def test_fast_path_without_a_model_uses_the_template() -> None:
    events, state, gateway, _ = run("Where is SKU-102 stored?", FakeProvider())
    assert types(events) == [
        "RUN_STARTED",
        "TOOL_CALL_START",
        "TOOL_CALL_END",
        "pw.result",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "pw.suggestions",
        "RUN_FINISHED",
    ]
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    assert events[0].data["path"] == "B" and state.path == "B" and state.outcome == "ANSWER"
    assert gateway.calls == [("find_product_locations", {"sku": "SKU-102"})]
    assert text_of(events) == "SKU-102 is stored in 2 locations: A8-01-02 (7 on hand), B1-02-02 (1 on hand)."
    assert state.suggestions[0].label == "What else is in A8-01-02?"
    assert len(events[3].data["result"]["highlights"]) == 2


def test_path_a_words_with_the_small_model() -> None:
    events, state, _, meter = run(
        "where is SKU-102 stored", FakeProvider(gemini_flash_lite=say("SKU-102 has 7 units in A8-01-02."))
    )
    assert events[0].data["path"] == "A" and state.path == "A"
    assert text_of(events) == "SKU-102 has 7 units in A8-01-02."
    assert len(meter.settled) == 1 and meter.settled[0].purpose == "WORDING" and meter.reserved == 0


def test_ungrounded_wording_is_retried_once_then_replaced_by_the_template() -> None:
    events, state, _, meter = run(
        "Where is SKU-102 stored?", FakeProvider(gemini_flash_lite=say("SKU-102 has 70 units."))
    )
    assert text_of(events).startswith("SKU-102 is stored in 2 locations") and state.path == "B"
    assert len(meter.settled) == 2


def test_budget_exhausted_falls_back_to_the_template_and_says_so() -> None:
    events, state, _, meter = run(
        "Where is SKU-102 stored?", FakeProvider(gemini_flash_lite=say("x")), meter=FakeMeter(budget=10)
    )
    assert state.budget_exhausted and state.path == "B" and text_of(events).endswith(BUDGET_TEXT)
    assert meter.settled == []


def test_a_selected_suggestion_is_routed_but_still_executed_through_the_gateway() -> None:
    chip = Suggestion("What else is in A8-01-02?", "get_bin_contents", {"bin_code": "A8-01-02"})
    events, _, gateway, _ = run("What else is in A8-01-02?", FakeProvider(), suggestion=chip)
    assert gateway.calls == [("get_bin_contents", {"bin_code": "A8-01-02"})]
    assert text_of(events).startswith("A8-01-02 holds 1 product.")


def test_an_unexpected_failure_ends_the_run_with_an_error_event() -> None:
    gateway = FakeGateway()
    gateway.execute = lambda *a: ToolCallResult(Outcome.OK, "find_product_locations", "not an envelope")  # type: ignore[method-assign]
    events, state, _, _ = run("Where is SKU-102 stored?", FakeProvider(), gateway=gateway)
    assert types(events)[-1] == "RUN_ERROR" and events[-1].data["code"] == "INTERNAL"
    assert state.outcome == "ERROR"


def test_a_suggestion_naming_an_unknown_tool_is_not_routed() -> None:
    chip = Suggestion("x", "drop_everything", {})
    events, state, gateway, _ = run("x", FakeProvider(), suggestion=chip)
    assert gateway.calls == [] and state.outcome == "DECLINE" and text_of(events) == NO_MODEL_TEXT


# ---------------------------------------------------------------- path C


def test_model_path_streams_the_result_before_the_text() -> None:
    model = tool_then_say("SKU-102 is in A8-01-02 with 7 units and in B1-02-02 with 1 unit.")
    events, state, gateway, meter = run(
        "Which bins hold the wireless mouse?", FakeProvider(gemini_flash=model)
    )
    order = types(events)
    assert events[0].data["path"] == "C" and order.index("pw.result") < order.index("TEXT_MESSAGE_CONTENT")
    assert state.outcome == "ANSWER" and gateway.calls == [("find_product_locations", {"sku": "SKU-102"})]
    assert [c.purpose for c in meter.settled] == ["AGENT", "AGENT"] and meter.reserved == 0
    assert state.entities and state.entities[0]["label"] == "SKU-102"


def test_ungrounded_model_answer_falls_back_to_the_template() -> None:
    events, state, _, _ = run(
        "Which bins hold the wireless mouse?", FakeProvider(gemini_flash=tool_then_say("It has 99 units."))
    )
    assert text_of(events).startswith("SKU-102 is stored in 2 locations") and state.outcome == "ANSWER"


def test_an_identifier_not_in_the_results_is_not_grounded() -> None:
    events, _, _, _ = run(
        "Which bins hold the mouse?", FakeProvider(gemini_flash=tool_then_say("It is in Z9-99-99."))
    )
    assert "Z9-99-99" not in text_of(events)


def test_the_agent_stops_after_five_tool_calls() -> None:
    def loop(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart("find_product_locations", {"sku": "SKU-102"})])

    events, state, gateway, _ = run("keep looking forever", FakeProvider(gemini_flash=FunctionModel(loop)))
    assert len(gateway.calls) <= 5 and state.outcome == "ANSWER" and "RUN_FINISHED" in types(events)


def test_no_route_and_no_model_declines_with_starters() -> None:
    events, state, gateway, _ = run("What is the meaning of life?", FakeProvider())
    assert state.outcome == "DECLINE" and state.path == "B" and gateway.calls == []
    assert text_of(events) == NO_MODEL_TEXT and state.suggestions[0].label == "Where is SKU-102 stored?"


def test_denied_tool_call_is_reported_as_denied() -> None:
    events, state, _, _ = run(
        "Where is SKU-102 stored?", FakeProvider(), gateway=FakeGateway(outcome=Outcome.DENIED)
    )
    assert state.outcome == "DENIED" and "permission" in text_of(events)


def test_suspension_mid_run_ends_with_run_error() -> None:
    gateway = FakeGateway(raises=RunStopped("suspended"))
    events, state, _, _ = run("Where is SKU-102 stored?", FakeProvider(), gateway=gateway)
    assert types(events)[-1] == "RUN_ERROR" and "RUN_FINISHED" not in types(events)
    assert events[-1].data["code"] == "COPILOT_SUSPENDED" and state.error_code == "COPILOT_SUSPENDED"


def test_model_failure_without_results_declines() -> None:
    def broken(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise RuntimeError("provider down")

    _, state, _, meter = run("Which bins hold the mouse?", FakeProvider(gemini_flash=FunctionModel(broken)))
    assert state.outcome == "DECLINE" and state.error_code == "MODEL_UNAVAILABLE"
    assert meter.reserved == 0 and meter.settled[0].outcome == "ERROR"


def test_dashes_are_normalized_in_answers() -> None:
    dash = chr(0x2014)
    events, _, _, _ = run(
        "Where is SKU-102 stored?",
        FakeProvider(gemini_flash_lite=say(f"SKU-102 {dash} 7 units in A8-01-02.")),
    )
    assert dash not in text_of(events)


def test_cancelling_the_consumer_stops_the_model_call() -> None:
    started = asyncio.Event()

    async def slow(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        started.set()
        await asyncio.sleep(30)
        return ModelResponse(parts=[TextPart("late")])

    meter, state = FakeMeter(), RunState()
    runtime = CopilotRuntime(
        v1_tools(), FakeGateway(), FakeProvider(gemini_flash=FunctionModel(slow)), run_timeout=60
    )

    async def scenario() -> None:
        async def consume() -> None:
            async for _ in runtime.run(request("Which bins hold the mouse?"), meter, state):
                pass

        task = asyncio.create_task(consume())
        await asyncio.wait_for(started.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert meter.reserved == 0 and meter.settled and meter.settled[0].outcome == "ERROR"


def test_an_answer_cut_off_at_the_output_limit_is_never_shown() -> None:
    def cut(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("SKU-102 is stored in")], finish_reason="length")

    events, state, _, _ = run("Where is SKU-102 stored?", FakeProvider(gemini_flash_lite=FunctionModel(cut)))
    assert text_of(events).startswith("SKU-102 is stored in 2 locations") and state.path == "B"


def test_reservations_cover_the_whole_output_cap() -> None:
    meter = FakeMeter()
    seen: list[int] = []
    original = meter.reserve

    def reserve(estimate: int) -> bool:
        seen.append(estimate)
        return original(estimate)

    meter.reserve = reserve  # type: ignore[method-assign]
    run(
        "Where is SKU-102 stored?",
        FakeProvider(gemini_flash_lite=say("SKU-102 is in A8-01-02.")),
        meter=meter,
    )
    assert seen and all(estimate > 400 for estimate in seen)  # input estimate plus the 400 token cap
