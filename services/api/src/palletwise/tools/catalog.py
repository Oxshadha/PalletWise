"""The tools (agent spec 4.1; Slice 2 acceptance step 2). Each is a thin, typed wrapper around the
semantic layer: no business rule lives here (semantic spec 8). Descriptions are what the model reads.
"""

from collections.abc import Callable
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from palletwise.contracts.results import ResultEnvelope
from palletwise.semantic import locations, rules
from palletwise.tools.executor import ToolContext, ToolRegistry, ToolSpec

SKU_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$"
BIN_CODE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9-]{0,31}$"


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)


class FindProductLocationsInput(StrictInput):
    sku: str | None = Field(default=None, pattern=SKU_PATTERN, description="Exact SKU, for example SKU-102")
    product_query: str | None = Field(
        default=None,
        min_length=2,
        max_length=80,
        description="Product name or words from it, when no SKU is given",
    )

    @model_validator(mode="after")
    def exactly_one(self) -> Self:
        if (self.sku is None) == (self.product_query is None):
            raise ValueError("give exactly one of sku or product_query")
        return self


class GetBinContentsInput(StrictInput):
    bin_code: str = Field(pattern=BIN_CODE_PATTERN, description="Location code, for example A8-01-02")


def find_product_locations(ctx: ToolContext, params: FindProductLocationsInput) -> ResultEnvelope:
    return locations.product_locations(ctx.conn, ctx.site, sku=params.sku, product_query=params.product_query)


def get_bin_contents(ctx: ToolContext, params: GetBinContentsInput) -> ResultEnvelope:
    return locations.bin_contents(ctx.conn, ctx.site, params.bin_code)


FIND_PRODUCT_LOCATIONS = ToolSpec(
    name="find_product_locations",
    description=(
        "Where a product is stored: every location holding it, with on hand, allocated and available "
        "units, and the map highlights for those locations. Give exactly one of sku or product_query. "
        "If several products match a name, the result lists them; ask the user which one. "
        "Does not answer demand, sales or reorder questions."
    ),
    input_model=FindProductLocationsInput,
    scope="inventory.read",
    side_effect="read",
    handler=find_product_locations,
)

GET_BIN_CONTENTS = ToolSpec(
    name="get_bin_contents",
    description=(
        "What is in one location: every product in it with on hand, allocated and available units "
        "and the time of its last movement. Does not answer where a product is stored."
    ),
    input_model=GetBinContentsInput,
    scope="inventory.read",
    side_effect="read",
    handler=get_bin_contents,
)


# ---------------------------------------------------------------- Slice 2 rule tools


class NoInput(StrictInput):
    """Tools whose rule takes no arguments: every parameter comes from the settings version."""


class GetTopMoversInput(StrictInput):
    days: int = Field(default=30, ge=1, le=365, description="Look back this many calendar days, 1 to 365")
    limit: int = Field(default=10, ge=1, le=50, description="How many products, 1 to 50")


class GetDelayedOrdersInput(StrictInput):
    since_days: int = Field(
        default=0, ge=0, le=30,
        description="Also list orders shipped late since this many days before today; 0 means today",
    )  # fmt: skip


class GetOrderStockoutRisksInput(StrictInput):
    view: Literal["products", "orders", "replenish_before_first_dispatch"] = Field(
        default="products",
        description=(
            "products: which products are short; orders: which open orders are at risk; "
            "replenish_before_first_dispatch: what to replenish before today's next dispatch deadline"
        ),
    )
    hours: int | None = Field(
        default=None, ge=1, le=168,
        description="Orders due within this many hours; omit to use the site setting (24 by default)",
    )  # fmt: skip


def _rule_tool[I: BaseModel](
    name: str,
    description: str,
    input_model: type[I],
    scope: str,
    handler: Callable[[ToolContext, I], ResultEnvelope],
) -> ToolSpec[I, ResultEnvelope]:
    return ToolSpec(name=name, description=description, input_model=input_model, scope=scope,
                    side_effect="read", handler=handler)  # fmt: skip


