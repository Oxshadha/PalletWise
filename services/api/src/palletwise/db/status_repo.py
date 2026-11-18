"""Read-only status queries for the API (role pw_app)."""

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
    sim_time: datetime | None


def site_status(conn: psycopg.Connection, tenant_id: UUID, site_id: UUID) -> SiteStatus | None:
    with tenant_transaction(conn, tenant_id):
        row = conn.execute(
            """
            SELECT s.code, s.name, s.data_status, c.sim_time
              FROM pw.site s LEFT JOIN pw.site_clock c ON c.site_id = s.id
             WHERE s.id = %s
            """,
            (site_id,),
        ).fetchone()
    if row is None:
        return None
    return SiteStatus(code=row[0], name=row[1], data_status=row[2], sim_time=row[3])
