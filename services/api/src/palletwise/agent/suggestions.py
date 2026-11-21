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
STARTERS = (
    Suggestion("Where is SKU-102 stored?", "find_product_locations", {"sku": "SKU-102"}),
    Suggestion("Which items are running low on stock?", "get_low_stock", {}),
    Suggestion("Which of today's orders are at risk?", "get_order_stockout_risks", {"view": "orders"}),
    Suggestion("Where are we close to capacity?", "get_capacity_hotspots", {}),
)


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


def _where(sku: str) -> Suggestion:
    return Suggestion(f"Where is {sku} stored?", "find_product_locations", {"sku": sku})


ORDERS_AT_RISK = Suggestion(
    "Which of today's orders are at risk?", "get_order_stockout_risks", {"view": "orders"}
)
REPLENISH_FIRST = Suggestion(
    "What needs replenishing before the first dispatch?",
    "get_order_stockout_risks",
    {"view": "replenish_before_first_dispatch"},
)
PRODUCTS_SHORT = Suggestion(
    "Which products are short for orders?", "get_order_stockout_risks", {"view": "products"}
)
CAPACITY = Suggestion("Where are we close to capacity?", "get_capacity_hotspots", {})
ZONES = Suggestion("Which zones hold the most stock?", "get_zone_summary", {})
SLOW_IN_PRIME = Suggestion("Are slow movers sitting in the fast-pick zone?", "get_slow_in_prime", {})


def _ok(rule: Callable[[ResultEnvelope], list[Suggestion]]) -> Callable[[ResultEnvelope], list[Suggestion]]:
    """Follow-ups only after a result with rows; otherwise none."""
    return lambda result: rule(result) if result.status is ResultStatus.OK and result.rows else []


def _bin(code: str) -> Suggestion:
    return Suggestion(f"What else is in {code}?", "get_bin_contents", {"bin_code": code})


def _first_sku(result: ResultEnvelope) -> list[Suggestion]:
    sku = result.rows[0].get("sku")
    return [_where(str(sku))] if sku else []


RULES: dict[str, Callable[[ResultEnvelope], list[Suggestion]]] = {
    "find_product_locations": _after_product_locations,
    "get_bin_contents": _after_bin_contents,
    "get_low_stock": _ok(lambda r: [REPLENISH_FIRST, ORDERS_AT_RISK, *_first_sku(r)]),
    "get_top_movers": _ok(lambda r: [*_first_sku(r), SLOW_IN_PRIME]),
    "get_dead_stock": _ok(lambda r: [*_first_sku(r), SLOW_IN_PRIME, CAPACITY]),
    "get_slow_in_prime": _ok(lambda r: [_bin(str(r.rows[0]["bin_code"])), *_first_sku(r)]),
    "get_zone_summary": _ok(lambda r: [CAPACITY, SLOW_IN_PRIME]),
    "get_capacity_hotspots": _ok(lambda r: [ZONES, SLOW_IN_PRIME]),
    "get_delayed_orders": _ok(lambda r: [ORDERS_AT_RISK, PRODUCTS_SHORT]),
    "get_order_stockout_risks": _ok(lambda r: [*_first_sku(r), REPLENISH_FIRST, ORDERS_AT_RISK]),
    "query_inventory": _ok(_first_sku),
    # After one order: where its first product is, then the day's other orders at risk.
    "get_order_status": _ok(lambda r: [*_first_sku(r), ORDERS_AT_RISK]),
    "get_open_exceptions": _ok(lambda r: [ORDERS_AT_RISK, REPLENISH_FIRST]),
    # A custom query has no standard next question.
    "run_governed_query": lambda r: [],
}


def suggest(tool: str | None, result: ResultEnvelope | None, available: Collection[str]) -> list[Suggestion]:
    """Suggestions after a result; the starters when there is none. Only built tools appear."""
    found = RULES[tool](result) if tool in RULES and result is not None else list(STARTERS)

    def repeats(s: Suggestion) -> bool:  # the question just answered is not a next question
        asked = result.normalized_request if result is not None else {}
        return s.tool == tool and all(asked.get(k) == v for k, v in s.arguments.items())

    return [s for s in found if s.tool in available and not repeats(s)][:MAX_SUGGESTIONS]
