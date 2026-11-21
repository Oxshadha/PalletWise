"""Simulation controls through the API (slice-4-acceptance step 2, D1, D3, D9).

The API authorizes, audits and asks the simulator; the simulator writes the period to its outbox;
the connector ingests it; the clock follows. The simulator here is the real store against the test
database, behind the API's simulator port, so only the internal HTTP hop is left out.
"""

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from support import CLOCK_START, provision_site
from test_copilot_api import ORIGIN, Env
from test_copilot_api import env as env  # the signed-in API fixture
from test_copilot_api import ready_site as ready_site  # its loaded site, one per module

from palletwise.api.simulation import SimulatorRefused
from palletwise.db import ingest_repo, sim_source
from pw_simulator import scenarios, store
from pw_simulator.history import ShiftRefused

pytestmark = pytest.mark.integration

Connect = Callable[[str], psycopg.Connection]


class StoreSimulator:
    """The simulator's own store, called directly (its HTTP API is a thin wrapper over it)."""

    def __init__(self, connect_as: Connect) -> None:
        self._connect = connect_as

    def state(self, run_id: UUID) -> dict[str, Any]:
        return store.run_state(self._connect("sim_owner"), str(run_id))

    def shift(self, run_id: UUID, to: datetime, key: str) -> dict[str, Any]:
        try:
            return store.shift(self._connect("sim_owner"), str(run_id), to, key)
        except store.Busy as exc:
            raise SimulatorRefused(409, "busy") from exc
        except (ShiftRefused, store.RunEnded) as exc:
            raise SimulatorRefused(422, str(exc)) from exc

    def scenarios(self) -> list[dict[str, Any]]:
        return [
            {"id": s.id, "title": s.title, "description": s.description, "tour": list(s.tour)}
            for s in scenarios.available()
        ]

    def run_scenario(self, run_id: UUID, scenario: str, key: str) -> dict[str, Any]:
        try:
            return store.run_scenario(self._connect("sim_owner"), str(run_id), scenario, key)
        except store.Busy as exc:
            raise SimulatorRefused(409, "busy") from exc
        except (scenarios.ScenarioRefused, ShiftRefused, store.RunEnded) as exc:
            raise SimulatorRefused(422, str(exc)) from exc

    def end_run(self, run_id: UUID) -> dict[str, Any]:
        try:
            return store.end_run(self._connect("sim_owner"), str(run_id))
        except store.Busy as exc:
            raise SimulatorRefused(409, "busy") from exc


@pytest.fixture
def sim(env: Env, connect_as: Connect) -> Env:
    env.client.app.state.simulator = StoreSimulator(connect_as)  # type: ignore[attr-defined]
    return env


def run_of(e: Env) -> UUID:
    row = e.db.execute(
        "SELECT id FROM pw.simulation_run WHERE site_id = %s AND status = 'ACTIVE'",
        (e.site.site,),
    ).fetchone()
    assert row is not None
    return row[0]


def ingest(e: Env, connect_as: Connect) -> None:
    connector = e.db.execute(
        "SELECT connector_id FROM pw.sync_state WHERE site_id = %s LIMIT 1", (e.site.site,)
    ).fetchone()
    assert connector is not None
    run = run_of(e)
    source, dest = connect_as("sim_outbox_reader"), connect_as("pw_ingest")
    while batch := sim_source.pull(
        source, run, ingest_repo.read_cursor(dest, e.site.tenant, connector[0], run), 5000
    ):
        ingest_repo.apply_batch(dest, e.site.tenant, e.site.site, connector[0], batch)


def shift(e: Env, body: dict[str, Any], key: str | None = None, site: UUID | None = None) -> Any:
    return e.client.post(
        f"/api/sites/{site or e.site.site}/simulation/shift",
        json=body,
        headers={"Origin": ORIGIN, "Idempotency-Key": key or uuid4().hex},
    )


def state(e: Env) -> dict[str, Any]:
    return e.client.get(f"/api/sites/{e.site.site}/simulation").json()


