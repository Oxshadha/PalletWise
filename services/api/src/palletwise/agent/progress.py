"""Live progress for a run (ADR 0001 change 1): plain-language stages for the Manager.

Labels come only from these fixed lists, so a progress event can never carry model reasoning, a
prompt, a raw tool name, arguments or identifiers (agent spec 9.1, UX spec 7).
"""

from typing import Literal

Stage = Literal["reading", "checking", "reviewing", "writing"]

STAGE_LABELS: dict[Stage, str] = {
    "reading": "Reading the question",
    "checking": "Checking the data",
    "reviewing": "Reviewing the results",
    "writing": "Writing the answer",
}

# What the Manager sees while a tool runs: the business action, never the tool's name.
TOOL_LABELS: dict[str, str] = {
    "find_product_locations": "Finding where the product is stored",
    "get_bin_contents": "Reading what the location holds",
    "get_low_stock": "Checking pick locations against their minimums",
    "get_top_movers": "Adding up recent picks",
    "get_dead_stock": "Looking for stock without recent picks",
    "get_slow_in_prime": "Checking the fast-pick zone for slow stock",
    "get_zone_summary": "Totalling stock by zone",
    "get_capacity_hotspots": "Checking space and capacity",
    "get_delayed_orders": "Checking dispatch deadlines",
    "get_order_stockout_risks": "Comparing open orders with stock",
    "get_order_status": "Reading the order and its lines",
    "get_open_exceptions": "Reading the exceptions inbox",
    "query_inventory": "Running the query on current data",
}


def label(stage: Stage, tool: str | None = None) -> str:
    if stage == "checking" and tool is not None:
        return TOOL_LABELS.get(tool, STAGE_LABELS["checking"])
    return STAGE_LABELS[stage]
