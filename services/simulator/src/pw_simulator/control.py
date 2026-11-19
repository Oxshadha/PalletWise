"""Simulator control API (simulator spec 7). Slice 0: start a run and report health.

Play, pause, speed, jump, scenarios, task submission and reset arrive in later slices.
"""

import logging
import threading
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime

import psycopg
from fastapi import FastAPI, Response, status
from pydantic import BaseModel, Field

from pw_simulator import store
from pw_simulator.history import RunParams, generate

log = logging.getLogger("pw_simulator")

_lock = threading.Lock()
_state: dict[str, object] = {"ready": False, "generating": None, "last_error": None}


class RunRequest(BaseModel):
    run_id: str
    seed: int
    dataset_version: str = Field(min_length=1)
    site_code: str
    clock_start: datetime
    history_days: int = Field(ge=1, le=365)


def _connect() -> psycopg.Connection:
    return psycopg.connect(store.dsn())


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
    with _connect() as conn:
        store.ensure_schema(conn)
    with _lock:
        _state["ready"] = True
    yield


app = FastAPI(title="PalletWise simulator", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health(response: Response) -> dict[str, object]:
    with _lock:
        body = dict(_state)
    if not body["ready"]:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return body


def _generate(req: RunRequest) -> None:
    params = RunParams(
        run_id=req.run_id,
        seed=req.seed,
        dataset_version=req.dataset_version,
        clock_start=req.clock_start,
        history_days=req.history_days,
    )
    try:
        records = generate(params)
        with _connect() as conn:
            store.write_run(conn, params, records)
        log.info("run %s written: %d records", req.run_id, len(records))
    except Exception as exc:
        log.exception("generation failed")
        with _lock:
            _state["last_error"] = str(exc)
    finally:
        with _lock:
            _state["generating"] = None


@app.post("/runs", status_code=status.HTTP_202_ACCEPTED)
def start_run(req: RunRequest, response: Response) -> dict[str, str]:
    with _connect() as conn:
        if store.run_exists(conn, req.run_id):
            response.status_code = status.HTTP_200_OK
            return {"run_id": req.run_id, "state": "EXISTS"}
    with _lock:
        if _state["generating"] is not None:
            if _state["generating"] == req.run_id:
                return {"run_id": req.run_id, "state": "GENERATING"}
            response.status_code = status.HTTP_409_CONFLICT
            return {"run_id": req.run_id, "state": "BUSY"}
        _state["generating"] = req.run_id
    threading.Thread(target=_generate, args=(req,), daemon=True).start()
    return {"run_id": req.run_id, "state": "GENERATING"}
