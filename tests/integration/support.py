"""Helpers shared by integration tests: provisioning and loading sites through the real paths."""

from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg

from palletwise.db import ingest_repo, sim_source, site_repo, status_repo
from pw_simulator import store
from pw_simulator.history import RunParams, generate

Connect = Callable[[str], psycopg.Connection]
CLOCK_START = datetime(2026, 4, 1, 6, 0, tzinfo=ZoneInfo("Asia/Colombo"))
HISTORY_DAYS = 14


def bootstrap_run(
    connect_as: Connect,
    tenant: UUID,
    site: UUID,
    connector: UUID,
    run: UUID,
    history_days: int = HISTORY_DAYS,
    site_code: str = "PLG",
) -> None:
    site_repo.bootstrap_site(
        connect_as("pw_sim_admin"),
        site_repo.SiteBootstrap(
            tenant_id=tenant,
            tenant_code=f"t-{tenant.hex[:8]}",
            tenant_name="Test",
            site_id=site,
            site_code=site_code,
            site_name="Test site",
            region_code="LK",
            time_zone="Asia/Colombo",
            # Floats, exactly as the simulation admin service passes them.
            width_m=80.0,
            depth_m=50.0,
            connector_id=connector,
            run_id=run,
            seed=42,
            dataset_version="test",
            history_start=CLOCK_START - timedelta(days=history_days),
            clock_start=CLOCK_START,
            started_by="test",
        ),
    )


def ingest_run(connect_as: Connect, tenant: UUID, site: UUID, connector: UUID, run: UUID) -> None:
    """Generate the run's outbox and ingest it through the real connector path."""
    params = RunParams(str(run), 42, "test", CLOCK_START, history_days=HISTORY_DAYS)
    sim = connect_as("sim_owner")
    store.ensure_schema(sim)
    store.write_run(sim, params, generate(params))
    source, dest = connect_as("sim_outbox_reader"), connect_as("pw_ingest")
    while batch := sim_source.pull(source, run, ingest_repo.read_cursor(dest, tenant, connector, run), 5000):
        ingest_repo.apply_batch(dest, tenant, site, connector, batch)


def load_site(connect_as: Connect, tenant: UUID) -> tuple[UUID, UUID, UUID]:
    site, connector, run = uuid4(), uuid4(), uuid4()
    bootstrap_run(connect_as, tenant, site, connector, run)
    ingest_run(connect_as, tenant, site, connector, run)
    return site, connector, run


def provision_site(
    connect_as: Connect, tenant: UUID | None = None, site: UUID | None = None, site_code: str = "PLG"
) -> tuple[UUID, UUID]:
    """Creates a tenant and an empty site through the simulation admin path (idempotent per id).

    Site codes are unique per tenant: a second site in the same tenant needs its own code.
    """
    tenant, site = tenant or uuid4(), site or uuid4()
    if status_repo.site_status(connect_as("pw_sim_admin"), tenant, site) is None:
        bootstrap_run(connect_as, tenant, site, uuid4(), uuid4(), history_days=1, site_code=site_code)
    return tenant, site
