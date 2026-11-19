"""Login attempts and sessions, only through the owner functions of migration 0003."""

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

import psycopg
from psycopg_pool import ConnectionPool


class TenantNotProvisioned(Exception):
    """The identity names a tenant that does not exist in PalletWise."""


@dataclass(frozen=True, slots=True)
class LoginAttempt:
    nonce: str
    code_verifier: str
    return_to: str


@dataclass(frozen=True, slots=True)
class SessionRow:
    subject: str
    role: str
    tenant: UUID
    sites: tuple[UUID, ...]
    display_name: str
    refresh_due: bool


@dataclass(frozen=True, slots=True)
class SessionRecord:
    """What a session stores about its user, plus the sealed refresh token."""

    subject: str
    role: str
    tenant: UUID
    sites: tuple[UUID, ...]
    display_name: str
    refresh_token: bytes
    key_id: str

    def params(self) -> tuple[object, ...]:
        return (
            self.subject, self.role, self.tenant, list(self.sites),
            self.display_name, self.refresh_token, self.key_id,
        )  # fmt: skip


def _session_row(row: tuple[object, ...] | None) -> SessionRow | None:
    if row is None:
        return None
    subject, role, tenant, sites, name, due = row
    return SessionRow(str(subject), str(role), tenant, tuple(sites), str(name), bool(due))  # type: ignore[arg-type]


def _touch(
    conn: psycopg.Connection, id_hash: bytes, idle: timedelta, revalidate: timedelta
) -> SessionRow | None:
    return _session_row(
        conn.execute("SELECT * FROM pw.touch_session(%s, %s, %s)", (id_hash, idle, revalidate)).fetchone()
    )


@dataclass(frozen=True, slots=True)
class EndedSession:
    refresh_token: bytes
    key_id: str
    subject: str
    role: str
    tenant: UUID


def _end(conn: psycopg.Connection, id_hash: bytes) -> EndedSession | None:
    row = conn.execute("SELECT * FROM pw.end_session(%s)", (id_hash,)).fetchone()
    return EndedSession(*row) if row else None


class LockedSession:
    """A session row locked for one transaction, so its refreshes are serialized."""

    def __init__(self, conn: psycopg.Connection, id_hash: bytes, row: tuple[object, ...]) -> None:
        self._conn = conn
        self._id_hash = id_hash
        self.subject = str(row[0])
        self.tenant: UUID = row[1]  # type: ignore[assignment]
        self.refresh_token: bytes = row[2]  # type: ignore[assignment]
        self.key_id = str(row[3])
        self.refresh_due = bool(row[4])

    def touch(self, idle: timedelta, revalidate: timedelta) -> SessionRow | None:
        return _touch(self._conn, self._id_hash, idle, revalidate)

    def store_refresh(self, record: SessionRecord) -> bool:
        """False when the subject or tenant changed; the session is then ended."""
        row = self._conn.execute(
            "SELECT pw.refresh_session(%s, %s, %s, %s, %s, %s, %s, %s)", (self._id_hash, *record.params())
        ).fetchone()
        return bool(row and row[0])

    def end(self) -> EndedSession | None:
        return _end(self._conn, self._id_hash)


class SessionStore:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def create_login(
        self, state_hash: bytes, nonce: str, verifier: str, return_to: str, ttl: timedelta
    ) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "SELECT pw.create_login(%s, %s, %s, %s, %s)", (state_hash, nonce, verifier, return_to, ttl)
            )

    def consume_login(self, state_hash: bytes) -> LoginAttempt | None:
        with self._pool.connection() as conn:
            row = conn.execute("SELECT * FROM pw.consume_login(%s)", (state_hash,)).fetchone()
        return LoginAttempt(*row) if row else None

    def create_session(self, id_hash: bytes, record: SessionRecord, absolute: timedelta) -> None:
        try:
            with self._pool.connection() as conn:
                conn.execute(
                    "SELECT pw.create_session(%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (id_hash, *record.params(), absolute),
                )
        except psycopg.errors.ForeignKeyViolation as exc:
            raise TenantNotProvisioned from exc

    def touch(self, id_hash: bytes, idle: timedelta, revalidate: timedelta) -> SessionRow | None:
        with self._pool.connection() as conn:
            return _touch(conn, id_hash, idle, revalidate)

    def end(self, id_hash: bytes) -> EndedSession | None:
        """Deletes the session; returns its sealed refresh token and owner."""
        with self._pool.connection() as conn:
            return _end(conn, id_hash)

    @contextmanager
    def locked(self, id_hash: bytes, revalidate: timedelta) -> Generator[LockedSession | None]:
        """Holds the row lock until the block ends; commits on normal exit, rolls back on error."""
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute("SELECT * FROM pw.lock_session(%s, %s)", (id_hash, revalidate)).fetchone()
            yield LockedSession(conn, id_hash, row) if row else None
