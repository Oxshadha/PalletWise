"""Deterministic answer text for path B and for grounding fallbacks (agent spec 3.2, 6).

Templates only restate values that are in the result; they never compute new numbers.
"""

from collections.abc import Callable

from palletwise.contracts.results import ResultEnvelope, ResultStatus

MAX_LISTED = 3


def _plural(count: int, one: str, many: str) -> str:
    return one if count == 1 else many


def _product_locations(result: ResultEnvelope) -> str:
    sku = str(result.normalized_request.get("sku"))
    count = result.row_count
    listed = ", ".join(f"{r['bin_code']} ({r['on_hand']} on hand)" for r in result.rows[:MAX_LISTED])
    more = " and more in the table" if count > MAX_LISTED else ""
    return f"{sku} is stored in {count} {_plural(count, 'location', 'locations')}: {listed}{more}."


def _bin_contents(result: ResultEnvelope) -> str:
    code = str(result.normalized_request.get("bin_code"))
    top = result.rows[0]
    count = result.row_count
    return (
        f"{code} holds {count} {_plural(count, 'product', 'products')}. "
        f"The most units are {top['sku']} ({top['name']}) with {top['on_hand']} on hand."
    )


TEMPLATES: dict[str, Callable[[ResultEnvelope], str]] = {
    "find_product_locations": _product_locations,
    "get_bin_contents": _bin_contents,
}


def template_answer(tool: str, result: ResultEnvelope) -> str:
    """The answer for a result: its message for every non-OK status, else the tool template."""
    if result.status is not ResultStatus.OK or tool not in TEMPLATES:
        return result.message or "Here is the result."
    return TEMPLATES[tool](result)