def audited(e: Env, action: str = "simulation.shift") -> list[tuple[str, str]]:
    return [
        (r[0], r[1])
        for r in e.db.execute(
            "SELECT outcome, user_role FROM pw.audit_event WHERE tenant_id = %s AND action = %s ORDER BY seq",
            (e.site.tenant, action),
        ).fetchall()
    ]


def test_a_manager_moves_time_and_the_clock_follows_the_ingestion(sim: Env, connect_as: Connect) -> None:
    sim.sign_in(subject="manager-time")
    before = state(sim)
    assert before["mode"] == "SIMULATED" and before["shifting"] is False
    assert before["can_control"] is True  # the screens show the time bar
    start = datetime.fromisoformat(before["site_time"])
    moved = shift(sim, {"by": "1h"})
    assert moved.status_code == 200, moved.text
    assert datetime.fromisoformat(moved.json()["to"]) == start + timedelta(hours=1)
    assert state(sim)["shifting"] is True  # emitted, not yet ingested: the site shows its last moment
    ingest(sim, connect_as)
    after = state(sim)
    assert (
        datetime.fromisoformat(after["site_time"]) == start + timedelta(hours=1)
        and after["shifting"] is False
    )
    assert audited(sim)[-1] == ("SHIFTED", "warehouse_manager")


def test_the_same_key_moves_time_once_and_the_admin_may_too(sim: Env, connect_as: Connect) -> None:
    sim.sign_in(subject="admin-time", role="admin")
    first = shift(sim, {"by": "4h"}, key="same-shift-key")
    again = shift(sim, {"by": "4h"}, key="same-shift-key")
    assert first.status_code == again.status_code == 200 and first.json() == again.json()
    ingest(sim, connect_as)
    assert datetime.fromisoformat(state(sim)["site_time"]) == datetime.fromisoformat(first.json()["to"])


def test_next_morning_moves_to_06_00_on_the_next_operating_day(sim: Env, connect_as: Connect) -> None:
    sim.sign_in(subject="manager-morning")
    target = datetime.fromisoformat(state(sim)["next_morning"])
    moved = shift(sim, {"by": "next_morning"})
    assert moved.status_code == 200 and datetime.fromisoformat(moved.json()["to"]) == target
    local = target.astimezone(CLOCK_START.tzinfo)
    assert (local.hour, local.minute) == (6, 0)
    ingest(sim, connect_as)


