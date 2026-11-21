"""Simulation reset on both sides (slice-4-acceptance step 4, simulator spec 7.1, CWM 7.4).

The API asks the simulator to end the old run, then resets PalletWise's side and audits it in one
transaction. The new run is then loaded as the simulation admin service loads any run: generated
from the seed, ingested through the connector, checked against the dataset hash, then READY.
Its own module, so the reset never disturbs the site other modules share.
"""

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from support import CLOCK_START, HISTORY_DAYS, provision_site
from test_copilot_api import ORIGIN, Env
from test_copilot_api import env as env  # the signed-in API fixture
from test_copilot_api import ready_site as ready_site  # this module's own loaded site
from test_simulation_api import StoreSimulator, audited, ingest, run_of, shift, state

from palletwise.db import dataset_hash, ingest_repo, sim_source, site_repo
from pw_simulator import store
from pw_simulator.history import RunParams, generate

pytestmark = pytest.mark.integration

Connect = Callable[[str], psycopg.Connection]


@pytest.fixture
def sim(env: Env, connect_as: Connect) -> Env:
    env.client.app.state.simulator = StoreSimulator(connect_as)  # type: ignore[attr-defined]
    return env


def reset(e: Env, key: str | None = None, site: UUID | None = None) -> Any:
    return e.client.post(
        f"/api/sites/{site or e.site.site}/simulation/reset",
        headers={"Origin": ORIGIN, "Idempotency-Key": key or uuid4().hex},
    )


def count(e: Env, sql: str) -> int:
    with e.db.transaction():
        e.db.execute("SELECT set_config('app.tenant_id', %s, true)", (str(e.site.tenant),))
        row = e.db.execute(sql, (e.site.site,)).fetchone()
    assert row is not None
    return int(row[0])


def rebuild(e: Env, connect_as: Connect, run: UUID) -> str:
    """What the simulation admin service does for a BUILDING run (its steps 2 to 5)."""
    params = RunParams(str(run), 42, "test", CLOCK_START, history_days=HISTORY_DAYS)
    store.write_run(connect_as("sim_owner"), params, generate(params))
    connector = e.db.execute(
        "SELECT connector_id FROM pw.sync_state WHERE site_id = %s LIMIT 1", (e.site.site,)
    ).fetchone()
    assert connector is not None
    source, dest = connect_as("sim_outbox_reader"), connect_as("pw_ingest")
    while batch := sim_source.pull(
        source, run, ingest_repo.read_cursor(dest, e.site.tenant, connector[0], run), 5000
    ):
        ingest_repo.apply_batch(dest, e.site.tenant, e.site.site, connector[0], batch)
    admin = connect_as("pw_sim_admin")
    digest, _ = dataset_hash.compute(admin, e.site.tenant, e.site.site)
    site_repo.complete_load(admin, e.site.tenant, e.site.site, run, digest)
    return digest


def test_a_manager_resets_and_the_site_rebuilds_to_the_same_data(sim: Env, connect_as: Connect) -> None:
    sim.sign_in(subject="manager-reset")
    admin = connect_as("pw_sim_admin")
    before, _ = dataset_hash.compute(admin, sim.site.tenant, sim.site.site)
    old = run_of(sim)
    # Some history after the load: a shift, its ingestion and the exceptions it opens.
    assert shift(sim, {"by": "4h"}).status_code == 200
    ingest(sim, connect_as)
    with sim.db.transaction():
        sim.db.execute("SELECT set_config('app.tenant_id', %s, true)", (str(sim.site.tenant),))
        sim.db.execute("SELECT * FROM pw.evaluate_site_exceptions(%s)", (sim.site.site,))
    assert count(sim, "SELECT count(*) FROM pw.exception_record WHERE site_id = %s AND status <> 'RESOLVED'")

    done = reset(sim, key="reset-once-key")
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["outcome"] == "reset" and body["data_status"] == "REBUILDING"
    new = UUID(body["new_run"])

    # PalletWise's side, in one transaction (CWM 7.4 step 1).
    now = state(sim)
    assert now["data_status"] == "REBUILDING"
    assert count(sim, "SELECT count(*) FROM pw.movement WHERE site_id = %s") == 0
    assert count(sim, "SELECT count(*) FROM pw.outbound_order WHERE site_id = %s") == 0
    assert (
        count(sim, "SELECT count(*) FROM pw.exception_record WHERE site_id = %s AND status <> 'RESOLVED'")
        == 0
    )
    assert count(
        sim,
        "SELECT count(*) FROM pw.exception_record"
        " WHERE site_id = %s AND resolution_reason = 'SIMULATION_RESET'",
    )
    assert count(sim, "SELECT count(*) FROM pw.bin WHERE site_id = %s")  # master data stays
    assert audited(sim, "simulation.reset")[-1] == ("RESET", "warehouse_manager")
    # The simulator ended the old run: it takes no more shifts.
    with pytest.raises(store.RunEnded):
        store.shift(connect_as("sim_owner"), str(old), CLOCK_START + timedelta(days=1), "old-run-shift")
    # A shift waits for the rebuild.
    assert shift(sim, {"by": "1h"}).status_code == 503

    # The same key resets once.
    again = reset(sim, key="reset-once-key")
    assert again.status_code == 200 and again.json()["outcome"] == "repeated"
    assert UUID(again.json()["new_run"]) == new
    assert count(sim, "SELECT count(*) FROM pw.simulation_run WHERE site_id = %s AND status = 'ENDED'") == 1

    # The new run loads like the first one, and its data is the seed's data again.
    assert rebuild(sim, connect_as, new) == before
    after = state(sim)
    assert after["data_status"] == "READY"
    assert datetime.fromisoformat(after["site_time"]) == CLOCK_START  # back at the clock start
    assert run_of(sim) == new


