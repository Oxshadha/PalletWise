"""Connections with tenant context set per transaction (CWM 11.1)."""

from collections.abc import Generator
from contextlib import contextmanager, suppress
from uuid import UUID

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg_pool import ConnectionPool

from palletwise.settings import DatabaseSettings, role_credentials


def dsn(db: DatabaseSettings, user: str, password: str) -> str:
    """libpq connection string with every value escaped (spaces, quotes, '=' are safe)."""
    return make_conninfo(
        host=db.host,
        port=db.port,
        dbname=db.name,
        user=user,
        password=password,
        application_name="palletwise",
    )


def dsn_for_role(role_env_prefix: str) -> str:
    """Connection string for the role configured under e.g. PW_APP_USER / PW_APP_PASSWORD."""
    creds = role_credentials(role_env_prefix)
    return dsn(DatabaseSettings(), creds.user, creds.password)


def connect(conninfo: str) -> psycopg.Connection:
    """Open a connection. Autocommit is off: every unit of work is one transaction."""
    return psycopg.connect(conninfo, autocommit=False)


def open_pool(conninfo: str, *, min_size: int = 1, max_size: int = 10) -> ConnectionPool:
    """A connection pool for request-serving processes. Opened lazily, checked on borrow."""
    return ConnectionPool(
        conninfo,
        min_size=min_size,
        max_size=max_size,
        open=True,
        check=ConnectionPool.check_connection,
        kwargs={"autocommit": False},
    )


_pools: dict[str, ConnectionPool] = {}


def role_pool(role_env_prefix: str) -> ConnectionPool:
    """One pool per role per process, created on first use (e.g. role_pool("PW_APP"))."""
    if role_env_prefix not in _pools:
        _pools[role_env_prefix] = open_pool(dsn_for_role(role_env_prefix))
    return _pools[role_env_prefix]


def close_pools() -> None:
    while _pools:
        _pools.popitem()[1].close()


class Reconnecting:
    """A single connection that is replaced when it breaks (database restart, network blip)."""

    def __init__(self, conninfo: str) -> None:
        self._conninfo = conninfo
        self._conn: psycopg.Connection | None = None

    def get(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed or self._conn.broken:
            self.reset()
            self._conn = connect(self._conninfo)
        return self._conn

    def reset(self) -> None:
        if self._conn is not None:
            with suppress(psycopg.Error):
                self._conn.close()
        self._conn = None


@contextmanager
def tenant_transaction(conn: psycopg.Connection, tenant_id: UUID) -> Generator[psycopg.Connection]:
    """Run one transaction with transaction-local tenant context.

    `set_config(..., true)` resets at commit or rollback, so a pooled connection can never
    carry one request's tenant into another.
    """
    with conn.transaction():
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        yield conn