GET_LOW_STOCK = _rule_tool(
    "get_low_stock",
    "Products running low: available units in their pick location at or below the replenishment "
    "policy minimum. Highlights those pick locations. Does not answer what is short for orders.",
    NoInput,
    "inventory.read",
    lambda ctx, _: rules.low_stock(ctx.conn, ctx.site),
)
GET_TOP_MOVERS = _rule_tool(
    "get_top_movers",
    "The fastest-moving products: most units picked for orders over the last N days. "
    "Highlights where they are stored.",
    GetTopMoversInput, "inventory.read",
    lambda ctx, p: rules.top_movers(ctx.conn, ctx.site, p.days, p.limit),
)  # fmt: skip
GET_DEAD_STOCK = _rule_tool(
    "get_dead_stock",
    "Dead stock: products with stock that have not been picked for more than the site's dead-stock "
    "period (30 days by default), largest stock value first. For 'not moved in N days' questions "
    "with another N, use query_inventory.",
    NoInput,
    "inventory.read",
    lambda ctx, _: rules.dead_stock(ctx.conn, ctx.site),
)
GET_SLOW_IN_PRIME = _rule_tool(
    "get_slow_in_prime",
    "Slow movers sitting in the fast-pick zone: products not picked for more than the site's "
    "period (14 days by default) that still hold fast-pick locations. Highlights those locations.",
    NoInput,
    "inventory.read",
    lambda ctx, _: rules.slow_in_prime(ctx.conn, ctx.site),
)
GET_ZONE_SUMMARY = _rule_tool(
    "get_zone_summary",
    "Every zone with units on hand, stock value, number of products and utilization, most stock "
    "first. Answers which zones hold the most stock.",
    NoInput,
    "inventory.read",
    lambda ctx, _: rules.zone_summary(ctx.conn, ctx.site),
)
GET_CAPACITY_HOTSPOTS = _rule_tool(
    "get_capacity_hotspots",
    "Where the warehouse is close to capacity: zones at or above the capacity threshold (90 percent "
    "by default) or with any location over capacity. Highlights those zones and locations.",
    NoInput,
    "inventory.read",
    lambda ctx, _: rules.capacity_hotspots(ctx.conn, ctx.site),
)
GET_DELAYED_ORDERS = _rule_tool(
    "get_delayed_orders",
    "Delayed outlet orders: open orders past their dispatch deadline, and orders shipped late "
    "today (or since since_days). Not location-based, so no map highlights.",
    GetDelayedOrdersInput, "orders.read",
    lambda ctx, p: rules.delayed_orders(ctx.conn, ctx.site, p.since_days),
)  # fmt: skip
GET_ORDER_STOCKOUT_RISKS = _rule_tool(
    "get_order_stockout_risks",
    "Orders at risk of a stockout: units due on open orders within a window compared with what the "
    "pick locations hold, with whether a replenishment from reserve fixes it. view=orders lists the "
    "affected orders ('which of today's orders are at risk'); view=replenish_before_first_dispatch "
    "lists what to replenish before today's next dispatch deadline.",
    GetOrderStockoutRisksInput, "orders.read",
    lambda ctx, p: rules.order_stockout_risks(ctx.conn, ctx.site, p.view, p.hours),
)  # fmt: skip

SLICE_2_TOOLS = (
    GET_LOW_STOCK, GET_TOP_MOVERS, GET_DEAD_STOCK, GET_SLOW_IN_PRIME, GET_ZONE_SUMMARY,
    GET_CAPACITY_HOTSPOTS, GET_DELAYED_ORDERS, GET_ORDER_STOCKOUT_RISKS,
)  # fmt: skip


def v1_tools() -> ToolRegistry:
    return ToolRegistry([FIND_PRODUCT_LOCATIONS, GET_BIN_CONTENTS, *SLICE_2_TOOLS])
