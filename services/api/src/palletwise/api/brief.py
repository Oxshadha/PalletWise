"""Daily brief endpoint (slice-3-acceptance step 5, FR-BRF-01 to 03).

`GET /api/sites/{id}/brief` returns the site's brief for the site-local day: generated on the
first request of the day and stored, then returned unchanged (a later request makes no model
call). One brief per site and day, read by every Manager and Admin of the site (product owner
revision of 2.2, 2026-11-21). Refused with DATA_NOT_READY while the site is not READY.

Authorized as a person reading the site: `inventory.read` and `orders.read` (the brief shows
stock and orders). The model words the items only when the copilot is active and its daily
budget allows (D7); otherwise every item has its template sentence.
"""

import asyncio
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from pydantic import BaseModel

from palletwise import clock
from palletwise.agent.brief import BRIEF_PROMPT_VERSION, word_brief
from palletwise.agent.runtime import LITE_MODEL
from palletwise.api.copilot import CopilotServices, LedgerMeter, SuggestionOut
from palletwise.api.problems import problem
from palletwise.api.security import CurrentPrincipal
from palletwise.api.sites import Reads, authorize_site
from palletwise.contracts.results import Highlight, ResultEnvelope
from palletwise.db.audit_repo import AuditEntry
from palletwise.db.brief_repo import BriefStore, NewBrief, StoredBrief
from palletwise.db.connection import role_pool, tenant_session
from palletwise.db.copilot_repo import Owner
from palletwise.semantic import brief as facts
from palletwise.semantic.locations import SiteNotReady
from palletwise.telemetry.tracing import current_trace_id

router = APIRouter(prefix="/api/sites/{site_id}", tags=["brief"])

WAIT_SECONDS = 15.0  # how long a request waits for another's generation before answering 503
POLL_SECONDS = 0.25

TITLES = {
    "top_exceptions": "Top exceptions",
    "orders_at_risk_today": "Orders at risk today",
    "throughput": "Yesterday's throughput",
    "data_freshness": "Data freshness",
}


class BriefLinkOut(BaseModel):
    to: Literal["inbox", "copilot"]
    suggestion: SuggestionOut | None = None


class BriefItemOut(BaseModel):
    kind: facts.ItemKind
    title: str
    text: str
    text_source: Literal["model", "template"]
    why: str
    link: BriefLinkOut | None
    highlights: list[Highlight]
    result: ResultEnvelope


class BriefOut(BaseModel):
    business_date: date
    as_of: datetime
    time_zone: str
    generated_at: datetime
    wording_source: Literal["model", "template", "mixed"]
    items: list[BriefItemOut]


def brief_store(request: Request) -> BriefStore:
    store: BriefStore | None = getattr(request.app.state, "brief_store", None)
    if store is None:
        store = request.app.state.brief_store = BriefStore(role_pool("PW_BRIEF"))
    return store


def _out(stored: StoredBrief) -> BriefOut:
    items = [BriefItemOut.model_validate(i) for i in stored.items]
    zone = items[0].result.evidence.time_zone if items else "UTC"
    return BriefOut(
        business_date=stored.business_date,
        as_of=stored.as_of,
        time_zone=zone,
        generated_at=stored.generated_at,
        wording_source=stored.wording_source,  # pyright: ignore[reportArgumentType]
        items=items,
    )


@dataclass(frozen=True, slots=True)
class _Day:
    business_date: date
    run: UUID | None


def _today(reads: Any, tenant: UUID, site: UUID) -> _Day | None:
    """The site-local business date and simulation run while the site is READY; None otherwise."""
    with tenant_session(reads.read_pool, tenant, read_snapshot=True) as conn:
        row = conn.execute(
            "SELECT n.data_status, sem.business_date(n.site_id, n.as_of), e.simulation_run_id"
            " FROM sem.site_now n JOIN sem.exception_state e ON e.site_id = n.site_id"
            " WHERE n.site_id = %s",
            (site,),
        ).fetchone()
    if row is None or row[0] != "READY" or row[1] is None:
        return None
    return _Day(row[1], row[2])


def _source(sources: list[str]) -> str:
    return sources[0] if len(set(sources)) == 1 else "mixed"


