"""Exceptions inbox endpoints (agent spec 12, slice-3-acceptance step 3).

`GET /api/sites/{id}/exceptions`: open and acknowledged exceptions, ranked by score, with the
components that explain the rank (FR-EXC-02). Any role that may read the site sees it.

`POST /api/sites/{id}/exceptions/{exception_id}/acknowledge`: Managers only (scope
`exception.acknowledge`), through `pw.acknowledge_exception()` as pw_app, recorded with user and
site time and audited in the same transaction (FR-EXC-04). The operation is idempotent by state:
a repeat or a concurrent request makes no second transition (the row is locked). The
`Idempotency-Key` is an audit correlation identifier, not a replay store: a retry is answered from
the current state (agent spec 16, 2026-11-21).
"""

import asyncio
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Header
from pydantic import BaseModel

from palletwise.api.problems import problem
from palletwise.api.security import CurrentPrincipal
from palletwise.api.sites import Reads, authorize_site
from palletwise.contracts.results import ExceptionType, Highlight, Severity
from palletwise.db import exceptions_repo
from palletwise.db.audit_repo import AuditEntry
from palletwise.db.connection import tenant_session
from palletwise.semantic import exceptions, locations
from palletwise.telemetry.tracing import current_trace_id

router = APIRouter(prefix="/api/sites/{site_id}/exceptions", tags=["exceptions"])

ACKNOWLEDGE_SCOPE = "exception.acknowledge"
IDEMPOTENCY_KEY = r"^[A-Za-z0-9_-]{8,128}$"


class ScoreComponentsOut(BaseModel):
    """The raw inputs, their 0 to 1 normalization, caps and weights (semantic 6.1)."""

    lines: int
    hours_until_due: float | None
    value_at_risk: float
    lines_normalized: float
    urgency_normalized: float
    value_normalized: float
    lines_cap: float
    hours_cap: float
    value_cap: float
    weight_lines: float
    weight_urgency: float
    weight_value: float


class AffectedOrderOut(BaseModel):
    order_id: UUID | None
    order_no: str


class ExceptionOut(BaseModel):
    id: UUID
    exception_type: ExceptionType
    status: Literal["OPEN", "ACKNOWLEDGED"]
    subject_key: str
    subject_label: str
    product_id: UUID | None
    zone_id: UUID | None
    order_id: UUID | None
    severity: Severity
    score: float
    components: ScoreComponentsOut
    settings_version_id: UUID
    first_detected_at: datetime
    last_evaluated_at: datetime
    acknowledged_by: str | None
    acknowledged_at: datetime | None
    highlights: list[Highlight]
    orders: list[AffectedOrderOut]


class ExceptionsOut(BaseModel):
    as_of: datetime
    time_zone: str
    exceptions: list[ExceptionOut]


class AcknowledgementOut(BaseModel):
    id: UUID
    status: Literal["ACKNOWLEDGED"]
    acknowledged_by: str
    acknowledged_at: datetime


@router.get(
    "",
    response_model=ExceptionsOut,
    responses={403: {"description": "FORBIDDEN"}, 503: {"description": "DATA_NOT_READY"}},
)
async def list_exceptions(site_id: UUID, principal: CurrentPrincipal, reads: Reads) -> Any:
    if (refused := await authorize_site(principal, site_id, "exceptions.list", reads)) is not None:
        return refused

    def load() -> exceptions.Inbox:
        with tenant_session(reads.read_pool, principal.tenant, read_snapshot=True) as conn:
            return exceptions.inbox(conn, site_id)

    try:
        inbox = await asyncio.to_thread(load)
    except locations.SiteNotReady:
        return problem(503, "DATA_NOT_READY", "The warehouse data is still loading.", current_trace_id())
    return ExceptionsOut(
        as_of=inbox.as_of,
        time_zone=inbox.time_zone,
        exceptions=[
            ExceptionOut(
                id=x.exception_id,
                exception_type=x.exception_type,  # pyright: ignore[reportArgumentType]
                status=x.status,  # pyright: ignore[reportArgumentType]
                subject_key=x.subject_key,
                subject_label=x.subject_label,
                product_id=x.product_id,
                zone_id=x.zone_id,
                order_id=x.order_id,
                severity=x.severity,
                score=x.score,
                components=ScoreComponentsOut.model_validate(x.components),
                settings_version_id=x.settings_version_id,
                first_detected_at=x.first_detected_at,
                last_evaluated_at=x.last_evaluated_at,
                acknowledged_by=x.acknowledged_by,
                acknowledged_at=x.acknowledged_at,
                highlights=list(x.highlights),
                orders=[AffectedOrderOut(order_id=o.order_id, order_no=o.order_no) for o in x.orders],
            )
            for x in inbox.exceptions
        ],
    )


@router.post(
    "/{exception_id}/acknowledge",
    response_model=AcknowledgementOut,
    responses={
        403: {"description": "FORBIDDEN"},
        404: {"description": "NOT_FOUND"},
        409: {"description": "EXCEPTION_RESOLVED"},
        422: {"description": "VALIDATION_FAILED"},
    },
)
async def acknowledge_exception(
    site_id: UUID,
    exception_id: UUID,
    principal: CurrentPrincipal,
    reads: Reads,
    idempotency_key: Annotated[str, Header(pattern=IDEMPOTENCY_KEY)],
) -> Any:
    refused = await authorize_site(
        principal,
        site_id,
        ACKNOWLEDGE_SCOPE,
        reads,
        scope=ACKNOWLEDGE_SCOPE,
        refusal="You do not have permission to acknowledge exceptions.",
    )
    if refused is not None:
        return refused
    identity = principal.identity
    trace_id = current_trace_id()

    def audit(outcome: exceptions_repo.Outcome) -> AuditEntry:
        return AuditEntry(
            actor_type="USER",
            action=ACKNOWLEDGE_SCOPE,
            outcome=outcome.upper(),
            trace_id=trace_id,
            site_id=site_id,
            user_subject=identity.subject,
            user_role=identity.role,
            scope=ACKNOWLEDGE_SCOPE,
            parameters={"exception_id": str(exception_id), "idempotency_key": idempotency_key},
            decision="ALLOW",
        )

    found = await asyncio.to_thread(
        exceptions_repo.acknowledge,
        reads.app_pool,
        identity.tenant,
        site_id,
        exception_id,
        identity.subject,
        audit,
    )
    if found.outcome == "not_found":
        return problem(404, "NOT_FOUND", "Not found.", trace_id)
    if found.outcome == "resolved":
        return problem(409, "EXCEPTION_RESOLVED", "This exception has already been resolved.", trace_id)
    assert found.acknowledged_by is not None and found.acknowledged_at is not None
    return AcknowledgementOut(
        id=exception_id,
        status="ACKNOWLEDGED",
        acknowledged_by=found.acknowledged_by,
        acknowledged_at=found.acknowledged_at,
    )
