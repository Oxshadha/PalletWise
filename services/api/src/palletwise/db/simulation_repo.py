"""What the API needs to move a simulated site's time (Slice 4 step 2), read as pw_app.

PalletWise never moves its own clock: it reads the site's state here and asks the simulator, which
emits the period through the outbox like any WMS (slice-4-acceptance D1).
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from palletwise.db.connection import Pool, tenant_session


@dataclass(frozen=True, slots=True)
class SiteSimulation:
    mode: str  # SIMULATED or REAL (CWM site_clock.mode)
    data_status: str
    site_time: datetime | None
    time_zone: str
    run_id: UUID | None  # the ACTIVE simulation run
    next_morning: datetime | None  # 06:00 on the next operating day after the site time


def site_simulation(pool: Pool, tenant: UUID, site: UUID) -> SiteSimulation | None:
    with tenant_session(pool, tenant) as conn:
        row = conn.execute(
            """
            SELECT c.mode, s.data_status, c.sim_time, s.time_zone,
                   (SELECT r.id FROM pw.simulation_run r
                     WHERE r.site_id = s.id AND r.status = 'ACTIVE'),
                   (SELECT (bc.date + time '06:00') AT TIME ZONE s.time_zone FROM pw.business_calendar bc
                     WHERE bc.region_code = s.region_code AND bc.is_operating
                       AND bc.date > (c.sim_time AT TIME ZONE s.time_zone)::date
                     ORDER BY bc.date LIMIT 1)
              FROM pw.site s JOIN pw.site_clock c ON c.site_id = s.id AND c.tenant_id = s.tenant_id
             WHERE s.id = %s
            """,
            (site,),
        ).fetchone()
    return SiteSimulation(*row) if row else None
