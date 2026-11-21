"""Simulation controls (slice-4-acceptance step 2): move a simulated site's time on demand.

`GET /api/sites/{id}/simulation`: the site's clock mode and time, the simulator's time, and
whether a shift is still being ingested; for every reader of the site.

`POST /api/sites/{id}/simulation/shift`: by +1 hour, +4 hours, +1 day, +1 week, to the next
operating morning, or to a time. Scope `simulation.control` (Manager and Admin, D3), refused unless
the site's clock mode is `SIMULATED`; `Idempotency-Key` required; audited with before and after;
the CSRF check applies as to every state change. PalletWise never moves its own clock: the
simulator emits the period through the outbox and the clock follows the ingested tick markers (D1).
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

import httpx
from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, model_validator

from palletwise.api.problems import problem
from palletwise.api.security import CurrentPrincipal
from palletwise.api.sites import Reads, authorize_site
from palletwise.db.audit_repo import AuditEntry
from palletwise.db.simulation_repo import site_simulation
from palletwise.settings import SimulationSettings
from palletwise.telemetry.tracing import current_trace_id

router = APIRouter(prefix="/api/sites/{site_id}/simulation", tags=["simulation"])

CONTROL_SCOPE = "simulation.control"
IDEMPOTENCY_KEY = r"^[A-Za-z0-9_-]{8,128}$"
STEPS = {
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
    "1w": timedelta(weeks=1),
}


# ---------------------------------------------------------------- the simulator, as a port


class SimulatorRefused(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class SimulatorUnavailable(Exception):
    pass


class Simulator(Protocol):
    def state(self, run_id: UUID) -> dict[str, Any]: ...

    def shift(self, run_id: UUID, to: datetime, key: str) -> dict[str, Any]: ...


@dataclass
class HttpSimulator:
    """The simulator's internal control API (simulator spec 7), on the stack's private network."""

    base_url: str
    timeout: float = 120.0  # a week's period is generated and written in one request

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = httpx.request(method, self.base_url + path, json=body, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise SimulatorUnavailable(str(type(exc).__name__)) from exc
        if response.status_code >= 400:
            detail = (
                response.json().get("detail")
                if response.headers.get("content-type", "").startswith("application/json")
                else None
            )
            raise SimulatorRefused(response.status_code, str(detail or "The simulator refused."))
        return dict(response.json())

    def state(self, run_id: UUID) -> dict[str, Any]:
        return self._call("GET", f"/runs/{run_id}")

    def shift(self, run_id: UUID, to: datetime, key: str) -> dict[str, Any]:
        return self._call("POST", f"/runs/{run_id}/shift", {"to": to.isoformat(), "idempotency_key": key})


def simulator(request: Request) -> Simulator:
    found: Simulator | None = getattr(request.app.state, "simulator", None)
    if found is None:
        found = request.app.state.simulator = HttpSimulator(SimulationSettings().control_url)
    return found


# ---------------------------------------------------------------- shapes


class SimulationOut(BaseModel):
    mode: Literal["SIMULATED", "REAL"]
    data_status: str
    site_time: datetime | None
    time_zone: str
    simulator_time: datetime | None  # how far the simulator has emitted; None if unknown
    shifting: bool  # the simulator is ahead of the site: a shift is still being ingested
    next_morning: datetime | None


class ShiftIn(BaseModel):
    by: Literal["1h", "4h", "1d", "1w", "next_morning"] | None = None
    to: datetime | None = None

    @model_validator(mode="after")
    def one(self) -> "ShiftIn":
        if (self.by is None) == (self.to is None):
            raise ValueError("give by or to")
        if self.to is not None and self.to.tzinfo is None:
            raise ValueError("to needs a time zone")
        return self


class ShiftOut(BaseModel):
    from_time: datetime
    to: datetime
    records: int


# ---------------------------------------------------------------- endpoints


@router.get("", response_model=SimulationOut, responses={404: {"description": "NOT_FOUND"}})
async def simulation_state(site_id: UUID, request: Request, principal: CurrentPrincipal, reads: Reads) -> Any:
    if (refused := await authorize_site(principal, site_id, "simulation.state", reads)) is not None:
        return refused
    found = await asyncio.to_thread(site_simulation, reads.app_pool, principal.tenant, site_id)
    if found is None:
        return problem(404, "NOT_FOUND", "Not found.", current_trace_id())
    simulator_time = None
    if found.mode == "SIMULATED" and found.run_id is not None:
        try:
            state = await asyncio.to_thread(simulator(request).state, found.run_id)
            simulator_time = datetime.fromisoformat(state["emitted_until"])
        except (SimulatorRefused, SimulatorUnavailable):
            simulator_time = None
    return SimulationOut(
        mode=found.mode,  # pyright: ignore[reportArgumentType]
        data_status=found.data_status,
        site_time=found.site_time,
        time_zone=found.time_zone,
        simulator_time=simulator_time,
        shifting=bool(simulator_time and found.site_time and simulator_time > found.site_time),
        next_morning=found.next_morning,
    )


@router.post(
    "/shift",
    response_model=ShiftOut,
    responses={
        403: {"description": "FORBIDDEN"},
        409: {"description": "NOT_SIMULATED or SIMULATION_BUSY"},
        422: {"description": "SHIFT_REFUSED or VALIDATION_FAILED"},
        503: {"description": "DATA_NOT_READY or SIMULATOR_UNAVAILABLE"},
    },
)
async def shift_time(
    site_id: UUID,
    body: ShiftIn,
    request: Request,
    principal: CurrentPrincipal,
    reads: Reads,
    idempotency_key: Annotated[str, Header(pattern=IDEMPOTENCY_KEY)],
) -> Any:
    refused = await authorize_site(
        principal, site_id, "simulation.shift", reads, scope=CONTROL_SCOPE,
        refusal="You do not have permission to move this site's time.",
    )  # fmt: skip
    if refused is not None:
        return refused
    trace_id = current_trace_id()
    found = await asyncio.to_thread(site_simulation, reads.app_pool, principal.tenant, site_id)
    if found is None:
        return problem(404, "NOT_FOUND", "Not found.", trace_id)
    if found.mode != "SIMULATED":
        return problem(
            409, "NOT_SIMULATED", "This site's data comes from a real WMS; its time cannot move.", trace_id
        )
    if found.data_status != "READY" or found.run_id is None:
        return problem(503, "DATA_NOT_READY", "The warehouse data is still loading.", trace_id)
    sim = simulator(request)
    identity = principal.identity

    async def audit(outcome: str, before: datetime | None, after: datetime | None) -> None:
        entry = AuditEntry(
            actor_type="USER", action="simulation.shift", outcome=outcome, trace_id=trace_id, site_id=site_id,
            user_subject=identity.subject, user_role=identity.role, scope=CONTROL_SCOPE, decision="ALLOW",
            parameters={
                "by": body.by, "to": body.to.isoformat() if body.to else None,
                "from_time": before.isoformat() if before else None,
                "to_time": after.isoformat() if after else None, "idempotency_key": idempotency_key,
            },
        )  # fmt: skip
        await asyncio.to_thread(reads.audit.append, identity.tenant, entry)

    try:
        state = await asyncio.to_thread(sim.state, found.run_id)
        before = datetime.fromisoformat(state["emitted_until"])
        if body.by == "next_morning":
            target = found.next_morning
            if target is None or target <= before:
                await audit("REFUSED", before, None)
                return problem(
                    422, "SHIFT_REFUSED", "There is no later operating morning to move to.", trace_id
                )
        elif body.by is not None:
            target = before + STEPS[body.by]
        else:
            assert body.to is not None
            target = body.to
        result = await asyncio.to_thread(sim.shift, found.run_id, target, idempotency_key)
    except SimulatorUnavailable:
        return problem(503, "SIMULATOR_UNAVAILABLE", "The simulator is not reachable right now.", trace_id)
    except SimulatorRefused as exc:
        if exc.status == 409:
            await audit("BUSY", None, None)
            return problem(
                409, "SIMULATION_BUSY", "Another time shift or reset is running. Try again shortly.", trace_id
            )
        await audit("REFUSED", None, None)
        return problem(422, "SHIFT_REFUSED", exc.message, trace_id)
    out = ShiftOut(
        from_time=datetime.fromisoformat(result["from"]),
        to=datetime.fromisoformat(result["to"]),
        records=int(result["records"]),
    )
    await audit("SHIFTED", out.from_time, out.to)
    return out