def test_a_reset_during_a_shift_is_busy_and_changes_nothing(sim: Env, connect_as: Connect) -> None:
    sim.sign_in(subject="manager-reset-busy")
    holder = connect_as("sim_owner")
    holder.execute("BEGIN")
    holder.execute("SELECT 1 FROM sim.run WHERE run_id = %s FOR UPDATE", (str(run_of(sim)),))
    try:
        busy = reset(sim)
        assert busy.status_code == 409 and busy.json()["code"] == "SIMULATION_BUSY"
    finally:
        holder.execute("ROLLBACK")
    assert state(sim)["data_status"] == "READY"
    assert audited(sim, "simulation.reset")[-1][0] == "BUSY"


def test_a_real_site_cannot_be_reset_and_a_key_is_required(sim: Env, connect_as: Connect) -> None:
    _, real = provision_site(connect_as, tenant=sim.site.tenant, site_code="REAL3")
    with sim.db.transaction():
        sim.db.execute("SELECT set_config('app.tenant_id', %s, true)", (str(sim.site.tenant),))
        sim.db.execute("UPDATE pw.site_clock SET mode = 'REAL', sim_time = NULL WHERE site_id = %s", (real,))
        sim.db.execute("UPDATE pw.site SET data_status = 'READY' WHERE id = %s", (real,))
    sim.sign_in(subject="admin-reset-real", role="admin", sites=[sim.site.site, real])
    refused = reset(sim, site=real)
    assert refused.status_code == 409 and refused.json()["code"] == "NOT_SIMULATED"
    no_key = sim.client.post(f"/api/sites/{sim.site.site}/simulation/reset", headers={"Origin": ORIGIN})
    assert no_key.status_code == 422
    no_origin = sim.client.post(
        f"/api/sites/{sim.site.site}/simulation/reset", headers={"Idempotency-Key": uuid4().hex}
    )
    assert no_origin.status_code == 403


def test_a_failed_rebuild_stays_failed_skips_old_records_and_can_reset_again(
    sim: Env, connect_as: Connect
) -> None:
    sim.sign_in(subject="manager-reset-failed")
    old = run_of(sim)
    first = reset(sim)
    assert first.status_code == 200, first.text
    new = UUID(first.json()["new_run"])
    # A late batch of the old run (a connector that read it before the reset) changes nothing.
    connector = sim.db.execute(
        "SELECT connector_id FROM pw.sync_state WHERE site_id = %s LIMIT 1", (sim.site.site,)
    ).fetchone()
    assert connector is not None
    late = sim_source.pull(connect_as("sim_outbox_reader"), old, 0, 50)
    skipped = ingest_repo.apply_batch(
        connect_as("pw_ingest"), sim.site.tenant, sim.site.site, connector[0], late
    )
    assert skipped == len(late)
    assert count(sim, "SELECT count(*) FROM pw.movement WHERE site_id = %s") == 0
    # The rebuild fails (what the simulation admin service records): FAILED, never READY.
    site_repo.fail_load(connect_as("pw_sim_admin"), sim.site.tenant, sim.site.site, new, "generation failed")
    assert state(sim)["data_status"] == "FAILED"
    assert shift(sim, {"by": "1h"}).status_code == 503
    # From FAILED a reset starts again (no active run for the simulator to end).
    again = reset(sim)
    assert again.status_code == 200 and again.json()["outcome"] == "reset"
    assert state(sim)["data_status"] == "REBUILDING"


