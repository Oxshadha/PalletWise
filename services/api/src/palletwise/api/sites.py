"""Map endpoints (agent spec 12, UX spec 3 to 5): layout, map state and bin details.

Read-only and site-scoped. Every request is authorized by OPA for `inventory.read` on the site
(a person acting directly, no agent). Denials are audited; allowed passive reads are not,
because the map re-reads its state every few seconds and would flood the audit chain.
"""

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path, Request, Response
from pydantic import BaseModel

from palletwise.api.problems import problem
from palletwise.api.security import CurrentPrincipal
from palletwise.auth.principal import Principal
from palletwise.contracts.results import ResultEnvelope, ResultStatus
from palletwise.db import layout_repo
from palletwise.db.audit_repo import AuditEntry, AuditLog
from palletwise.db.connection import Pool, tenant_session
from palletwise.policy import Decision, PolicyClient, PolicyRequest, PolicyUser
from palletwise.semantic import locations, map_state
from palletwise.telemetry.tracing import current_trace_id, record_policy_decision, tracer
from palletwise.tools.catalog import BIN_CODE_PATTERN

router = APIRouter(prefix="/api/sites/{site_id}", tags=["map"])

BIN_CODE = BIN_CODE_PATTERN  # one definition, shared with get_bin_contents


@dataclass
class ReadServices:
    policy: PolicyClient
    audit: AuditLog
    app_pool: Pool
    read_pool: Pool


def read_services(request: Request) -> ReadServices:
    found: ReadServices = request.app.state.reads
    return found


Reads = Annotated[ReadServices, Depends(read_services)]
Point = tuple[float, float]


class SiteGeometry(BaseModel):
    id: UUID
    code: str
    name: str
    width_m: float
    depth_m: float


class ZoneOut(BaseModel):
    id: UUID
    code: str
    name: str
    zone_type: str
    is_prime: bool
    outline: list[Point]


class RackOut(BaseModel):
    id: UUID
    code: str
    zone_id: UUID
    origin_x: float
    origin_y: float
    rotation_deg: float
    bay_count: int
    level_count: int
    bay_width_m: float
    depth_m: float
    level_height_m: float


class BinOut(BaseModel):
    id: UUID
    code: str
    zone_id: UUID
    rack_id: UUID | None
    bin_role: str
    bay: int | None
    level: int | None
    x: float
    y: float
    z: float
    width_m: float
    depth_m: float
    height_m: float


class FeatureOut(BaseModel):
    id: UUID
    feature_type: str
    label: str
    outline: list[Point]


class LayoutOut(BaseModel):
    site: SiteGeometry
    zones: list[ZoneOut]
    racks: list[RackOut]
    bins: list[BinOut]
    features: list[FeatureOut]


class BinStateOut(BaseModel):
    bin_id: UUID
    state: Literal["NORMAL", "EMPTY", "LOW_STOCK", "OVER_CAPACITY"]
    on_hand: int


class MapStateOut(BaseModel):
    as_of: datetime
    time_zone: str
    data_as_of: datetime | None
    bins: list[BinStateOut]


async def policy_decision(
    principal: Principal, site_id: UUID, action: str, reads: ReadServices, scope: str
) -> Decision:
    """The policy's answer for this user, site and scope, traced; nothing is audited here."""
    identity = principal.identity
    request = PolicyRequest(
        user=PolicyUser(
            identity.subject, identity.role, str(identity.tenant), tuple(str(s) for s in identity.sites)
        ),
        action=action,
        scope=scope,
        resource_tenant=str(identity.tenant),
        resource_site=str(site_id),
    )
    with tracer.start_as_current_span("policy.decision") as span:
        decision = await asyncio.to_thread(reads.policy.decide, request)
        record_policy_decision(span, action, scope, decision.allow, decision.reasons)
    return decision


