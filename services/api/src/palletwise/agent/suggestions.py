"""Suggestions from rules, never from the model (agent spec 11, FR-COP-07).

Each suggestion names a tool and its arguments. When one is selected, the server treats it as
untrusted input: validated and authorized like any other call.
"""

from collections.abc import Callable, Collection
from dataclasses import asdict, dataclass
from typing import Any

from palletwise.contracts.results import ResultEnvelope, ResultStatus

MAX_SUGGESTIONS = 3


@dataclass(frozen=True, slots=True)
class Suggestion:
    label: str
    tool: str
    arguments: dict[str, Any]

    def as_json(self) -> dict[str, Any]:
        return asdict(self)


# UX spec 6: each starter runs on the fast path; shown only when its capability is built.
STARTERS = (Suggestion("Where is SKU-102 stored?", "find_product_locations", {"sku": "SKU-102"}),)


def _after_product_locations(result: ResultEnvelope) -> list[Suggestion]:
    if result.status is ResultStatus.AMBIGUOUS:
        return [Suggestion(f"Where is {r['sku']} stored?", "find_product_locations", {"sku": r["sku"]})
                for r in result.rows]  # fmt: skip
    if result.status is ResultStatus.OK:
        top = result.rows[0]["bin_code"]
        return [Suggestion(f"What else is in {top}?", "get_bin_contents", {"bin_code": top})]
    return []


def _after_bin_contents(result: ResultEnvelope) -> list[Suggestion]:
    if result.status is not ResultStatus.OK:
        return []
    return [Suggestion(f"Where else is {r['sku']} stored?", "find_product_locations", {"sku": r["sku"]})
            for r in result.rows[:2]]  # fmt: skip


RULES: dict[str, Callable[[ResultEnvelope], list[Suggestion]]] = {
    "find_product_locations": _after_product_locations,
    "get_bin_contents": _after_bin_contents,
}


def suggest(tool: str | None, result: ResultEnvelope | None, available: Collection[str]) -> list[Suggestion]:
    """Suggestions after a result; the starters when there is none. Only built tools appear."""
    found = RULES[tool](result) if tool in RULES and result is not None else list(STARTERS)
    return [s for s in found if s.tool in available][:MAX_SUGGESTIONS]
