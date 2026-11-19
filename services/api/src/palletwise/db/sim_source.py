"""Source side of the simulator connector: reads sim.outbox as sim_outbox_reader.

A separate connection and role from the destination (pw_ingest): neither role can reach
the other side (simulator spec 2).
"""

from datetime import datetime
from uuid import UUID

import psycopg

from palletwise.db.ingest_repo import SourceRecord


def pull(conn: psycopg.Connection, run_id: UUID, after_sequence: int, limit: int) -> list[SourceRecord]:
    """Next records of a run in global sequence order (connector contract `pull`)."""
    with conn.transaction():
        rows = conn.execute(
            """
            SELECT seq, run_id, record_type, sim_time, dataset_version, payload
              FROM sim.outbox
             WHERE run_id = %s AND seq > %s
             ORDER BY seq
             LIMIT %s
            """,
            (run_id, after_sequence, limit),
        ).fetchall()
    return [
        SourceRecord(
            sequence=r[0],
            run_id=r[1],
            record_type=r[2],
            occurred_at=r[3],
            dataset_version=r[4],
            is_synthetic=True,
            payload=r[5],
        )
        for r in rows
    ]


def newest_time(conn: psycopg.Connection, run_id: UUID) -> datetime | None:
    """Simulated time of the newest record of a run in the outbox (the ingest lag reference)."""
    with conn.transaction():
        row = conn.execute("SELECT max(sim_time) FROM sim.outbox WHERE run_id = %s", (run_id,)).fetchone()
    return row[0] if row else None
