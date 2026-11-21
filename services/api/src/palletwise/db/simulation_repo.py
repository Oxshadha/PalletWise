"""What the API needs to move a simulated site's time (Slice 4 step 2), read as pw_app.

PalletWise never moves its own clock: it reads the site's state here and asks the simulator, which
emits the period through the outbox like any WMS (slice-4-acceptance D1).
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from psycopg.types.json import Jsonb

from palletwise.db.audit_repo import AuditEntry
from palletwise.db.connection import Pool, tenant_session, tenant_transaction


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


@dataclass(frozen=True, slots=True)
class Reset:
    outcome: str  # reset, repeated, not_found, not_simulated, loading, busy
    old_run: UUID | None
    new_run: UUID | None
    clock_start: datetime | None


def reset_site(
    pool: Pool, tenant: UUID, site: UUID, new_run: UUID, subject: str, key: str,
    audit: Callable[[Reset], AuditEntry],
) -> Reset:  # fmt: skip
    """`pw.reset_simulated_site()` (CWM 7.4 step 1) and its audit entry in one transaction: a
    failed audit rolls the reset back."""
    with pool.connection() as conn, tenant_transaction(conn, tenant):
        row = conn.execute(
            "SELECT outcome, old_run, new_run, clock_start FROM pw.reset_simulated_site(%s, %s, %s, %s)",
            (site, new_run, subject, key),
        ).fetchone()
        assert row is not None
        found = Reset(*row)
        conn.execute("SELECT pw.append_audit(%s, %s)", (tenant, Jsonb(audit(found).as_json())))
    return found