def ready(e: Env, connect_as: Connect) -> None:
    """The module's site, READY again: an earlier test may have left a reset rebuilding."""
    building = e.db.execute(
        "SELECT id FROM pw.simulation_run WHERE site_id = %s AND status = 'BUILDING'", (e.site.site,)
    ).fetchone()
    if building is not None:
        rebuild(e, connect_as, building[0])


def ended_at(connect_as: Connect, run: UUID) -> object:
    row = (
        connect_as("sim_owner")
        .execute("SELECT ended_at FROM sim.run WHERE run_id = %s", (str(run),))
        .fetchone()
    )
    assert row is not None
    return row[0]


def test_a_retried_reset_after_the_rebuild_leaves_the_new_run_alone(sim: Env, connect_as: Connect) -> None:
    """Review finding: a retry with the same key once the new run is ACTIVE must not end it."""
    sim.sign_in(subject="manager-reset-retry")
    ready(sim, connect_as)
    first = reset(sim, key="reset-retry-key")
    assert first.status_code == 200, first.text
    new = UUID(first.json()["new_run"])
    rebuild(sim, connect_as, new)
    again = reset(sim, key="reset-retry-key")
    assert again.status_code == 200 and again.json()["outcome"] == "repeated"
    assert ended_at(connect_as, new) is None  # the simulator still takes shifts on it
    assert shift(sim, {"by": "1h"}).status_code == 200
    ingest(sim, connect_as)
    assert audited(sim, "simulation.reset")[-1][0] == "REPEATED"


def test_a_reset_refused_by_palletwise_resumes_the_simulator_run(sim: Env, connect_as: Connect) -> None:
    """Review finding: if PalletWise's side is busy after the simulator ended the run, the run is
    resumed, so the site keeps working instead of refusing every shift."""
    sim.sign_in(subject="manager-reset-resume")
    ready(sim, connect_as)
    run = run_of(sim)
    holder = connect_as("postgres")  # holds the clock row, as a long ingestion batch would
    holder.execute("BEGIN")
    holder.execute("SELECT 1 FROM pw.site_clock WHERE site_id = %s FOR UPDATE", (sim.site.site,))
    try:
        busy = reset(sim)
    finally:
        holder.execute("ROLLBACK")
    assert busy.status_code == 409 and busy.json()["code"] == "SIMULATION_BUSY"
    assert ended_at(connect_as, run) is None and state(sim)["data_status"] == "READY"
    assert shift(sim, {"by": "1h"}).status_code == 200
    ingest(sim, connect_as)


def test_a_run_the_simulator_does_not_know_does_not_block_a_reset(sim: Env, connect_as: Connect) -> None:
    """Review finding: reset is how a lost simulator database is recovered."""
    sim.sign_in(subject="manager-reset-unknown")
    ready(sim, connect_as)

    class LostRun(StoreSimulator):
        def end_run(self, run_id: UUID) -> dict[str, Any]:
            from palletwise.api.simulation import SimulatorRefused

            raise SimulatorRefused(404, "Unknown run.")

    sim.client.app.state.simulator = LostRun(connect_as)  # type: ignore[attr-defined]
    done = reset(sim)
    assert done.status_code == 200 and done.json()["outcome"] == "reset"


def test_replays_after_a_reset_return_their_result_and_keys_keep_their_kind(connect_as: Connect) -> None:
    sim = connect_as("sim_owner")
    store.ensure_schema(sim)
    run = str(uuid4())
    params = RunParams(run, 42, "test", CLOCK_START, history_days=HISTORY_DAYS)
    store.write_run(sim, params, generate(params))
    first = store.shift(sim, run, CLOCK_START + timedelta(hours=1), "replay-key-1")
    with pytest.raises(store.KeyReused):
        store.run_scenario(sim, run, "festival_rush", "replay-key-1")
    store.end_run(sim, run)
    assert store.shift(sim, run, CLOCK_START + timedelta(hours=1), "replay-key-1") == first
    with pytest.raises(store.RunEnded):
        store.shift(sim, run, CLOCK_START + timedelta(hours=2), "replay-key-2")
