"""Run stream events (agent spec 9.1): AG-UI event types plus PalletWise events.

Every event of a run carries a sequence number, so the client can ignore duplicates and
out-of-order events.
"""

from dataclasses import dataclass, field
from typing import Any, Literal

EventType = Literal[
    "RUN_STARTED",
    "pw.status",
    "TOOL_CALL_START",
    "TOOL_CALL_END",
    "pw.result",
    "pw.headline",
    "TEXT_MESSAGE_START",
    "TEXT_MESSAGE_CONTENT",
    "TEXT_MESSAGE_END",
    "pw.suggestions",
    "RUN_FINISHED",
    "RUN_ERROR",
]


@dataclass(frozen=True, slots=True)
class RunEvent:
    type: EventType
    seq: int
    data: dict[str, Any]


@dataclass
class EventCounter:
    """Numbers the events of one run from 1."""

    _next: int = field(default=1)

    def make(self, type_: EventType, **data: Any) -> RunEvent:
        event = RunEvent(type_, self._next, data)
        self._next += 1
        return event
