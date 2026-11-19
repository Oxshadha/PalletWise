"""Connections with tenant context set per transaction (CWM 11.1).

Every process borrows connections from a per-role pool. The pool checks a connection on
borrow and discards broken ones, so a database restart needs no reconnect logic in callers.
"""

from collections.abc import Generator
from contextlib import contextmanager
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


_pools: dict[str, ConnectionPool] = {}


def role_pool(role_env_prefix: str, *, max_size: int = 10) -> ConnectionPool:
    """One pool per role per process, created on first use (e.g. role_pool("PW_APP")).

    Workers that need a single connection pass max_size=1.
    """
    if role_env_prefix not in _pools:
        _pools[role_env_prefix] = ConnectionPool(
            dsn_for_role(role_env_prefix),
            min_size=1,
            max_size=max_size,
            open=True,
            check=ConnectionPool.check_connection,
            kwargs={"autocommit": False},
        )
    return _pools[role_env_prefix]


def close_pools() -> None:
    while _pools:
        _pools.popitem()[1].close()


@contextmanager
def tenant_transaction(conn: psycopg.Connection, tenant_id: UUID) -> Generator[psycopg.Connection]:
    """Run one transaction with transaction-local tenant context.

    `set_config(..., true)` resets at commit or rollback, so a pooled connection can never
    carry one request's tenant into another.
    """
    with conn.transaction():
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        yield conn


# Names for code outside palletwise.db, which never imports a driver (baseline 4.1).
Pool = ConnectionPool
Connection = psycopg.Connection


READ_STATEMENT_TIMEOUT = "3s"  # semantic spec 7.4


@contextmanager
def tenant_session(
    pool: ConnectionPool, tenant_id: UUID, *, read_snapshot: bool = False, subject: str | None = None
) -> Generator[psycopg.Connection]:
    """A pooled connection with one transaction bound to the tenant.

    `read_snapshot` makes it a read-only REPEATABLE READ transaction with a statement timeout:
    every statement sees one snapshot, so the site time and the stock read in one tool call
    describe the same moment even while the simulator advances (semantic spec 2, 7.4).
    """
    with pool.connection() as conn, conn.transaction():
        if read_snapshot:
            # Must be the first statement of the transaction.
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            conn.execute("SELECT set_config('statement_timeout', %s, true)", (READ_STATEMENT_TIMEOUT,))
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        if subject is not None:  # tables owned by one user (conversations) also check the subject
            conn.execute("SELECT set_config('app.user_subject', %s, true)", (subject,))
        yield conn
