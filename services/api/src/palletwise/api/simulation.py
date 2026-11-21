"""Simulation controls (slice-4-acceptance step 2): move a simulated site's time on demand.

`GET /api/sites/{id}/simulation`: the site's clock mode and time, the simulator's time, and
whether a shift is still being ingested; for every reader of the site.

`POST /api/sites/{id}/simulation/shift`: by +1 hour, +4 hours, +1 day, +1 week, to the next
operating morning, or to a time. `GET .../simulation/scenarios` lists the scenarios and
`POST .../simulation/scenario` stages one (step 3): its events go to the simulator's input log and
time moves one tick. `POST .../simulation/reset` discards the run and rebuilds the site from the
seed (simulator spec 7.1). All three writes: scope `simulation.control` (Manager and Admin, D3),
refused unless the site's clock mode is `SIMULATED`; `Idempotency-Key` required; audited with
before and after; the CSRF check applies as to every state change. PalletWise never moves its own clock: the
simulator emits the period through the outbox and the clock follows the ingested tick markers (D1).
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

import httpx
from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, Field, model_validator

from palletwise.api.problems import problem
from palletwise.api.security import CurrentPrincipal
from palletwise.api.sites import Reads, authorize_site, policy_decision
from palletwise.db.audit_repo import AuditEntry
from palletwise.db.ids import uuid7
from palletwise.db.simulation_repo import Reset, reset_by_key, reset_site, site_simulation
from palletwise.settings import SimulationSettings
from palletwise.telemetry.tracing import current_trace_id

log = logging.getLogger("palletwise.api.simulation")

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

    def scenarios(self) -> list[dict[str, Any]]: ...

    def run_scenario(self, run_id: UUID, scenario: str, key: str) -> dict[str, Any]: ...

    def end_run(self, run_id: UUID) -> dict[str, Any]: ...

    def resume_run(self, run_id: UUID) -> dict[str, Any]: ...


@dataclass
class HttpSimulator:
    """The simulator's internal control API (simulator spec 7), on the stack's private network.
    One connection pool for the process; the state read the screens poll fails fast."""

    base_url: str
    timeout: float = 120.0  # a week's period is generated and written in one request
    state_timeout: float = 5.0
    _client: httpx.Client = field(default_factory=httpx.Client, repr=False)

    def _call(
        self, method: str, path: str, body: dict[str, Any] | None = None, timeout: float | None = None
    ) -> Any:
        try:
            response = self._client.request(
                method, self.base_url + path, json=body, timeout=timeout or self.timeout
            )
        except httpx.HTTPError as exc:
            raise SimulatorUnavailable(str(type(exc).__name__)) from exc
        if response.status_code >= 400:
            detail = (
                response.json().get("detail")
                if response.headers.get("content-type", "").startswith("application/json")
                else None
            )
            raise SimulatorRefused(response.status_code, str(detail or "The simulator refused."))
        return response.json()

    def state(self, run_id: UUID) -> dict[str, Any]:
        return dict(self._call("GET", f"/runs/{run_id}", timeout=self.state_timeout))

    def shift(self, run_id: UUID, to: datetime, key: str) -> dict[str, Any]:
        return dict(
            self._call("POST", f"/runs/{run_id}/shift", {"to": to.isoformat(), "idempotency_key": key})
        )

    def scenarios(self) -> list[dict[str, Any]]:
        return list(self._call("GET", "/scenarios"))

    def run_scenario(self, run_id: UUID, scenario: str, key: str) -> dict[str, Any]:
        return dict(
            self._call("POST", f"/runs/{run_id}/scenario", {"scenario": scenario, "idempotency_key": key})
        )

    def end_run(self, run_id: UUID) -> dict[str, Any]:
        return dict(self._call("POST", f"/runs/{run_id}/end"))

    def resume_run(self, run_id: UUID) -> dict[str, Any]:
        return dict(self._call("POST", f"/runs/{run_id}/resume"))


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
    # The viewer holds simulation.control here: the screens show the time bar, the demo and reset.
    can_control: bool


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


class ResetOut(BaseModel):
    outcome: Literal["reset", "repeated"]
    new_run: UUID
    clock_start: datetime
    data_status: str  # REBUILDING until the new run is loaded and checked


class ScenarioInfo(BaseModel):
    id: str
    title: str
    description: str
    tour: list[str]  # questions to ask the copilot afterwards


class ScenarioIn(BaseModel):
    scenario: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")


class ScenarioOut(BaseModel):
    scenario: str
    title: str
    events: int
    from_time: datetime
    to: datetime
    records: int
    tour: list[str]


# ---------------------------------------------------------------- endpoints


@router.get("", response_model=SimulationOut, responses={404: {"description": "NOT_FOUND"}})
async def simulation_state(site_id: UUID, request: Request, principal: CurrentPrincipal, reads: Reads) -> Any:
    if (refused := await authorize_site(principal, site_id, "simulation.state", reads)) is not None:
        return refused
    found = await asyncio.to_thread(site_simulation, reads.app_pool, principal.tenant, site_id)
    if found is None:
        return problem(404, "NOT_FOUND", "Not found.", current_trace_id())
    simulator_time = None
    can_control = (
        found.mode == "SIMULATED"
        and (await policy_decision(principal, site_id, "simulation.state", reads, CONTROL_SCOPE)).allow
    )
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
        can_control=can_control,
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


@router.get(
    "/scenarios",
    response_model=list[ScenarioInfo],
    responses={403: {"description": "FORBIDDEN"}, 503: {"description": "SIMULATOR_UNAVAILABLE"}},
)
async def list_scenarios(site_id: UUID, request: Request, principal: CurrentPrincipal, reads: Reads) -> Any:
    refused = await authorize_site(
        principal, site_id, "simulation.scenarios", reads, scope=CONTROL_SCOPE,
        refusal="You do not have permission to run scenarios on this site.",
    )  # fmt: skip
    if refused is not None:
        return refused
    try:
        found = await asyncio.to_thread(simulator(request).scenarios)
    except (SimulatorUnavailable, SimulatorRefused):
        return problem(
            503, "SIMULATOR_UNAVAILABLE", "The simulator is not reachable right now.", current_trace_id()
        )
    return [ScenarioInfo.model_validate(s, from_attributes=False) for s in found]


@router.post(
    "/scenario",
    response_model=ScenarioOut,
    responses={
        403: {"description": "FORBIDDEN"},
        409: {"description": "NOT_SIMULATED or SIMULATION_BUSY"},
        422: {"description": "SCENARIO_REFUSED or VALIDATION_FAILED"},
        503: {"description": "DATA_NOT_READY or SIMULATOR_UNAVAILABLE"},
    },
)
async def run_scenario(
    site_id: UUID,
    body: ScenarioIn,
    request: Request,
    principal: CurrentPrincipal,
    reads: Reads,
    idempotency_key: Annotated[str, Header(pattern=IDEMPOTENCY_KEY)],
) -> Any:
    refused = await authorize_site(
        principal, site_id, "simulation.scenario", reads, scope=CONTROL_SCOPE,
        refusal="You do not have permission to run scenarios on this site.",
    )  # fmt: skip
    if refused is not None:
        return refused
    trace_id = current_trace_id()
    found = await asyncio.to_thread(site_simulation, reads.app_pool, principal.tenant, site_id)
    if found is None:
        return problem(404, "NOT_FOUND", "Not found.", trace_id)
    if found.mode != "SIMULATED":
        return problem(
            409, "NOT_SIMULATED", "This site's data comes from a real WMS; it runs no scenarios.", trace_id
        )
    if found.data_status != "READY" or found.run_id is None:
        return problem(503, "DATA_NOT_READY", "The warehouse data is still loading.", trace_id)
    identity = principal.identity

    async def audit(outcome: str, result: dict[str, Any] | None) -> None:
        entry = AuditEntry(
            actor_type="USER", action="simulation.scenario", outcome=outcome, trace_id=trace_id,
            site_id=site_id, user_subject=identity.subject, user_role=identity.role, scope=CONTROL_SCOPE,
            decision="ALLOW",
            parameters={
                "scenario": body.scenario, "idempotency_key": idempotency_key,
                "from_time": result["from"] if result else None, "to_time": result["to"] if result else None,
            },
        )  # fmt: skip
        await asyncio.to_thread(reads.audit.append, identity.tenant, entry)

    try:
        result = await asyncio.to_thread(
            simulator(request).run_scenario, found.run_id, body.scenario, idempotency_key
        )
    except SimulatorUnavailable:
        return problem(503, "SIMULATOR_UNAVAILABLE", "The simulator is not reachable right now.", trace_id)
    except SimulatorRefused as exc:
        if exc.status == 409:
            await audit("BUSY", None)
            return problem(
                409, "SIMULATION_BUSY", "Another time shift or reset is running. Try again shortly.", trace_id
            )
        await audit("REFUSED", None)
        return problem(422, "SCENARIO_REFUSED", exc.message, trace_id)
    await audit("STAGED", result)
    return ScenarioOut(
        scenario=str(result["scenario"]), title=str(result["title"]), events=int(result["events"]),
        from_time=datetime.fromisoformat(result["from"]), to=datetime.fromisoformat(result["to"]),
        records=int(result["records"]), tour=[str(t) for t in result["tour"]],
    )  # fmt: skip


RESET_REFUSALS = {
    "not_found": (404, "NOT_FOUND", "Not found."),
    "not_simulated": (409, "NOT_SIMULATED", "This site's data comes from a real WMS; it cannot be reset."),
    "loading": (409, "SIMULATION_BUSY", "The site is still loading. Try again when it is ready."),
    "busy": (409, "SIMULATION_BUSY", "Another time shift or reset is running. Try again shortly."),
}


@router.post(
    "/reset",
    response_model=ResetOut,
    responses={
        403: {"description": "FORBIDDEN"},
        409: {"description": "NOT_SIMULATED or SIMULATION_BUSY"},
        503: {"description": "SIMULATOR_UNAVAILABLE"},
    },
)
async def reset_simulation(
    site_id: UUID,
    request: Request,
    principal: CurrentPrincipal,
    reads: Reads,
    idempotency_key: Annotated[str, Header(pattern=IDEMPOTENCY_KEY)],
) -> Any:
    """Discards the site's simulation run and rebuilds it from the seed (simulator spec 7.1).

    A key that already reset the site is answered from that reset, without touching the simulator.
    A site still loading is refused first. The simulator then ends the old run under the run's lock,
    so a reset during a shift or scenario is refused as busy before anything changes (D9); a run it
    does not know is nothing to end (reset is how a lost simulator database is recovered). One
    transaction resets PalletWise's side and audits it; if that is refused or fails, the simulator
    run is resumed, so the two sides never disagree. The simulation admin service then loads the new
    run as at the first load."""
    refused = await authorize_site(
        principal, site_id, "simulation.reset", reads, scope=CONTROL_SCOPE,
        refusal="You do not have permission to reset this site's simulation.",
    )  # fmt: skip
    if refused is not None:
        return refused
    trace_id = current_trace_id()
    found = await asyncio.to_thread(site_simulation, reads.app_pool, principal.tenant, site_id)
    if found is None:
        return problem(404, "NOT_FOUND", "Not found.", trace_id)
    if found.mode != "SIMULATED":
        status, code, message = RESET_REFUSALS["not_simulated"]
        return problem(status, code, message, trace_id)
    identity = principal.identity

    def entry(outcome: str, result: Reset | None) -> AuditEntry:
        return AuditEntry(
            actor_type="USER", action="simulation.reset", outcome=outcome, trace_id=trace_id, site_id=site_id,
            user_subject=identity.subject, user_role=identity.role, scope=CONTROL_SCOPE, decision="ALLOW",
            parameters={
                "idempotency_key": idempotency_key,
                "from_time": found.site_time.isoformat() if found.site_time else None,
                "old_run": str(result.old_run) if result and result.old_run else None,
                "new_run": str(result.new_run) if result and result.new_run else None,
            },
        )  # fmt: skip

    async def refuse(outcome: str) -> Any:
        await asyncio.to_thread(reads.audit.append, identity.tenant, entry(outcome.upper(), None))
        status, code, message = RESET_REFUSALS[outcome]
        return problem(status, code, message, trace_id)

    repeated = await asyncio.to_thread(
        reset_by_key, reads.app_pool, principal.tenant, site_id, idempotency_key
    )
    if repeated is not None:
        result = repeated
        await asyncio.to_thread(reads.audit.append, identity.tenant, entry("REPEATED", repeated))
    else:
        if found.data_status not in ("READY", "FAILED"):
            return await refuse("loading")
        sim = simulator(request)
        ended = found.run_id
        if ended is not None:  # an ACTIVE run: the simulator ends it first
            try:
                await asyncio.to_thread(sim.end_run, ended)
            except SimulatorUnavailable:
                return problem(
                    503, "SIMULATOR_UNAVAILABLE", "The simulator is not reachable right now.", trace_id
                )
            except SimulatorRefused as exc:
                if exc.status == 409:
                    return await refuse("busy")
                if exc.status != 404:
                    return problem(503, "SIMULATOR_UNAVAILABLE", "The simulator refused the reset.", trace_id)
                ended = None  # the simulator does not know the run: nothing to end

        async def resume() -> None:
            if ended is None:
                return
            try:
                await asyncio.to_thread(sim.resume_run, ended)
            except (SimulatorUnavailable, SimulatorRefused):
                log.exception("the simulator run %s could not be resumed after a refused reset", ended)

        try:
            result = await asyncio.to_thread(
                reset_site, reads.app_pool, principal.tenant, site_id, uuid7(), identity.subject,
                idempotency_key, lambda r: entry(r.outcome.upper(), r),
            )  # fmt: skip
        except Exception:
            await resume()
            raise
        if result.outcome not in ("reset", "repeated"):
            await resume()
        if result.outcome in RESET_REFUSALS:
            status, code, message = RESET_REFUSALS[result.outcome]
            return problem(status, code, message, trace_id)
    assert result.new_run is not None and result.clock_start is not None
    return ResetOut(
        outcome=result.outcome,  # pyright: ignore[reportArgumentType]
        new_run=result.new_run, clock_start=result.clock_start, data_status="REBUILDING",
    )  # fmt: skip
