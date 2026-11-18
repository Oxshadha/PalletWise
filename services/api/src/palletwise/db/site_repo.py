"""Simulated site lifecycle, used by the simulation admin service as pw_sim_admin."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import psycopg

from palletwise.db.connection import tenant_transaction


@dataclass(frozen=True, slots=True)
class SiteBootstrap:
    tenant_id: UUID
    tenant_code: str
    tenant_name: str
    site_id: UUID
    site_code: str
    site_name: str
    region_code: str
    time_zone: str
    width_m: float
    depth_m: float
    connector_id: UUID
    run_id: UUID
    seed: int
    dataset_version: str
    history_start: datetime
    clock_start: datetime
    started_by: str


@dataclass(frozen=True, slots=True)
class RunState:
    run_id: UUID
    status: str
    history_loaded: bool
    dataset_hash: str | None
    seed: int
    dataset_version: str


def bootstrap_site(conn: psycopg.Connection, b: SiteBootstrap) -> None:
    with tenant_transaction(conn, b.tenant_id):
        conn.execute(
            "SELECT pw.bootstrap_simulated_site(%s::uuid, %s::text, %s::text, %s::uuid, %s::text, %s::text, "
            "%s::text, %s::text, %s::numeric, %s::numeric, %s::uuid, %s::uuid, %s::bigint, %s::text, "
            "%s::timestamptz, %s::timestamptz, %s::text)",
            (
                b.tenant_id,
                b.tenant_code,
                b.tenant_name,
                b.site_id,
                b.site_code,
                b.site_name,
                b.region_code,
                b.time_zone,
                b.width_m,
                b.depth_m,
                b.connector_id,
                b.run_id,
                b.seed,
                b.dataset_version,
                b.history_start,
                b.clock_start,
                b.started_by,
            ),
        )


def latest_run(conn: psycopg.Connection, tenant_id: UUID, site_id: UUID) -> RunState | None:
    with tenant_transaction(conn, tenant_id):
        row = conn.execute(
            """
            SELECT id, status, history_loaded_at IS NOT NULL, dataset_hash, seed, dataset_version
              FROM pw.simulation_run WHERE site_id = %s ORDER BY started_at DESC LIMIT 1
            """,
            (site_id,),
        ).fetchone()
    if row is None:
        return None
    return RunState(
        run_id=row[0],
        status=row[1],
        history_loaded=row[2],
        dataset_hash=row[3],
        seed=row[4],
        dataset_version=row[5],
    )


def complete_load(
    conn: psycopg.Connection, tenant_id: UUID, site_id: UUID, run_id: UUID, dataset_hash: str
) -> None:
    with tenant_transaction(conn, tenant_id):
        conn.execute("SELECT pw.complete_load(%s, %s, %s, %s)", (tenant_id, site_id, run_id, dataset_hash))


def fail_load(conn: psycopg.Connection, tenant_id: UUID, site_id: UUID, run_id: UUID, detail: str) -> None:
    with tenant_transaction(conn, tenant_id):
        conn.execute("SELECT pw.fail_load(%s, %s, %s, %s)", (tenant_id, site_id, run_id, detail))


def site_status(conn: psycopg.Connection, tenant_id: UUID, site_id: UUID) -> tuple[str, str | None] | None:
    with tenant_transaction(conn, tenant_id):
        row = conn.execute(
            "SELECT data_status, data_status_detail FROM pw.site WHERE id = %s", (site_id,)
        ).fetchone()
    return (row[0], row[1]) if row else None
