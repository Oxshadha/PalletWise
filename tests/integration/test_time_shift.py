"""Time shift through the real path (Slice 4 step 1): the simulator appends the period to its
outbox, the connector ingests it in order, and PalletWise's clock follows the tick markers.

The same seed and the same run always give the same records, whatever steps time moves in.
"""

from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from support import CLOCK_START, HISTORY_DAYS, load_site

from palletwise.db import ingest_repo, sim_source
from palletwise.db.connection import tenant_transaction
from pw_simulator import store
from pw_simulator.history import RunParams, ShiftRefused, generate

pytestmark = pytest.mark.integration

Connect = Callable[[str], psycopg.Connection]


def ingest(connect_as: Connect, tenant: UUID, site: UUID, connector: UUID, run: UUID) -> int:
    """The connector's loop: apply every pending batch, in order."""
    source, dest = connect_as("sim_outbox_reader"), connect_as("pw_ingest")
    applied = 0
    while batch := sim_source.pull(source, run, ingest_repo.read_cursor(dest, tenant, connector, run), 5000):
        ingest_repo.apply_batch(dest, tenant, site, connector, batch)
        applied += len(batch)
    return applied


def clock(connect_as: Connect, tenant: UUID, site: UUID) -> tuple[object, object]:
    conn = connect_as("pw_app")
    with tenant_transaction(conn, tenant):
        row = conn.execute(
            "SELECT c.sim_time, (SELECT max(occurred_at) FROM pw.movement m WHERE m.site_id = c.site_id)"
            " FROM pw.site_clock c WHERE c.site_id = %s",
            (site,),
        ).fetchone()
    assert row is not None
    return row[0], row[1]


@pytest.fixture
def site(connect_as: Connect) -> tuple[UUID, UUID, UUID, UUID]:
    tenant = uuid4()
    site_id, connector, run = load_site(connect_as, tenant)
    return tenant, site_id, connector, run


@pytest.fixture
def run(connect_as: Connect) -> str:
    """A simulator run alone (its outbox), for the tests that need no PalletWise site."""
    sim = connect_as("sim_owner")
    store.ensure_schema(sim)
    run_id = str(uuid4())
    params = RunParams(run_id, 42, "test", CLOCK_START, history_days=HISTORY_DAYS)
    store.write_run(sim, params, generate(params))
    return run_id


def test_a_shift_moves_the_clock_through_the_normal_ingestion(
    connect_as: Connect, site: tuple[UUID, UUID, UUID, UUID]
) -> None:
    tenant, site_id, connector, run = site
    sim = connect_as("sim_owner")
    target = CLOCK_START + timedelta(days=1)
    result = store.shift(sim, str(run), target, "shift-key-0001")
    assert result["records"] > 0 and datetime.fromisoformat(result["to"]) == target
    assert ingest(connect_as, tenant, site_id, connector, run) == result["records"]
    now, latest_movement = clock(connect_as, tenant, site_id)
    assert now == target and latest_movement is not None and latest_movement <= now  # never leads
    assert datetime.fromisoformat(store.run_state(sim, str(run))["emitted_until"]) == target


def test_the_same_key_shifts_once(connect_as: Connect, run: str) -> None:
    sim = connect_as("sim_owner")
    target = CLOCK_START + timedelta(hours=4)
    first = store.shift(sim, run, target, "shift-key-same")
    rows = sim.execute("SELECT count(*) FROM sim.outbox WHERE run_id = %s", (run,)).fetchone()
    again = store.shift(sim, run, target + timedelta(hours=4), "shift-key-same")  # a retry
    assert again == first
    assert sim.execute("SELECT count(*) FROM sim.outbox WHERE run_id = %s", (run,)).fetchone() == rows


def test_shifting_in_steps_writes_the_same_records_as_one_shift(connect_as: Connect) -> None:
    """Two runs with the same seed: one shifted a day at once, one in three steps."""
    sim = connect_as("sim_owner")
    store.ensure_schema(sim)
    tables: list[list[tuple[object, ...]]] = []
    for steps in ((24,), (2, 10, 12)):
        run = str(uuid4())
        params = RunParams(run, 42, "test", CLOCK_START, history_days=HISTORY_DAYS)
        store.write_run(sim, params, generate(params))
        edge = CLOCK_START
        for i, hours in enumerate(steps):
            edge += timedelta(hours=hours)
            store.shift(sim, run, edge, f"steps-{i}-{run[:8]}")
        tables.append(
            sim.execute(
                "SELECT seq, record_type, sim_time, payload FROM sim.outbox WHERE run_id = %s ORDER BY seq",
                (run,),
            ).fetchall()
        )
    assert tables[0] == tables[1]


def test_a_second_operation_during_a_shift_is_refused_as_busy(connect_as: Connect, run: str) -> None:
    holder = connect_as("sim_owner")
    holder.execute("BEGIN")
    holder.execute("SELECT 1 FROM sim.run WHERE run_id = %s FOR UPDATE", (run,))  # a shift running
    try:
        with pytest.raises(store.Busy):
            store.shift(connect_as("sim_owner"), run, CLOCK_START + timedelta(hours=1), "busy-key-01")
    finally:
        holder.execute("ROLLBACK")


def test_shifts_outside_the_rules_write_nothing(
    connect_as: Connect, site: tuple[UUID, UUID, UUID, UUID]
) -> None:
    _, _, _, run = site
    sim = connect_as("sim_owner")
    before = sim.execute("SELECT count(*) FROM sim.outbox WHERE run_id = %s", (str(run),)).fetchone()
    for bad in (CLOCK_START - timedelta(hours=1), CLOCK_START + timedelta(minutes=5)):
        with pytest.raises(ShiftRefused):
            store.shift(sim, str(run), bad, f"bad-key-{bad.minute:04d}")
    assert sim.execute("SELECT count(*) FROM sim.outbox WHERE run_id = %s", (str(run),)).fetchone() == before
    with pytest.raises(store.UnknownRun):
        store.shift(sim, str(uuid4()), CLOCK_START + timedelta(hours=1), "unknown-run-key")
