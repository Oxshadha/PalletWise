"""Connections with tenant context set per transaction (CWM 11.1)."""

from collections.abc import Generator
from contextlib import contextmanager
from uuid import UUID

import psycopg


def connect(dsn: str) -> psycopg.Connection:
    """Open a connection. Autocommit is off: every unit of work is one transaction."""
    return psycopg.connect(dsn, autocommit=False)


@contextmanager
def tenant_transaction(conn: psycopg.Connection, tenant_id: UUID) -> Generator[psycopg.Connection]:
    """Run one transaction with transaction-local tenant context.

    `set_config(..., true)` resets at commit or rollback, so a pooled connection can never
    carry one request's tenant into another.
    """
    with conn.transaction():
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant_id),))
        yield conn
