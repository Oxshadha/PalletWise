"""Acknowledging an exception (FR-EXC-04, slice-3-acceptance step 3), as pw_app.

The write is `pw.acknowledge_exception()`, the only exception write pw_app may make (CWM 11.3).
The audit entry is appended in the same transaction, so an acknowledgement is never recorded
without its audit entry, nor the reverse.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from psycopg.types.json import Jsonb

from palletwise.db.audit_repo import AuditEntry
from palletwise.db.connection import Pool, tenant_transaction

Outcome = Literal["acknowledged", "unchanged", "resolved", "not_found"]


@dataclass(frozen=True, slots=True)
class Acknowledgement:
    outcome: Outcome
    acknowledged_by: str | None
    acknowledged_at: datetime | None


def acknowledge(
    pool: Pool,
    tenant: UUID,
    site: UUID,
    exception_id: UUID,
    subject: str,
    audit: Callable[[Outcome], AuditEntry],
) -> Acknowledgement:
    with pool.connection() as conn, tenant_transaction(conn, tenant):
        row = conn.execute(
            "SELECT outcome, acknowledged_by, acknowledged_at FROM pw.acknowledge_exception(%s, %s, %s)",
            (site, exception_id, subject),
        ).fetchone()
        assert row is not None
        found = Acknowledgement(*row)
        conn.execute("SELECT pw.append_audit(%s, %s)", (tenant, Jsonb(audit(found.outcome).as_json())))
    return found
