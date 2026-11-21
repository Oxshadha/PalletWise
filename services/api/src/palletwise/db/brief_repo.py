"""The daily brief store (slice-3-acceptance 2.2 as revised 2026-11-21), as pw_brief.

One brief per site, site-local business date and simulation run. No transaction is ever held
across the model call (product owner review of step 5); each operation below is one short
transaction:

- `claim` takes the generation for the day with a lease (`LEASE_SECONDS`), unless a valid lease
  exists;
- the caller reads the facts and words them outside any pw_brief transaction;
- `store` saves the complete brief and clears the claim in one transaction;
- `release` clears the claim when generation failed, so the next request generates at once.

A generator that crashes leaves only its claim, which expires; the next request takes over. Only a
complete brief is ever stored, and a stored brief is never changed (a trigger refuses updates).
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from palletwise.db.connection import Pool, tenant_session

LEASE_SECONDS = 30  # longer than a generation's wording deadline and reads together
_NO_RUN = "-"  # the run key of a site fed by a real WMS

FIND = (
    "SELECT id, business_date, simulation_run_id, items, wording_source, data_watermark, as_of,"
    " settings_version_id, prompt_version, generated_at FROM pw.daily_brief"
    " WHERE site_id = %s AND business_date = %s AND simulation_run_id IS NOT DISTINCT FROM %s"
)
# A claim is taken when there is none, or when the existing one's lease has run out.
CLAIM = (
    "INSERT INTO pw.daily_brief_claim (tenant_id, site_id, business_date, run_key, token, expires_at)"
    " VALUES (%s, %s, %s, %s, %s, now() + make_interval(secs => %s))"
    " ON CONFLICT (tenant_id, site_id, business_date, run_key) DO UPDATE"
    " SET token = EXCLUDED.token, expires_at = EXCLUDED.expires_at"
    " WHERE pw.daily_brief_claim.expires_at < now()"
    " RETURNING token"
)
INSERT = (
    "INSERT INTO pw.daily_brief (id, tenant_id, site_id, business_date, simulation_run_id, items,"
    " wording_source, data_watermark, as_of, settings_version_id, prompt_version)"
    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING"
)
RELEASE = "DELETE FROM pw.daily_brief_claim WHERE site_id = %s AND token = %s"


@dataclass(frozen=True, slots=True)
class StoredBrief:
    id: UUID
    business_date: date
    simulation_run_id: UUID | None
    items: list[dict[str, Any]]
    wording_source: str
    data_watermark: str
    as_of: datetime
    settings_version_id: UUID
    prompt_version: str
    generated_at: datetime


@dataclass(frozen=True, slots=True)
class NewBrief:
    business_date: date
    simulation_run_id: UUID | None
    items: list[dict[str, Any]]
    wording_source: str
    data_watermark: str
    as_of: datetime
    settings_version_id: UUID
    prompt_version: str


def _run_key(run: UUID | None) -> str:
    return str(run) if run is not None else _NO_RUN


def _find(conn: Any, site: UUID, business_date: date, run: UUID | None) -> StoredBrief | None:
    row = conn.execute(FIND, (site, business_date, run)).fetchone()
    return StoredBrief(*row) if row else None


class BriefStore:
    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    def find(self, tenant: UUID, site: UUID, business_date: date, run: UUID | None) -> StoredBrief | None:
        with tenant_session(self._pool, tenant) as conn:
            return _find(conn, site, business_date, run)

    def claim(self, tenant: UUID, site: UUID, business_date: date, run: UUID | None) -> UUID | None:
        """This caller's token when it now owns the generation; None while another holds a lease."""
        token = uuid4()
        with tenant_session(self._pool, tenant) as conn:
            row = conn.execute(
                CLAIM, (tenant, site, business_date, _run_key(run), token, LEASE_SECONDS)
            ).fetchone()
        return token if row is not None and row[0] == token else None

    def store(self, tenant: UUID, site: UUID, token: UUID, brief: NewBrief) -> StoredBrief:
        """Stores the complete brief under the claim's token and clears the claim, in one
        transaction. If another generator stored the day's brief first (this one's lease ran
        out), that brief is returned and this one is dropped."""
        with tenant_session(self._pool, tenant) as conn:
            conn.execute(
                INSERT,
                (
                    token, tenant, site, brief.business_date, brief.simulation_run_id, Jsonb(brief.items),
                    brief.wording_source, brief.data_watermark, brief.as_of, brief.settings_version_id,
                    brief.prompt_version,
                ),
            )  # fmt: skip
            conn.execute(RELEASE, (site, token))
            found = _find(conn, site, brief.business_date, brief.simulation_run_id)
        assert found is not None
        return found

    def release(self, tenant: UUID, site: UUID, token: UUID) -> None:
        with tenant_session(self._pool, tenant) as conn:
            conn.execute(RELEASE, (site, token))
