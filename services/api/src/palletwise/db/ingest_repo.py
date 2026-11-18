"""Ingestion write path, used by the connector worker as pw_ingest (CWM 11.3)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from palletwise.db.connection import tenant_transaction


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """One record from a connector, in global sequence order."""

    sequence: int
    run_id: UUID | None
    record_type: str
    occurred_at: datetime
    dataset_version: str | None
    is_synthetic: bool
    payload: dict[str, Any]


def read_cursor(conn: psycopg.Connection, tenant_id: UUID, connector_id: UUID) -> int:
    """Last applied global sequence; all streams share one cursor (simulator spec 2.1)."""
    with tenant_transaction(conn, tenant_id):
        row = conn.execute(
            "SELECT max(cursor::bigint) FROM pw.sync_state WHERE connector_id = %s", (connector_id,)
        ).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def current_run(conn: psycopg.Connection, tenant_id: UUID, site_id: UUID) -> UUID | None:
    with tenant_transaction(conn, tenant_id):
        row = conn.execute(
            "SELECT id FROM pw.simulation_run WHERE site_id = %s AND status IN ('BUILDING','ACTIVE')",
            (site_id,),
        ).fetchone()
    return row[0] if row else None


def apply_batch(
    conn: psycopg.Connection,
    tenant_id: UUID,
    site_id: UUID,
    connector_id: UUID,
    records: Sequence[SourceRecord],
) -> int:
    """Apply records in order and advance the cursor, all in one transaction.

    Replaying a batch is safe: every record type is idempotent by its natural key.
    Returns the number of records skipped because they belong to an ended run.
    """
    if not records:
        return 0
    skipped = 0
    with tenant_transaction(conn, tenant_id), conn.cursor() as cur:
        for rec in records:
            cur.execute(
                "SELECT pw.apply_record(%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    tenant_id,
                    site_id,
                    connector_id,
                    rec.run_id,
                    rec.record_type,
                    rec.occurred_at,
                    rec.dataset_version,
                    rec.is_synthetic,
                    Jsonb(rec.payload),
                ),
            )
            result = cur.fetchone()
            if result and result[0] == "SKIPPED_OLD_RUN":
                skipped += 1
        cur.execute(
            "SELECT pw.set_cursor(%s, %s, %s)",
            (tenant_id, connector_id, str(records[-1].sequence)),
        )
    return skipped