def test_shifts_outside_the_rules_are_refused_and_audited(sim: Env) -> None:
    sim.sign_in(subject="manager-refused")
    now = datetime.fromisoformat(state(sim)["site_time"])
    backward = shift(sim, {"to": (now - timedelta(hours=1)).isoformat()})
    assert backward.status_code == 422 and backward.json()["code"] == "SHIFT_REFUSED"
    assert shift(sim, {"by": "1h", "to": now.isoformat()}).status_code == 422  # one or the other
    assert shift(sim, {"by": "1y"}).status_code == 422
    no_key = sim.client.post(
        f"/api/sites/{sim.site.site}/simulation/shift", json={"by": "1h"}, headers={"Origin": ORIGIN}
    )
    assert no_key.status_code == 422
    no_origin = sim.client.post(
        f"/api/sites/{sim.site.site}/simulation/shift",
        json={"by": "1h"},
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert no_origin.status_code == 403  # CSRF
    assert audited(sim)[-1][0] == "REFUSED"


def test_a_second_operation_during_a_shift_is_busy(sim: Env, connect_as: Connect) -> None:
    sim.sign_in(subject="manager-busy")
    holder = connect_as("sim_owner")
    holder.execute("BEGIN")
    holder.execute("SELECT 1 FROM sim.run WHERE run_id = %s FOR UPDATE", (str(run_of(sim)),))
    try:
        busy = shift(sim, {"by": "1h"})
        assert busy.status_code == 409 and busy.json()["code"] == "SIMULATION_BUSY"
    finally:
        holder.execute("ROLLBACK")


def test_a_real_site_cannot_move_and_an_outside_site_is_not_found(sim: Env, connect_as: Connect) -> None:
    _, real = provision_site(connect_as, tenant=sim.site.tenant, site_code="REAL1")
    with sim.db.transaction():
        sim.db.execute("SELECT set_config('app.tenant_id', %s, true)", (str(sim.site.tenant),))
        sim.db.execute("UPDATE pw.site_clock SET mode = 'REAL', sim_time = NULL WHERE site_id = %s", (real,))
        sim.db.execute("UPDATE pw.site SET data_status = 'READY' WHERE id = %s", (real,))
    sim.sign_in(subject="manager-real", sites=[sim.site.site, real])
    refused = shift(sim, {"by": "1h"}, site=real)
    assert refused.status_code == 409 and refused.json()["code"] == "NOT_SIMULATED"
    real_state = sim.client.get(f"/api/sites/{real}/simulation").json()
    assert real_state["mode"] == "REAL" and real_state["can_control"] is False
    assert shift(sim, {"by": "1h"}, site=uuid4()).status_code == 404


def test_the_copilot_agent_holds_no_simulation_scope(sim: Env) -> None:
    agent = sim.db.execute(
        "SELECT allowed_scopes FROM pw.agent WHERE tenant_id = %s AND id = 'copilot'", (sim.site.tenant,)
    ).fetchone()
    assert agent is not None and "simulation.control" not in agent[0]


def scenario(e: Env, name: str, key: str | None = None, site: UUID | None = None) -> Any:
    return e.client.post(
        f"/api/sites/{site or e.site.site}/simulation/scenario",
        json={"scenario": name},
        headers={"Origin": ORIGIN, "Idempotency-Key": key or uuid4().hex},
    )


def test_a_manager_stages_a_scenario_once_per_key(sim: Env, connect_as: Connect) -> None:
    sim.sign_in(subject="manager-scenario")
    listed = sim.client.get(f"/api/sites/{sim.site.site}/simulation/scenarios")
    assert listed.status_code == 200
    assert {s["id"] for s in listed.json()} >= {"outlet_window_at_risk", "monsoon_slowdown"}
    start = datetime.fromisoformat(state(sim)["simulator_time"])
    first = scenario(sim, "dead_stock_in_prime", key="same-scenario-key")
    again = scenario(sim, "dead_stock_in_prime", key="same-scenario-key")
    assert first.status_code == again.status_code == 200, first.text
    assert first.json() == again.json()
    assert datetime.fromisoformat(first.json()["to"]) == start + timedelta(minutes=15)
    assert first.json()["tour"]
    ingest(sim, connect_as)
    assert audited(sim, "simulation.scenario")[-1] == ("STAGED", "warehouse_manager")


def test_unknown_scenarios_and_real_sites_are_refused(sim: Env, connect_as: Connect) -> None:
    sim.sign_in(subject="manager-scenario-refused")
    unknown = scenario(sim, "no_such_scenario")
    assert unknown.status_code == 422 and unknown.json()["code"] == "SCENARIO_REFUSED"
    assert scenario(sim, "Bad Name; DROP").status_code == 422  # not a scenario id at all
    assert audited(sim, "simulation.scenario")[-1][0] == "REFUSED"
    _, real = provision_site(connect_as, tenant=sim.site.tenant, site_code="REAL2")
    with sim.db.transaction():
        sim.db.execute("SELECT set_config('app.tenant_id', %s, true)", (str(sim.site.tenant),))
        sim.db.execute("UPDATE pw.site_clock SET mode = 'REAL', sim_time = NULL WHERE site_id = %s", (real,))
        sim.db.execute("UPDATE pw.site SET data_status = 'READY' WHERE id = %s", (real,))
    sim.sign_in(subject="manager-scenario-real", sites=[sim.site.site, real])
    refused = scenario(sim, "festival_rush", site=real)
    assert refused.status_code == 409 and refused.json()["code"] == "NOT_SIMULATED"
