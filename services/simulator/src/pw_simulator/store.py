"""The simulator's own storage in the `sim` schema, owned by sim_owner (simulator spec 2).

The run row and the run's outbox are written in one transaction. Generator parameters such as
the true festival uplift stay in `sim.run.params` and are never emitted to the outbox.
"""

import json
import os
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

import psycopg
from psycopg.conninfo import make_conninfo

from pw_simulator import scenarios
from pw_simulator.catalogue import CATEGORIES, true_uplift
from pw_simulator.history import TICK, Input, OutRecord, RunParams, extend

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
-- The run's input log (simulator spec 5.3, 6): replayed at its times whenever the run is
-- regenerated, so a run is reproduced by its seed plus this log.
CREATE TABLE IF NOT EXISTS sim.input_log (
  run_id       uuid NOT NULL REFERENCES sim.run (run_id),
  n            int NOT NULL,
  at           timestamptz NOT NULL,
  type         text NOT NULL,
  args         jsonb NOT NULL,
  scenario_id  text,
  PRIMARY KEY (run_id, n)
);
-- Slice 4 reset: an ended run takes no more shifts or scenarios (simulator spec 7.1 step 2).
ALTER TABLE sim.run ADD COLUMN IF NOT EXISTS ended_at timestamptz;
REVOKE ALL ON sim.run, sim.outbox, sim.command, sim.input_log FROM PUBLIC;
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


class RunEnded(ValueError):
    """The run was ended by a reset; a new run replaces it."""


def _lock(conn: psycopg.Connection, run_id: str) -> tuple[int, str, datetime, int, datetime]:
    """The run row, locked without waiting: a second operation meanwhile is refused as busy (D9)."""
    try:
        row = conn.execute(
            "SELECT seed, dataset_version, clock_start, history_days,"
            " coalesce(emitted_until, clock_start), ended_at"
            " FROM sim.run WHERE run_id = %s FOR UPDATE NOWAIT",
            (run_id,),
        ).fetchone()
    except psycopg.errors.LockNotAvailable as exc:
        raise Busy(run_id) from exc
    if row is None:
        raise UnknownRun(run_id)
    if row[5] is not None:
        raise RunEnded("This simulation run was reset; a new run replaces it.")
    return (int(row[0]), str(row[1]), row[2], int(row[3]), row[4])


def _done(conn: psycopg.Connection, run_id: str, key: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT result FROM sim.command WHERE run_id = %s AND idempotency_key = %s", (run_id, key)
    ).fetchone()
    return dict(row[0]) if row is not None else None


def _inputs(conn: psycopg.Connection, run_id: str) -> tuple[Input, ...]:
    rows = conn.execute("SELECT at, type, args FROM sim.input_log WHERE run_id = %s ORDER BY n", (run_id,))
    return tuple(Input(at, kind, dict(args)) for at, kind, args in rows.fetchall())


def _emit(
    conn: psycopg.Connection, run_id: str, row: tuple[int, str, datetime, int, datetime], until: datetime
) -> int:
    """Appends the period up to `until` (regenerated from the seed and the input log) and moves
    the run's end; inside the caller's transaction."""
    seed, dataset_version, clock_start, history_days, after = row
    params = RunParams(run_id, seed, dataset_version, clock_start, history_days, inputs=_inputs(conn, run_id))
    last = conn.execute("SELECT max(seq) FROM sim.outbox WHERE run_id = %s", (run_id,)).fetchone()
    records = extend(params, after, until, (last[0] if last and last[0] else 0) + 1)
    _copy_records(conn, run_id, dataset_version, records)
    conn.execute("UPDATE sim.run SET emitted_until = %s WHERE run_id = %s", (until, run_id))
    return len(records)


def _record(conn: psycopg.Connection, run_id: str, key: str, kind: str, result: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO sim.command (run_id, idempotency_key, kind, result) VALUES (%s, %s, %s, %s)",
        (run_id, key, kind, json.dumps(result)),
    )


def shift(conn: psycopg.Connection, run_id: str, until: datetime, key: str) -> dict[str, Any]:
    """Moves the run's time to `until` (Slice 4 step 1, simulator spec 5.2): the period's records
    go to the outbox after the run's last record, in one transaction with the new end of the
    run. One operation per run at a time; the same key returns the first result (D9)."""
    with conn.transaction():
        row = _lock(conn, run_id)
        if (done := _done(conn, run_id, key)) is not None:
            return done
        after = row[4]
        count = _emit(conn, run_id, row, until)
        result = {"run_id": run_id, "from": after.isoformat(), "to": until.isoformat(), "records": count}
        _record(conn, run_id, key, "SHIFT", result)
        return result


def run_scenario(conn: psycopg.Connection, run_id: str, scenario_id: str, key: str) -> dict[str, Any]:
    """Stages a scenario (Slice 4 step 3, simulator spec 6): its preconditions are checked against
    the run's time, its events go to the input log from one minute after the emitted period (so the
    past never changes), and time moves one tick so the first events reach PalletWise at once.
    One transaction; one operation per run at a time; the same key returns the first result."""
    scenario = scenarios.load(scenario_id)
    with conn.transaction():
        row = _lock(conn, run_id)
        if (done := _done(conn, run_id, key)) is not None:
            return done
        after = row[4]
        scenario.check(after)
        start = after + timedelta(minutes=1)
        last = conn.execute(
            "SELECT coalesce(max(n), -1) FROM sim.input_log WHERE run_id = %s", (run_id,)
        ).fetchone()
        first = (last[0] if last else -1) + 1
        for i, inp in enumerate(scenario.inputs(start)):
            conn.execute(
                "INSERT INTO sim.input_log (run_id, n, at, type, args, scenario_id)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                (run_id, first + i, inp.at, inp.type, json.dumps(inp.args), scenario.id),
            )
        count = _emit(conn, run_id, row, after + TICK)
        result = {
            "run_id": run_id,
            "scenario": scenario.id,
            "title": scenario.title,
            "events": len(scenario.events),
            "from": after.isoformat(),
            "to": (after + TICK).isoformat(),
            "records": count,
            "tour": list(scenario.tour),
        }
        _record(conn, run_id, key, "SCENARIO", result)
        return result


def end_run(conn: psycopg.Connection, run_id: str) -> dict[str, Any]:
    """Ends a run for a reset (simulator spec 7.1 step 2): under the run's lock, so a reset while a
    shift or scenario runs is refused as busy (D9). Idempotent: ending an ended run changes nothing.
    Its outbox stays; the connector skips an ended run's records (spec 2.1)."""
    with conn.transaction():
        try:
            row = conn.execute(
                "SELECT ended_at FROM sim.run WHERE run_id = %s FOR UPDATE NOWAIT", (run_id,)
            ).fetchone()
        except psycopg.errors.LockNotAvailable as exc:
            raise Busy(run_id) from exc
        if row is None:
            raise UnknownRun(run_id)
        ended: datetime | None = row[0]
        if ended is None:
            stamped = conn.execute(
                "UPDATE sim.run SET ended_at = now() WHERE run_id = %s RETURNING ended_at", (run_id,)
            ).fetchone()
            assert stamped is not None
            ended = stamped[0]
    assert ended is not None
    return {"run_id": run_id, "ended_at": ended.isoformat()}


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