@router.get(
    "/brief",
    response_model=BriefOut,
    responses={
        403: {"description": "FORBIDDEN"},
        503: {"description": "DATA_NOT_READY or BRIEF_PREPARING"},
    },
)
async def daily_brief(site_id: UUID, request: Request, principal: CurrentPrincipal, reads: Reads) -> Any:
    for scope in ("inventory.read", "orders.read"):
        refused = await authorize_site(principal, site_id, "brief.read", reads, scope=scope)
        if refused is not None:
            return refused
    trace_id = current_trace_id()
    tenant = principal.tenant
    store = brief_store(request)
    waited_until = clock.monotonic() + WAIT_SECONDS

    # Read the day's brief, or claim its generation; while another request holds a valid lease,
    # wait for its brief (no transaction is open meanwhile).
    while True:
        today = await asyncio.to_thread(_today, reads, tenant, site_id)
        if today is None:
            return problem(503, "DATA_NOT_READY", "The warehouse data is still loading.", trace_id)
        found = await asyncio.to_thread(store.find, tenant, site_id, today.business_date, today.run)
        if found is not None:
            return _out(found)
        token = await asyncio.to_thread(store.claim, tenant, site_id, today.business_date, today.run)
        if token is None:
            if clock.monotonic() >= waited_until:
                return problem(
                    503, "BRIEF_PREPARING", "Today's brief is being prepared. Try again shortly.",
                    trace_id, headers={"Retry-After": "2"},
                )  # fmt: skip
            await asyncio.sleep(POLL_SECONDS)
            continue
        try:
            stored = await _generate(request, principal, reads, site_id, token, today)
        except _DayChanged:
            continue
        except BaseException:
            # Failed or cancelled: the next request generates at once instead of waiting out the lease.
            await asyncio.shield(asyncio.to_thread(store.release, tenant, site_id, token))
            raise
        if stored is None:
            return problem(503, "DATA_NOT_READY", "The warehouse data is still loading.", trace_id)
        return _out(stored)


class _DayChanged(Exception):
    """The claimed day or run is no longer the site's current one."""


async def _generate(
    request: Request, principal: Any, reads: Any, site_id: UUID, token: UUID, today: _Day
) -> StoredBrief | None:
    """Reads the facts, words them and stores the brief. None when the site stopped being READY."""
    tenant = principal.tenant
    store = brief_store(request)

    def read() -> facts.BriefFacts:
        with tenant_session(reads.read_pool, tenant, read_snapshot=True) as conn:
            return facts.brief_facts(conn, site_id)

    try:
        day = await asyncio.to_thread(read)
    except SiteNotReady:
        await asyncio.to_thread(store.release, tenant, site_id, token)
        return None
    if (day.business_date, day.simulation_run_id) != (today.business_date, today.run):
        # The site day turned over (or a reset started a new run) between the claim and the read:
        # these facts belong to another brief. Give the claim up and start again.
        await asyncio.to_thread(store.release, tenant, site_id, token)
        raise _DayChanged

    copilot: CopilotServices = request.app.state.copilot
    agent = await asyncio.to_thread(copilot.agents.load, tenant, "copilot")
    lite = copilot.runtime.provider.model(LITE_MODEL) if agent is not None and agent.active else None
    meter = None
    if agent is not None and lite is not None:
        budget_day = clock.wall_now().astimezone(ZoneInfo(day.time_zone)).date()
        owner = Owner(tenant, principal.identity.subject, site_id)
        meter = LedgerMeter(copilot.ledger, owner, None, agent, budget_day, brief_id=token)
    worded = await word_brief(day.items, lite, meter)

    items = [
        {
            "kind": item.kind,
            "title": TITLES[item.kind],
            "text": w.text,
            "text_source": w.source,
            "why": item.why,
            "link": item.link,
            "highlights": [h.model_dump(mode="json") for h in item.result.highlights],
            "result": item.result.model_dump(mode="json"),
        }
        for item, w in zip(day.items, worded, strict=True)
    ]
    stored = await asyncio.to_thread(
        store.store,
        tenant,
        site_id,
        token,
        NewBrief(
            business_date=day.business_date,
            simulation_run_id=day.simulation_run_id,
            items=items,
            wording_source=_source([w.source for w in worded]),
            data_watermark=day.data_watermark,
            as_of=day.as_of,
            settings_version_id=day.settings_version_id,
            prompt_version=BRIEF_PROMPT_VERSION,
        ),
    )
    identity = principal.identity
    await asyncio.to_thread(
        reads.audit.append,
        tenant,
        AuditEntry(
            actor_type="USER",
            action="brief.generate",
            outcome=stored.wording_source.upper(),
            trace_id=current_trace_id(),
            site_id=site_id,
            user_subject=identity.subject,
            user_role=identity.role,
            agent_id=agent.id if meter is not None and agent is not None else None,
            prompt_version=BRIEF_PROMPT_VERSION,
            parameters={"business_date": stored.business_date.isoformat(), "brief_id": str(stored.id)},
        ),
    )
    return stored
