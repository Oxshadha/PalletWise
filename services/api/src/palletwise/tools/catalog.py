"""The v1 tools (agent spec 4.1). Each is a thin, typed wrapper around the semantic layer:
no business rule lives here (semantic spec 8). Descriptions are what the model reads.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from palletwise.contracts.results import ResultEnvelope
from palletwise.semantic import locations
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


def v1_tools() -> ToolRegistry:
    return ToolRegistry([FIND_PRODUCT_LOCATIONS, GET_BIN_CONTENTS])