async def authorize_site(
    principal: Principal,
    site_id: UUID,
    action: str,
    reads: ReadServices,
    scope: str = "inventory.read",
    refusal: str = "You do not have permission to view this site.",
) -> Response | None:
    """None when allowed; otherwise the 404 or 403 response (site outside the session is 404)."""
    trace_id = current_trace_id()
    if not principal.may_see_site(site_id):
        return problem(404, "NOT_FOUND", "Not found.", trace_id)
    identity = principal.identity
    decision = await policy_decision(principal, site_id, action, reads, scope)
    if decision.allow:
        return None
    entry = AuditEntry(
        actor_type="USER",
        action=action,
        outcome="DENIED",
        trace_id=trace_id,
        site_id=site_id,
        user_subject=identity.subject,
        user_role=identity.role,
        scope=scope,
        decision="DENY",
        deny_reasons=decision.reasons,
    )
    await asyncio.to_thread(reads.audit.append, identity.tenant, entry)
    return problem(403, "FORBIDDEN", refusal, trace_id)


@router.get("/layout", response_model=LayoutOut, responses={304: {"description": "Not modified"}})
async def layout(
    site_id: UUID,
    principal: CurrentPrincipal,
    reads: Reads,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Any:
    if (refused := await authorize_site(principal, site_id, "map.layout", reads)) is not None:
        return refused

    def load() -> layout_repo.Layout | None:
        with reads.app_pool.connection() as conn:
            return layout_repo.site_layout(conn, principal.tenant, site_id)

    found = await asyncio.to_thread(load)
    if found is None:
        return problem(404, "NOT_FOUND", "No layout is available for this site.", current_trace_id())
    body = LayoutOut.model_validate(
        {
            "site": found.site,
            "zones": found.zones,
            "racks": found.racks,
            "bins": found.bins,
            "features": found.features,
        }
    )
    payload = body.model_dump_json()
    etag = '"' + hashlib.sha256(payload.encode()).hexdigest()[:32] + '"'
    headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
    if if_none_match == etag:
        return Response(status_code=304, headers=headers)
    return Response(payload, media_type="application/json", headers=headers)


@router.get("/map-state", response_model=MapStateOut)
async def current_map_state(site_id: UUID, principal: CurrentPrincipal, reads: Reads) -> Any:
    if (refused := await authorize_site(principal, site_id, "map.state", reads)) is not None:
        return refused

    def load() -> map_state.MapState:
        with tenant_session(reads.read_pool, principal.tenant, read_snapshot=True) as conn:
            return map_state.map_state(conn, site_id)

    try:
        state = await asyncio.to_thread(load)
    except locations.SiteNotReady:
        return problem(503, "DATA_NOT_READY", "The warehouse data is still loading.", current_trace_id())
    return MapStateOut(
        as_of=state.as_of,
        time_zone=state.time_zone,
        data_as_of=state.data_as_of,
        bins=[BinStateOut(bin_id=b.bin_id, state=b.state, on_hand=b.on_hand) for b in state.bins],  # pyright: ignore[reportArgumentType]
    )


@router.get("/bins/{code}", response_model=ResultEnvelope)
async def bin_details(
    site_id: UUID,
    code: Annotated[str, Path(pattern=BIN_CODE)],
    principal: CurrentPrincipal,
    reads: Reads,
) -> Any:
    """The same semantic definition the copilot's get_bin_contents uses (FR-MAP-06)."""
    if (refused := await authorize_site(principal, site_id, "map.bin_details", reads)) is not None:
        return refused

    def load() -> ResultEnvelope:
        with tenant_session(reads.read_pool, principal.tenant, read_snapshot=True) as conn:
            return locations.bin_contents(conn, site_id, code)

    try:
        result = await asyncio.to_thread(load)
    except locations.SiteNotReady:
        return problem(503, "DATA_NOT_READY", "The warehouse data is still loading.", current_trace_id())
    if result.status is ResultStatus.NOT_FOUND:  # a missing resource is 404 (agent spec 12.1)
        return problem(404, "NOT_FOUND", result.message or "Not found.", current_trace_id())
    return json.loads(result.model_dump_json())
