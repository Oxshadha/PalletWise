"""The site change sequence (migration 0022_site_changes): reads for the live stream, and the
notification listener that wakes it (agent spec 9.2, slice-3-acceptance 2.1)."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID

import psycopg

from palletwise.db.connection import Pool, dsn_for_role, tenant_session

CHANNEL = "pw_site_changes"


@dataclass(frozen=True)
class Changes:
    seq: int
    kinds: tuple[str, ...]
    complete: bool


def changes_since(pool: Pool, tenant: UUID, site: UUID, after: int) -> Changes:
    """The site's current sequence and the kinds changed after `after`; `complete` is false when
    the history no longer reaches back that far (the client refetches everything)."""
    with tenant_session(pool, tenant) as conn:
        row = conn.execute("SELECT * FROM pw.site_changes_since(%s, %s)", (site, after)).fetchone()
    assert row is not None
    return Changes(int(row[0]), tuple(row[1]), bool(row[2]))


async def notifications(role_env_prefix: str) -> AsyncIterator[str]:
    """Site IDs as their changes commit, on one dedicated connection. Ends with an exception
    when the connection is lost; the caller reconnects."""
    dsn = dsn_for_role(role_env_prefix)
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        await conn.execute(f"LISTEN {CHANNEL}")
        yield ""  # listening: the caller reconciles whatever happened before this point
        async for note in conn.notifies():
            yield note.payload
