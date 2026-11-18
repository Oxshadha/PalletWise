"""End-to-end Slice 0 path: simulator outbox, connector ingestion, invariants and dataset hash.

Runs two tenants from the same seed through the real write path and checks that the dataset
hash is identical, that replays are idempotent and that the ledger equals the balances.
"""

from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest

from palletwise.db import dataset_hash, ingest_repo, sim_source, site_repo, status_repo
from palletwise.db.connection import tenant_transaction
from pw_simulator import store
from pw_simulator.history import RunParams, generate

pytestmark = pytest.mark.integration

Connect = Callable[[str], psycopg.Connection]
CLOCK_START = datetime(2026, 4, 1, 6, 0, tzinfo=ZoneInfo("Asia/Colombo"))
HISTORY_DAYS = 14


def load_site(connect_as: Connect, tenant: UUID, seed: int = 42) -> tuple[UUID, UUID, UUID]:
    site, connector, run = uuid4(), uuid4(), uuid4()
    admin = connect_as("pw_sim_admin")
    site_repo.bootstrap_site(
        admin,
        site_repo.SiteBootstrap(
            tenant_id=tenant,
            tenant_code=f"t-{tenant.hex[:8]}",
            tenant_name="Test",
            site_id=site,
            site_code="PLG",
            site_name="Test site",
            region_code="LK",
            time_zone="Asia/Colombo",
            # Floats, exactly as the simulation admin service passes them.
            width_m=80.0,
            depth_m=50.0,
            connector_id=connector,
            run_id=run,
            seed=seed,
            dataset_version="test",
            history_start=CLOCK_START - timedelta(days=HISTORY_DAYS),
            clock_start=CLOCK_START,
            started_by="test",
        ),
    )
    params = RunParams(str(run), seed, "test", CLOCK_START, history_days=HISTORY_DAYS)
    sim = connect_as("sim_owner")
    store.ensure_schema(sim)
    store.write_run(sim, params, generate(params))

    source, dest = connect_as("sim_outbox_reader"), connect_as("pw_ingest")
    while True:
        cursor = ingest_repo.read_cursor(dest, tenant, connector)
        batch = sim_source.pull(source, run, cursor, 5000)
        if not batch:
            break
        ingest_repo.apply_batch(dest, tenant, site, connector, batch)
    return site, connector, run


@pytest.fixture
def loaded(connect_as: Connect) -> tuple[UUID, UUID, UUID, UUID]:
    tenant = uuid4()
    site, connector, run = load_site(connect_as, tenant)
    return tenant, site, connector, run


def test_load_completes_and_site_becomes_ready(connect_as: Connect, loaded: tuple[UUID, ...]) -> None:
    tenant, site, _, run = loaded
    admin = connect_as("pw_sim_admin")
    state = site_repo.latest_run(admin, tenant, site)
    assert state and state.history_loaded and state.status == "BUILDING"
    digest, counts = dataset_hash.compute(admin, tenant, site)
    assert counts["bin"] == 676 and counts["product"] == 300 and counts["movement"] > 0
    site_repo.complete_load(admin, tenant, site, run, digest)
    status = status_repo.site_status(admin, tenant, site)
    assert status is not None and status.data_status == "READY" and status.data_status_detail is None


def test_clock_equals_last_tick_and_never_leads(connect_as: Connect, loaded: tuple[UUID, ...]) -> None:
    tenant, site, _, _ = loaded
    conn = connect_as("pw_app")
    with tenant_transaction(conn, tenant):
        sim_time, latest = conn.execute(
            "SELECT c.sim_time, (SELECT max(occurred_at) FROM pw.movement m WHERE m.site_id = c.site_id) "
            "FROM pw.site_clock c WHERE c.site_id = %s",
            (site,),
        ).fetchone() or (None, None)
    assert sim_time == CLOCK_START
    assert latest is not None and latest <= sim_time


def test_ledger_equals_balances(connect_as: Connect, loaded: tuple[UUID, ...]) -> None:
    tenant, site, _, _ = loaded
    conn = connect_as("pw_app")
    with tenant_transaction(conn, tenant):
        mismatches = conn.execute(
            """
            WITH flows AS (
              SELECT to_bin_id AS bin_id, product_id, qty FROM pw.movement
               WHERE site_id = %(s)s AND to_bin_id IS NOT NULL
              UNION ALL
              SELECT from_bin_id, product_id, -qty FROM pw.movement
               WHERE site_id = %(s)s AND from_bin_id IS NOT NULL),
            ledger AS (SELECT bin_id, product_id, sum(qty) AS on_hand FROM flows GROUP BY 1, 2)
            SELECT count(*) FROM ledger l
              FULL JOIN (SELECT * FROM pw.inventory_balance WHERE site_id = %(s)s) b
                ON b.bin_id = l.bin_id AND b.product_id = l.product_id
             WHERE coalesce(l.on_hand, 0) <> coalesce(b.on_hand, 0)
            """,
            {"s": site},
        ).fetchone()
        allocation_mismatch = conn.execute(
            """
            SELECT count(*) FROM pw.inventory_balance b
             WHERE b.site_id = %(s)s AND b.allocated <> coalesce((
               SELECT sum(a.qty - a.qty_consumed - a.qty_released) FROM pw.allocation a
                WHERE a.bin_id = b.bin_id AND a.product_id = b.product_id), 0)
            """,
            {"s": site},
        ).fetchone()
    assert mismatches == (0,)
    assert allocation_mismatch == (0,)


