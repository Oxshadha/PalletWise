"""The answer-wording cache store (agent spec 14b), written only by pw_cache.

It sees key hashes, never what they were built from. Entries expire after `ttl`, a tenant keeps at
most `max_entries` (the least recently used go first), and forced row-level security keeps every
read and write inside the caller's tenant. Behind the runtime's WordingCache port, so Redis can
replace it without touching the copilot.
"""

from datetime import timedelta
from uuid import UUID

from psycopg_pool import ConnectionPool

from palletwise.db.connection import tenant_session

DEFAULT_TTL = timedelta(hours=24)
DEFAULT_MAX_ENTRIES = 5000


class WordingCacheStore:
    def __init__(
        self, pool: ConnectionPool, ttl: timedelta = DEFAULT_TTL, max_entries: int = DEFAULT_MAX_ENTRIES
    ) -> None:
        self._pool = pool
        self._ttl = ttl
        self._max = max_entries

    def get(self, tenant: UUID, key_hash: str) -> str | None:
        with tenant_session(self._pool, tenant) as conn:
            row = conn.execute(
                """
                UPDATE pw.wording_cache SET hits = hits + 1, last_used_at = now()
                 WHERE key_hash = %s AND expires_at > now()
                RETURNING wording
                """,
                (key_hash,),
            ).fetchone()
        return str(row[0]) if row else None

    def put(self, tenant: UUID, site: UUID, key_hash: str, tool: str, wording: str) -> None:
        with tenant_session(self._pool, tenant) as conn:
            conn.execute("DELETE FROM pw.wording_cache WHERE expires_at <= now()")
            conn.execute(
                """
                INSERT INTO pw.wording_cache (tenant_id, site_id, key_hash, tool, wording, expires_at)
                VALUES (pw.current_tenant(), %s, %s, %s, %s, now() + %s)
                ON CONFLICT (tenant_id, key_hash) DO UPDATE
                   SET wording = excluded.wording, expires_at = excluded.expires_at, created_at = now()
                """,
                (site, key_hash, tool, wording, self._ttl),
            )
            conn.execute(
                """
                DELETE FROM pw.wording_cache WHERE key_hash IN (
                  SELECT key_hash FROM pw.wording_cache
                   ORDER BY coalesce(last_used_at, created_at) DESC OFFSET %s)
                """,
                (self._max,),
            )
