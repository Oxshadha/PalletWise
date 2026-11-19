"""Deterministic answer text for path B and for grounding fallbacks (agent spec 3.2, 6).

Templates only restate values that are in the result; they never compute new numbers.
"""

from collections.abc import Callable
from typing import Any

from palletwise.contracts.results import ResultEnvelope, ResultStatus

MAX_LISTED = 3


def _plural(count: int, one: str, many: str) -> str:
    return one if count == 1 else many


def _how_many(result: ResultEnvelope) -> str:
    """The row count, or "more than" it when the result stopped at the row limit."""
    return f"more than {result.row_count}" if result.truncated else str(result.row_count)


def _product_locations(result: ResultEnvelope) -> str:
    sku = str(result.normalized_request.get("sku"))
    count = result.row_count
    listed = ", ".join(f"{r['bin_code']} ({r['on_hand']} on hand)" for r in result.rows[:MAX_LISTED])
    more = " and more in the table" if count > MAX_LISTED else ""
    noun = _plural(count, "location", "locations")
    return f"{sku} is stored in {_how_many(result)} {noun}: {listed}{more}."


def _bin_contents(result: ResultEnvelope) -> str:
    code = str(result.normalized_request.get("bin_code"))
    top = result.rows[0]
    count = result.row_count
    return (
        f"{code} holds {_how_many(result)} {_plural(count, 'product', 'products')}. "
        f"The most units are {top['sku']} ({top['name']}) with {top['on_hand']} on hand."
    )


def _first(result: ResultEnvelope) -> dict[str, Any]:
    return result.rows[0]


def _low_stock(result: ResultEnvelope) -> str:
    r, n = _first(result), result.row_count
    return (
        f"{_how_many(result)} {_plural(n, 'product is', 'products are')} at or below the minimum in "
        f"{_plural(n, 'its', 'their')} pick location. {r['sku']} has {r['available']} available in "
        f"{r['bin_code']} against a minimum of {r['min_qty']}."
    )


def _top_movers(result: ResultEnvelope) -> str:
    r = _first(result)
    days = result.normalized_request.get("days")
    return (
        f"The fastest mover over the last {days} days is {r['sku']} ({r['name']}), with "
        f"{r['demand_units']} units picked. The table lists {_how_many(result)} products."
    )


def _dead_stock(result: ResultEnvelope) -> str:
    r, n = _first(result), result.row_count
    picked = (
        "never picked"
        if r["days_since_last_pick"] is None
        else f"last picked {r['days_since_last_pick']} days ago"
    )
    return (
        f"{_how_many(result)} {_plural(n, 'product has', 'products have')} gone longer than the dead "
        f"stock period without a pick. The most value is in {r['sku']}: {r['storable_on_hand']} units, "
        f"{picked}."
    )


def _slow_in_prime(result: ResultEnvelope) -> str:
    r, n = _first(result), result.row_count
    picked = "has never been picked" if r["days_since_last_pick"] is None else (
        f"was last picked {r['days_since_last_pick']} days ago")  # fmt: skip
    return (
        f"Yes: {_how_many(result)} fast-pick {_plural(n, 'location holds', 'locations hold')} slow "
        f"stock. {r['sku']} in {r['bin_code']} {picked}."
    )


def _zone_summary(result: ResultEnvelope) -> str:
    r = _first(result)
    return (
        f"Zone {r['zone_code']} ({r['zone_name']}) holds the most stock, with {r['on_hand']} units on hand."
    )


def _capacity(result: ResultEnvelope) -> str:
    """Says why each zone is flagged: a zone with locations over capacity is not "close to
    capacity" as a whole (the browser review found "close to capacity at 6.2 percent")."""
    r, n = _first(result), result.row_count
    over = r["over_capacity_bins"]
    zone = f"Zone {r['zone_code']} ({r['zone_name']})"
    detail = (
        f"{zone} has {over} {_plural(over, 'location', 'locations')} over capacity, while the zone as a "
        f"whole is at {r['utilization_pct']} percent"
        if over
        else f"{zone} is at {r['utilization_pct']} percent of its capacity"
    )
    return f"{_how_many(result)} {_plural(n, 'zone needs', 'zones need')} attention for capacity. {detail}."


def _delayed(result: ResultEnvelope) -> str:
    r, n = _first(result), result.row_count
    state = "still open" if r["status"] != "SHIPPED" else "shipped late"
    return (
        f"{_how_many(result)} {_plural(n, 'order is', 'orders are')} delayed. {r['order_no']} for "
        f"{r['customer_code']} is {r['hours_late']} hours late and {state}."
    )


def _stockout(result: ResultEnvelope) -> str:
    r, n = _first(result), result.row_count
    view = result.normalized_request.get("view", "products")
    if view == "orders":
        return (
            f"{_how_many(result)} open {_plural(n, 'order is', 'orders are')} at risk of a stockout. "
            f"{r['order_no']} for {r['customer_code']} is due first."
        )
    if view == "replenish_before_first_dispatch":
        return (
            f"{_how_many(result)} {_plural(n, 'product needs', 'products need')} replenishing before "
            f"the next dispatch. {r['sku']} is short {r['shortfall']} units, with {r['reserve_available']} "
            "in reserve."
        )
    fix = (
        "a replenishment from reserve covers it"
        if r["fixable"]
        else f"{r['true_shortage']} units are truly short"
    )
    return (
        f"{_how_many(result)} {_plural(n, 'product is', 'products are')} short for orders due soon. "
        f"{r['sku']} is short {r['shortfall']} units; {fix}."
    )


def _query(result: ResultEnvelope) -> str:
    return f"The query returned {_how_many(result)} {_plural(result.row_count, 'row', 'rows')}."


TEMPLATES: dict[str, Callable[[ResultEnvelope], str]] = {
    "find_product_locations": _product_locations,
    "get_bin_contents": _bin_contents,
    "get_low_stock": _low_stock,
    "get_top_movers": _top_movers,
    "get_dead_stock": _dead_stock,
    "get_slow_in_prime": _slow_in_prime,
    "get_zone_summary": _zone_summary,
    "get_capacity_hotspots": _capacity,
    "get_delayed_orders": _delayed,
    "get_order_stockout_risks": _stockout,
    "query_inventory": _query,
}


def template_answer(tool: str, result: ResultEnvelope) -> str:
    """The answer for a result: its message for every non-OK status, else the tool template."""
    if result.status is not ResultStatus.OK or tool not in TEMPLATES:
        return result.message or "Here is the result."
    return TEMPLATES[tool](result)
