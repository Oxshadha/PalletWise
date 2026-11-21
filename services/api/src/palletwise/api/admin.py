"""Admin panel core (FR-ADM-03, 05, 06, 07): the copilot's status and kill switch, usage and cost,
unanswered questions, and the audit trail with CSV export.

Tenant-wide views. The policy decides on a site, so each request is decided for the first site of
the Admin's session (an Admin of a tenant with no site has nothing to administer here). Scopes:
`admin.agents.manage` for the agent and its switch, `admin.usage.read` for usage and unanswered
questions, `admin.audit.read` for the audit trail. Reads as pw_admin_read; the switch is audited in
its own transaction.
"""

import asyncio
import csv
import io
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, Query, Response
from pydantic import BaseModel

from palletwise import clock
from palletwise.api.problems import problem
from palletwise.api.security import CurrentPrincipal
from palletwise.api.sites import Reads, authorize_site
from palletwise.auth.principal import Principal
from palletwise.db import admin_repo
from palletwise.db.audit_repo import AuditEntry
from palletwise.db.simulation_repo import site_simulation
from palletwise.telemetry.tracing import current_trace_id

router = APIRouter(prefix="/api/admin", tags=["admin"])

AGENT = "copilot"
IDEMPOTENCY_KEY = r"^[A-Za-z0-9_-]{8,128}$"


async def authorize(principal: Principal, action: str, scope: str, reads: Reads) -> Response | None:
    sites = principal.identity.sites
    if not sites:
        return problem(403, "FORBIDDEN", "Your account has no site to administer.", current_trace_id())
    return await authorize_site(
        principal, sites[0], action, reads, scope=scope,
        refusal="You do not have permission for the admin panel.",
    )  # fmt: skip


def _since(days: int) -> datetime:
    # Usage and audit times are wall-clock times (when calls happened), not site time.
    return clock.wall_now() - timedelta(days=days)


class AgentOut(BaseModel):
    id: str
    version: str
    status: Literal["ACTIVE", "SUSPENDED"]
    primary_model: str
    prompt_version: str
    daily_token_budget: int
    used_tokens_today: int


class StatusIn(BaseModel):
    status: Literal["ACTIVE", "SUSPENDED"]


class StatusOut(BaseModel):
    outcome: Literal["changed", "unchanged"]
    status: Literal["ACTIVE", "SUSPENDED"]


@router.get("/agent", response_model=AgentOut)
async def get_agent(principal: CurrentPrincipal, reads: Reads) -> Any:
    if (refused := await authorize(principal, "admin.agent.read", "admin.agents.manage", reads)) is not None:
        return refused
    # The budget counts wall-clock days in the site's time zone (copilot.LedgerMeter).
    site = await asyncio.to_thread(
        site_simulation, reads.app_pool, principal.tenant, principal.identity.sites[0]
    )
    zone = ZoneInfo(site.time_zone) if site else UTC
    day = clock.wall_now().astimezone(zone).date().isoformat()
    found = await asyncio.to_thread(admin_repo.agent, principal.tenant, AGENT, day)
    if found is None:
        return problem(404, "NOT_FOUND", "Not found.", current_trace_id())
    return AgentOut(**found)


@router.put("/agent/status", response_model=StatusOut)
async def set_agent_status(
    body: StatusIn,
    principal: CurrentPrincipal,
    reads: Reads,
    idempotency_key: Annotated[str, Header(pattern=IDEMPOTENCY_KEY)],
) -> Any:
    """The kill switch (FR-ADM-03): suspending stops new copilot requests, fast path included, on
    the next request (the registry is read per request and per tool call)."""
    if (
        refused := await authorize(principal, "admin.agent.status", "admin.agents.manage", reads)
    ) is not None:
        return refused
    identity, trace_id = principal.identity, current_trace_id()

    def entry(change: admin_repo.StatusChange) -> AuditEntry:
        return AuditEntry(
            actor_type="USER", action="agent.status", outcome=change.outcome.upper(), trace_id=trace_id,
            user_subject=identity.subject, user_role=identity.role, scope="admin.agents.manage",
            decision="ALLOW", agent_id=AGENT,
            parameters={"before": change.before, "after": change.after, "idempotency_key": idempotency_key},
        )  # fmt: skip

    change = await asyncio.to_thread(
        admin_repo.set_status, reads.app_pool, principal.tenant, AGENT, body.status, entry
    )
    if change.outcome == "not_found":
        return problem(404, "NOT_FOUND", "Not found.", trace_id)
    return StatusOut(outcome=change.outcome, status=body.status)  # pyright: ignore[reportArgumentType]


