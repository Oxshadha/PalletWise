"""Deterministic answer text for path B and for grounding fallbacks (agent spec 3.2, 6).

Templates only restate values that are in the result; they never compute new numbers.
"""

from collections.abc import Callable
from typing import Any

from palletwise.agent.guardrails import instruction_echo
from palletwise.contracts.results import ResultEnvelope, ResultStatus

MAX_LISTED = 3


def _plural(count: int, one: str, many: str) -> str:
    return one if count == 1 else many


def _product(row: dict[str, Any]) -> str:
    """A product by SKU and name; by SKU alone when the name carries instruction text, so even a
    template never repeats an injection found in data (FR-COP-12; the table still shows the name)."""
    name = str(row.get("name") or "")
    if not name or instruction_echo(name, ""):
        return str(row["sku"])
    return f"{row['sku']} ({name})"


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
        f"The most units are {_product(top)} with {top['on_hand']} on hand."
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
        f"The fastest mover over the last {days} days is {_product(r)}, with "
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


def _capacity_zone(r: dict[str, Any]) -> str:
    zone, over = f"Zone {r['zone_code']} ({r['zone_name']})", r["over_capacity_bins"]
    if over:
        return (
            f"{zone} has {over} {_plural(over, 'location', 'locations')} over capacity, while the zone "
            f"as a whole is at {r['utilization_pct']} percent"
        )
    return f"{zone} is at {r['utilization_pct']} percent of its capacity"


def _capacity(result: ResultEnvelope) -> str:
    """Says why each zone is flagged: a zone with locations over capacity is not "close to
    capacity" as a whole (the browser review found "close to capacity at 6.2 percent")."""
    n = result.row_count
    details = ". ".join(_capacity_zone(r) for r in result.rows[:MAX_LISTED])
    return f"{_how_many(result)} {_plural(n, 'zone needs', 'zones need')} attention for capacity. {details}."


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


QUERY_NOUNS = {
    "product": ("product", "products"),
    "category": ("category", "categories"),
    "brand": ("brand", "brands"),
    "zone": ("zone", "zones"),
    "rack": ("rack", "racks"),
    "bin": ("location", "locations"),
    "customer": ("outlet", "outlets"),
}


def _query(result: ResultEnvelope) -> str:
    """The count and the leading rows by name with their first measure, so a query answer says
    what it found (the baseline's "The query returned 8 rows." answered nothing)."""
    first, n = result.columns[0], result.row_count
    if first.type in ("integer", "number"):
        # No breakdown: the result is totals, one value per measure (the baseline said
        # "1 row found, led by 5728960.27").
        r = result.rows[0]
        values = "; ".join(
            f"{c.label.lower()} {r[c.key]}" for c in result.columns if c.type in ("integer", "number")
        )
        return f"In total: {values}."
    measure = next((c for c in result.columns[1:] if c.type in ("integer", "number")), None)
    one, many = QUERY_NOUNS.get(first.key, ("row", "rows"))
    if measure is None:
        listed = ", ".join(str(r[first.key]) for r in result.rows[:MAX_LISTED])
    else:
        label = measure.label.lower()
        listed = ", ".join(f"{r[first.key]} ({label} {r[measure.key]})" for r in result.rows[:MAX_LISTED])
    return f"{_how_many(result)} {_plural(n, one, many)} found, led by {listed}."


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


# ---------------------------------------------------------------- key facts (ADR 0001 section 4, 6)
#
# The facts an answer must state for its result: the ones its template states. Model wording that
# drops one is not shown; the template, which states them all, is shown instead.


def _first_value(result: ResultEnvelope, key: str) -> list[str]:
    return [str(result.rows[0][key])] if result.rows and result.rows[0].get(key) is not None else []


def key_facts(tool: str, result: ResultEnvelope) -> tuple[str, ...]:
    if result.status is not ResultStatus.OK or not result.rows:
        return ()
    r = result.rows[0]
    facts: list[str]
    match tool:
        case "find_product_locations":
            facts = [str(result.normalized_request.get("sku") or r.get("sku") or "")]
        case "get_bin_contents":
            facts = [str(result.normalized_request.get("bin_code"))]
        case "get_low_stock":
            facts = [_how_many(result), str(r["sku"])]
        case "get_top_movers":
            facts = [str(r["sku"]), str(r["demand_units"])]
        case "get_dead_stock":
            facts = [str(r["sku"])]
        case "get_slow_in_prime":
            facts = [str(r["sku"]), str(r["bin_code"])]
        case "get_zone_summary":
            facts = [str(r["zone_name"]), str(r["on_hand"])]
        case "get_capacity_hotspots":
            facts = [str(z["zone_name"]) for z in result.rows[:MAX_LISTED]]
            if any(z["over_capacity_bins"] for z in result.rows[:MAX_LISTED]):
                facts.append("over capacity")
        case "get_delayed_orders":
            facts = [str(r["order_no"])]
        case "get_order_stockout_risks":
            facts = _first_value(result, "order_no") or _first_value(result, "sku")
        case "query_inventory":
            if not result.columns:
                facts = []
            elif result.columns[0].type in ("integer", "number"):
                facts = [str(r[result.columns[0].key])]  # totals: the first measure's value
            else:
                facts = [_how_many(result), str(r[result.columns[0].key])]
        case _:
            facts = []
    return tuple(f for f in facts if f and f != "None")


def template_answer(tool: str, result: ResultEnvelope) -> str:
    """The answer for a result: its message for every non-OK status, else the tool template."""
    if result.status is not ResultStatus.OK or tool not in TEMPLATES:
        return result.message or "Here is the result."
    return TEMPLATES[tool](result)
