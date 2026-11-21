"""Site search (slice-3-acceptance D8, step 6): `GET /api/sites/{id}/search?q=`.

A read endpoint, not a copilot tool: finding a record never costs a model call or a copilot run.
Authorized by OPA as a person reading the site (`inventory.read` for products and bins,
`orders.read` for orders), read as `pw_agent_read` through `sem.search`: exact identifiers first,
then prefix matches, then product-name matches; at most 8. Each result carries the fast-path
suggestion its choice runs, which the copilot validates and executes through `tools.executor` like
any other call.
"""

import asyncio
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel

from palletwise.api.copilot import SuggestionOut
from palletwise.api.problems import problem
from palletwise.api.security import CurrentPrincipal
from palletwise.api.sites import Reads, authorize_site
from palletwise.db.connection import query, tenant_session
from palletwise.telemetry.tracing import current_trace_id

router = APIRouter(prefix="/api/sites/{site_id}", tags=["search"])

MAX_QUERY = 64


class SearchResultOut(BaseModel):
    kind: Literal["product", "bin", "order"]
    id: UUID
    code: str
    label: str | None
    match: Literal["exact", "prefix", "name"]
    suggestion: SuggestionOut


class SearchOut(BaseModel):
    query: str
    results: list[SearchResultOut]


def _suggestion(kind: str, code: str) -> SuggestionOut:
    if kind == "product":
        return SuggestionOut(
            label=f"Where is {code} stored?", tool="find_product_locations", arguments={"sku": code}
        )
    if kind == "bin":
        return SuggestionOut(
            label=f"What is in bin {code}?", tool="get_bin_contents", arguments={"bin_code": code}
        )
    return SuggestionOut(
        label=f"What is the status of order {code}?", tool="get_order_status", arguments={"order_no": code}
    )


@router.get(
    "/search",
    response_model=SearchOut,
    responses={403: {"description": "FORBIDDEN"}, 422: {"description": "VALIDATION_FAILED"}},
)
async def search(
    site_id: UUID,
    principal: CurrentPrincipal,
    reads: Reads,
    q: Annotated[str, Query(min_length=1, max_length=MAX_QUERY)],
) -> Any:
    for scope in ("inventory.read", "orders.read"):
        refused = await authorize_site(principal, site_id, "site.search", reads, scope=scope)
        if refused is not None:
            return refused
    text = " ".join(q.split())
    if not text:
        return problem(422, "VALIDATION_FAILED", "Type something to search for.", current_trace_id())

    def load() -> list[tuple[Any, ...]]:
        with tenant_session(reads.read_pool, principal.tenant, read_snapshot=True) as conn:
            return query(
                conn, "search", "SELECT kind, id, code, label, match FROM sem.search(%s, %s)", (site_id, text)
            ).fetchall()

    rows = await asyncio.to_thread(load)
    return SearchOut(
        query=text,
        results=[
            SearchResultOut(
                kind=kind, id=id_, code=code, label=label, match=match, suggestion=_suggestion(kind, code)
            )
            for kind, id_, code, label, match in rows
        ],
    )