class UsageRow(BaseModel):
    day: str
    model: str
    purpose: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_estimated: float
    avg_latency_ms: int
    errors: int


@router.get("/usage", response_model=list[UsageRow])
async def get_usage(
    principal: CurrentPrincipal, reads: Reads, days: Annotated[int, Query(ge=1, le=90)] = 7
) -> Any:
    """Model calls by day, model and purpose. Cost is estimated: provider-reported tokens times the
    configured price table (FR-ADM-06)."""
    if (refused := await authorize(principal, "admin.usage.read", "admin.usage.read", reads)) is not None:
        return refused
    rows = await asyncio.to_thread(admin_repo.usage, principal.tenant, _since(days))
    return [UsageRow(**{**r, "day": r["day"].isoformat()}) for r in rows]


class UnansweredRow(BaseModel):
    question: str
    times: int
    last_asked: datetime
    reasons: list[str]


@router.get("/unanswered", response_model=list[UnansweredRow])
async def get_unanswered(
    principal: CurrentPrincipal, reads: Reads, days: Annotated[int, Query(ge=1, le=90)] = 30
) -> Any:
    """Declined or failed questions with their reasons and how often they were asked (FR-ADM-07)."""
    if (
        refused := await authorize(principal, "admin.unanswered.read", "admin.usage.read", reads)
    ) is not None:
        return refused
    rows = await asyncio.to_thread(
        admin_repo.unanswered, principal.tenant, _since(days), principal.identity.subject
    )
    return [UnansweredRow(**r) for r in rows]


class AuditRow(BaseModel):
    occurred_at: datetime
    actor_type: str
    user_subject: str | None
    user_role: str | None
    agent_id: str | None
    agent_version: str | None
    model: str | None
    prompt_version: str | None
    run_id: str | None
    action: str
    scope: str | None
    tool: str | None
    decision: str | None
    deny_reasons: list[str] | None
    outcome: str
    row_count: int | None
    latency_ms: int | None
    cost_estimated: float | None
    trace_id: str
    parameters: dict[str, Any] | None


def _audit_filter(
    since: datetime | None, until: datetime | None, user: str | None, agent: str | None,
    tool: str | None, action: str | None, decision: str | None,
) -> admin_repo.AuditFilter:  # fmt: skip
    return admin_repo.AuditFilter(since, until, user, agent, tool, action, decision)


@router.get("/audit", response_model=list[AuditRow])
async def get_audit(
    principal: CurrentPrincipal,
    reads: Reads,
    since: datetime | None = None,
    until: datetime | None = None,
    user: Annotated[str | None, Query(max_length=200)] = None,
    agent: Annotated[str | None, Query(max_length=100)] = None,
    tool: Annotated[str | None, Query(max_length=100)] = None,
    action: Annotated[str | None, Query(max_length=100)] = None,
    decision: Literal["ALLOW", "DENY"] | None = None,
) -> Any:
    """The audit trail, newest first, at most 500 entries, with filters (FR-ADM-05)."""
    if (refused := await authorize(principal, "admin.audit.read", "admin.audit.read", reads)) is not None:
        return refused
    f = _audit_filter(since, until, user, agent, tool, action, decision)
    rows = await asyncio.to_thread(admin_repo.audit, principal.tenant, f)
    return [AuditRow.model_validate({**r, "run_id": str(r["run_id"]) if r["run_id"] else None}) for r in rows]


@router.get("/audit.csv", response_class=Response, responses={200: {"content": {"text/csv": {}}}})
async def export_audit(
    principal: CurrentPrincipal,
    reads: Reads,
    since: datetime | None = None,
    until: datetime | None = None,
    user: Annotated[str | None, Query(max_length=200)] = None,
    agent: Annotated[str | None, Query(max_length=100)] = None,
    tool: Annotated[str | None, Query(max_length=100)] = None,
    action: Annotated[str | None, Query(max_length=100)] = None,
    decision: Literal["ALLOW", "DENY"] | None = None,
) -> Any:
    """The same filtered view as CSV (FR-ADM-05: the export matches the filtered view)."""
    if (refused := await authorize(principal, "admin.audit.export", "admin.audit.read", reads)) is not None:
        return refused
    f = _audit_filter(since, until, user, agent, tool, action, decision)
    rows = await asyncio.to_thread(admin_repo.audit, principal.tenant, f)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(admin_repo.AUDIT_COLUMNS)
    for r in rows:
        # A leading =, +, - or @ would be read as a formula by a spreadsheet: prefix a quote.
        writer.writerow([_cell(r[c]) for c in admin_repo.AUDIT_COLUMNS])
    return Response(
        out.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="palletwise-audit.csv"'},
    )


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text
