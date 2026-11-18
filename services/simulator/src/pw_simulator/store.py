"""The simulator's own storage in the `sim` schema, owned by sim_owner (simulator spec 2).

The run row and the run's outbox are written in one transaction. Generator parameters such as
the true festival uplift stay in `sim.run.params` and are never emitted to the outbox.
"""

import json
import os
from collections.abc import Sequence
from typing import Any

import psycopg

from pw_simulator.catalogue import CATEGORIES, true_uplift
from pw_simulator.history import OutRecord, RunParams

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
REVOKE ALL ON sim.run, sim.outbox FROM PUBLIC;
GRANT SELECT ON sim.outbox TO sim_outbox_reader;
"""


def dsn() -> str:
    return (
        f"host={os.environ.get('PW_DB_HOST', 'localhost')} "
        f"port={os.environ.get('PW_DB_PORT', '5432')} "
        f"dbname={os.environ.get('PW_DB_NAME', 'palletwise')} "
        f"user={os.environ.get('SIM_OWNER_USER', 'sim_owner')} "
        f"password={os.environ['SIM_OWNER_PASSWORD']} application_name=pw-simulator"
    )


def ensure_schema(conn: psycopg.Connection) -> None:
    with conn.transaction():
        conn.execute(DDL)


def run_exists(conn: psycopg.Connection, run_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM sim.run WHERE run_id = %s", (run_id,)).fetchone()
    return row is not None


def hidden_params() -> dict[str, Any]:
    return {"true_uplift": {c.code: true_uplift(c.code) for c in CATEGORIES}}


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
        with conn.cursor().copy(
            "COPY sim.outbox (run_id, seq, record_type, sim_time, dataset_version, payload) FROM STDIN"
        ) as copy:
            for r in records:
                copy.write_row(
                    (
                        params.run_id,
                        r.seq,
                        r.record_type,
                        r.sim_time,
                        params.dataset_version,
                        json.dumps(r.payload, sort_keys=True),
                    )
                )
