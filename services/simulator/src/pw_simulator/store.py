"""The simulator's own storage in the `sim` schema, owned by sim_owner (simulator spec 2).

The run row and the run's outbox are written in one transaction. Generator parameters such as
the true festival uplift stay in `sim.run.params` and are never emitted to the outbox.
"""

import json
import os
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import psycopg
from psycopg.conninfo import make_conninfo

from pw_simulator.catalogue import CATEGORIES, true_uplift
from pw_simulator.history import OutRecord, RunParams, extend

DDL = """
CREATE TABLE IF NOT EXISTS sim.run (
  run_id           uuid PRIMARY KEY,
  seed             bigint NOT NULL,
  dataset_version  text NOT NULL,
  clock_start      timestamptz NOT NULL,
  history_days     int NOT NULL,
  params           jsonb NOT NULL,
  record_count     bigint NOT NULL,
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS sim.outbox (
  run_id           uuid NOT NULL REFERENCES sim.run (run_id),
  seq              bigint NOT NULL,
  record_type      text NOT NULL,
  sim_time         timestamptz NOT NULL,
  dataset_version  text NOT NULL,
  payload          jsonb NOT NULL,
  PRIMARY KEY (run_id, seq)
);
-- Slice 4 time shift: how far the run's outbox reaches, and each command once per key.
ALTER TABLE sim.run ADD COLUMN IF NOT EXISTS emitted_until timestamptz;
CREATE TABLE IF NOT EXISTS sim.command (
  run_id           uuid NOT NULL REFERENCES sim.run (run_id),
  idempotency_key  text NOT NULL,
  kind             text NOT NULL,
  result           jsonb NOT NULL,
  created_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (run_id, idempotency_key)
);
REVOKE ALL ON sim.run, sim.outbox, sim.command FROM PUBLIC;
GRANT SELECT ON sim.outbox TO sim_outbox_reader;
"""


def dsn() -> str:
    """libpq connection string with every value escaped."""
    return make_conninfo(
        host=os.environ.get("PW_DB_HOST", "localhost"),
        port=os.environ.get("PW_DB_PORT", "5432"),
        dbname=os.environ.get("PW_DB_NAME", "palletwise"),
        user=os.environ.get("SIM_OWNER_USER", "sim_owner"),
        password=os.environ["SIM_OWNER_PASSWORD"],
        application_name="pw-simulator",
    )


def ensure_schema(conn: psycopg.Connection) -> None:
    with conn.transaction():
        conn.execute(DDL)


def run_exists(conn: psycopg.Connection, run_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM sim.run WHERE run_id = %s", (run_id,)).fetchone()
    return row is not None


def hidden_params() -> dict[str, Any]:
    return {"true_uplift": {c.code: true_uplift(c.code) for c in CATEGORIES}}


class Busy(Exception):
    """Another operation holds the run (Slice 4 D9): one shift, scenario step or reset at a time."""


class UnknownRun(LookupError):
    pass


def _copy_records(
    conn: psycopg.Connection, run_id: str, dataset_version: str, records: Sequence[OutRecord]
) -> None:
    with conn.cursor().copy(
        "COPY sim.outbox (run_id, seq, record_type, sim_time, dataset_version, payload) FROM STDIN"
    ) as copy:
        for r in records:
            copy.write_row(
                (
                    run_id,
                    r.seq,
                    r.record_type,
                    r.sim_time,
                    dataset_version,
                    json.dumps(r.payload, sort_keys=True),
                )
            )


def run_state(conn: psycopg.Connection, run_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT clock_start, coalesce(emitted_until, clock_start) FROM sim.run WHERE run_id = %s", (run_id,)
    ).fetchone()
    if row is None:
        raise UnknownRun(run_id)
    return {"run_id": run_id, "clock_start": row[0].isoformat(), "emitted_until": row[1].isoformat()}


def shift(conn: psycopg.Connection, run_id: str, until: datetime, key: str) -> dict[str, Any]:
    """Moves the run's time to `until` (Slice 4 step 1, simulator spec 5.2): the period's records
    go to the outbox after the run's last record, in one transaction with the new end of the
    run. The run row is locked without waiting, so a second operation meanwhile is refused as
    busy; the same key returns the first result and writes nothing again (D9)."""
    with conn.transaction():
        try:
            row = conn.execute(
                "SELECT seed, dataset_version, clock_start, history_days,"
                " coalesce(emitted_until, clock_start) FROM sim.run WHERE run_id = %s FOR UPDATE NOWAIT",
                (run_id,),
            ).fetchone()
        except psycopg.errors.LockNotAvailable as exc:
            raise Busy(run_id) from exc
        if row is None:
            raise UnknownRun(run_id)
        done = conn.execute(
            "SELECT result FROM sim.command WHERE run_id = %s AND idempotency_key = %s", (run_id, key)
        ).fetchone()
        if done is not None:
            return dict(done[0])
        seed, dataset_version, clock_start, history_days, after = row
        params = RunParams(run_id, seed, dataset_version, clock_start, history_days)
        last = conn.execute("SELECT max(seq) FROM sim.outbox WHERE run_id = %s", (run_id,)).fetchone()
        records = extend(params, after, until, (last[0] if last and last[0] else 0) + 1)
        _copy_records(conn, run_id, dataset_version, records)
        conn.execute("UPDATE sim.run SET emitted_until = %s WHERE run_id = %s", (until, run_id))
        result = {
            "run_id": run_id,
            "from": after.isoformat(),
            "to": until.isoformat(),
            "records": len(records),
        }
        conn.execute(
            "INSERT INTO sim.command (run_id, idempotency_key, kind, result) VALUES (%s, %s, 'SHIFT', %s)",
            (run_id, key, json.dumps(result)),
        )
        return result


def write_run(conn: psycopg.Connection, params: RunParams, records: Sequence[OutRecord]) -> None:
    """Write the run and its full outbox in one transaction; idempotent per run id."""
    with conn.transaction():
        if run_exists(conn, params.run_id):
            return
        conn.execute(
            """
            INSERT INTO sim.run (run_id, seed, dataset_version, clock_start, history_days, params,
                                 record_count)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                params.run_id,
                params.seed,
                params.dataset_version,
                params.clock_start,
                params.history_days,
                json.dumps(hidden_params()),
                len(records),
            ),
        )
        _copy_records(conn, params.run_id, params.dataset_version, records)
