"""Ingestion write path, used by the connector worker as pw_ingest (CWM 11.3)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from palletwise.db.connection import tenant_transaction

APPLY_SQL = "SELECT pw.apply_record(%s, %s, %s, %s, %s, %s, %s, %s, %s)"


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


def _cursor_text(run_id: UUID | None, sequence: int) -> str:
    """A cursor names its run, so a new run can never inherit an old run's position."""
    return f"{run_id or ''}:{sequence}"


def read_cursor(conn: psycopg.Connection, tenant_id: UUID, connector_id: UUID, run_id: UUID | None) -> int:
    """Last applied sequence of `run_id`; 0 when the stored cursor belongs to another run."""
    with tenant_transaction(conn, tenant_id):
        row = conn.execute(
            "SELECT cursor FROM pw.sync_state WHERE connector_id = %s AND cursor IS NOT NULL LIMIT 1",
            (connector_id,),
        ).fetchone()
    if row is None:
        return 0
    stored_run, _, sequence = str(row[0]).rpartition(":")
    return int(sequence) if stored_run == str(run_id or "") else 0


def current_run(conn: psycopg.Connection, tenant_id: UUID, site_id: UUID) -> UUID | None:
    with tenant_transaction(conn, tenant_id):
        row = conn.execute(
            "SELECT id FROM pw.simulation_run WHERE site_id = %s AND status IN ('BUILDING','ACTIVE')",
            (site_id,),
        ).fetchone()
    return row[0] if row else None


def _without_superseded_ticks(records: Sequence[SourceRecord]) -> list[SourceRecord]:
    """Keep only the batch's last TICK: the batch commits atomically, so earlier ticks are never
    visible, and applying them only rewrites the clock row repeatedly."""
    last_tick = max((i for i, r in enumerate(records) if r.record_type == "TICK"), default=-1)
    return [r for i, r in enumerate(records) if r.record_type != "TICK" or i == last_tick]


def apply_batch(
    conn: psycopg.Connection,
    tenant_id: UUID,
    site_id: UUID,
    connector_id: UUID,
    records: Sequence[SourceRecord],
) -> int:
    """Apply records in order and advance the cursor, all in one transaction.

    Records are sent in pipeline mode (one network round trip per batch, same order, same
    per-record checks). Replaying a batch is safe: every record type is idempotent by its
    natural key. Returns the number of records skipped because they belong to an ended run.
    """
    if not records:
        return 0
    params = [
        (
            tenant_id,
            site_id,
            connector_id,
            r.run_id,
            r.record_type,
            r.occurred_at,
            r.dataset_version,
            r.is_synthetic,
            Jsonb(r.payload),
        )
        for r in _without_superseded_ticks(records)
    ]
    skipped = 0
    with tenant_transaction(conn, tenant_id), conn.cursor() as cur:
        cur.executemany(APPLY_SQL, params, returning=True)
        while True:
            result = cur.fetchone()
            if result and result[0] == "SKIPPED_OLD_RUN":
                skipped += 1
            if not cur.nextset():
                break
        last = records[-1]
        cur.execute(
            "SELECT pw.set_cursor(%s, %s, %s)",
            (tenant_id, connector_id, _cursor_text(last.run_id, last.sequence)),
        )
    return skipped
