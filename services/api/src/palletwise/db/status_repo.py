"""Read-only site status, shared by the API (pw_app) and the simulation admin (pw_sim_admin)."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import psycopg

from palletwise.db.connection import tenant_transaction


@dataclass(frozen=True, slots=True)
class SiteStatus:
    code: str
    name: str
    data_status: str
    data_status_detail: str | None
    sim_time: datetime | None
    time_zone: str = "UTC"
    # When the site's current simulation run started: results from before it describe data that
    # a reset replaced, so their working sets no longer count (FR-SIM-09).
    data_since: datetime | None = None


def site_status(conn: psycopg.Connection, tenant_id: UUID, site_id: UUID) -> SiteStatus | None:
    with tenant_transaction(conn, tenant_id):
        row = conn.execute(
            """
            SELECT s.code, s.name, s.data_status, s.data_status_detail, c.sim_time, s.time_zone,
                   (SELECT max(r.started_at) FROM pw.simulation_run r
                     WHERE r.site_id = s.id AND r.status = 'ACTIVE')
              FROM pw.site s LEFT JOIN pw.site_clock c ON c.site_id = s.id
             WHERE s.id = %s
            """,
            (site_id,),
        ).fetchone()
    if row is None:
        return None
    return SiteStatus(
        code=row[0],
        name=row[1],
        data_status=row[2],
        data_status_detail=row[3],
        sim_time=row[4],
        time_zone=row[5],
        data_since=row[6],
    )


@dataclass(frozen=True, slots=True)
class SiteSummary:
    id: UUID
    code: str
    name: str


def site_summaries(
    conn: psycopg.Connection, tenant_id: UUID, site_ids: tuple[UUID, ...]
) -> list[SiteSummary]:
    """The given sites that exist in the tenant, in code order."""
    with tenant_transaction(conn, tenant_id):
        rows = conn.execute(
            "SELECT id, code, name FROM pw.site WHERE id = ANY(%s) ORDER BY code", (list(site_ids),)
        ).fetchall()
    return [SiteSummary(*row) for row in rows]