def test_same_seed_gives_same_hash_and_replay_is_idempotent(
    connect_as: Connect, loaded: tuple[UUID, ...]
) -> None:
    tenant_a, site_a, connector_a, run_a = loaded
    tenant_b = uuid4()
    site_b, _, _ = load_site(connect_as, tenant_b)
    admin = connect_as("pw_sim_admin")
    hash_a, _ = dataset_hash.compute(admin, tenant_a, site_a)
    hash_b, _ = dataset_hash.compute(admin, tenant_b, site_b)
    assert hash_a == hash_b

    replay = sim_source.pull(connect_as("sim_outbox_reader"), run_a, 0, 50_000)
    ingest_repo.apply_batch(connect_as("pw_ingest"), tenant_a, site_a, connector_a, replay)
    assert dataset_hash.compute(admin, tenant_a, site_a)[0] == hash_a


def test_tenants_cannot_see_each_other(connect_as: Connect, loaded: tuple[UUID, ...]) -> None:
    tenant_a, site_a, _, _ = loaded
    conn = connect_as("pw_app")
    with tenant_transaction(conn, uuid4()):
        assert conn.execute("SELECT count(*) FROM pw.movement WHERE site_id = %s", (site_a,)).fetchone() == (
            0,
        )
        assert conn.execute("SELECT count(*) FROM pw.product").fetchone() == (0,)
    with tenant_transaction(conn, tenant_a):
        assert (conn.execute("SELECT count(*) FROM pw.product").fetchone() or (0,))[0] == 300


def test_negative_stock_is_rejected(connect_as: Connect, loaded: tuple[UUID, ...]) -> None:
    tenant, site, connector, run = loaded
    bad = ingest_repo.SourceRecord(
        sequence=10_000_000,
        run_id=run,
        record_type="MOVEMENT",
        occurred_at=CLOCK_START,
        dataset_version="test",
        is_synthetic=True,
        payload={
            "external_id": "MV-BAD-1",
            "movement_type": "TRANSFER",
            "sku": "SKU-102",
            "qty": 1_000_000,
            "from_bin": "C-F01",
            "to_bin": "C-F02",
            "order_line": None,
            "reason_code": None,
        },
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        ingest_repo.apply_batch(connect_as("pw_ingest"), tenant, site, connector, [bad])


def test_records_of_an_ended_run_are_skipped(connect_as: Connect, loaded: tuple[UUID, ...]) -> None:
    tenant, site, connector, _ = loaded
    stale = ingest_repo.SourceRecord(
        sequence=10_000_001,
        run_id=uuid4(),
        record_type="CATEGORY",
        occurred_at=CLOCK_START,
        dataset_version="test",
        is_synthetic=True,
        payload={"code": "STALE", "name": "Stale"},
    )
    skipped = ingest_repo.apply_batch(connect_as("pw_ingest"), tenant, site, connector, [stale])
    assert skipped == 1


def test_new_run_after_a_failed_run_loads_from_the_start(connect_as: Connect) -> None:
    """Review finding 1: the cursor restarts with each run, so a retry run is not skipped."""
    tenant = uuid4()
    site, connector, first_run = load_site(connect_as, tenant)
    admin = connect_as("pw_sim_admin")
    site_repo.fail_load(admin, tenant, site, first_run, "simulated failure")

    second_run = uuid4()
    site_repo.bootstrap_site(
        admin,
        site_repo.SiteBootstrap(
            tenant_id=tenant,
            tenant_code=f"t-{tenant.hex[:8]}",
            tenant_name="Test",
            site_id=site,
            site_code="PLG",
            site_name="Test site",
            region_code="LK",
            time_zone="Asia/Colombo",
            width_m=80.0,
            depth_m=50.0,
            connector_id=connector,
            run_id=second_run,
            seed=42,
            dataset_version="test",
            history_start=CLOCK_START - timedelta(days=HISTORY_DAYS),
            clock_start=CLOCK_START,
            started_by="test",
        ),
    )
    status = status_repo.site_status(admin, tenant, site)
    assert status is not None and status.data_status == "LOADING"
    dest = connect_as("pw_ingest")
    assert ingest_repo.read_cursor(dest, tenant, connector) == 0

    params = RunParams(str(second_run), 42, "test", CLOCK_START, history_days=HISTORY_DAYS)
    store.write_run(connect_as("sim_owner"), params, generate(params))
    source = connect_as("sim_outbox_reader")
    while batch := sim_source.pull(
        source, second_run, ingest_repo.read_cursor(dest, tenant, connector), 5000
    ):
        ingest_repo.apply_batch(dest, tenant, site, connector, batch)
    state = site_repo.latest_run(admin, tenant, site)
    assert state is not None and state.run_id == second_run and state.history_loaded


def test_order_line_without_its_order_is_rejected(connect_as: Connect, loaded: tuple[UUID, ...]) -> None:
    """Review finding 5: an orphan line fails loudly instead of being dropped."""
    tenant, site, connector, run = loaded
    orphan = ingest_repo.SourceRecord(
        sequence=10_000_002,
        run_id=run,
        record_type="ORDER_LINE",
        occurred_at=CLOCK_START,
        dataset_version="test",
        is_synthetic=True,
        payload={
            "external_id": "SO-MISSING-01",
            "order_no": "SO-MISSING",
            "line_no": 1,
            "sku": "SKU-102",
            "qty_ordered": 1,
        },
    )
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        ingest_repo.apply_batch(connect_as("pw_ingest"), tenant, site, connector, [orphan])
