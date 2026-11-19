"""Connector worker for the simulated WMS (simulator spec 2, 2.1).

Two connections, two narrow roles:
  source: sim_outbox_reader, reads sim.outbox only
  destination: pw_ingest, executes pw.apply_record and pw.set_cursor only

Records are applied strictly in global sequence order; each batch and its cursor commit
together, so a crash replays at most one batch, which is idempotent.
"""

import logging
import os
import sys
import threading

from palletwise import clock
from palletwise.db import ingest_repo, sim_source
from palletwise.db.connection import role_pool
from palletwise.settings import DEMO_CONNECTOR_ID, DEMO_SITE_ID, DEMO_TENANT_ID
from palletwise.telemetry.health import configure_logging, health_port, serve_health

log = logging.getLogger("palletwise.connector")

BATCH_SIZE = int(os.environ.get("PW_CONNECTOR_BATCH_SIZE", "5000"))
IDLE_SECONDS = float(os.environ.get("PW_CONNECTOR_IDLE_SECONDS", "1.0"))


class ConnectorWorker:
    def __init__(self) -> None:
        self._source = role_pool("SIM_OUTBOX_READER", max_size=1)
        self._dest = role_pool("PW_INGEST", max_size=1)
        self._lock = threading.Lock()
        self._ready = True
        self._applied = 0
        self._cursor = 0

    def status(self) -> dict[str, object]:
        with self._lock:
            return {"ready": self._ready, "applied": self._applied, "cursor": self._cursor}

    def run_once(self) -> int:
        """Apply at most one batch. Returns the number of records applied."""
        with self._dest.connection() as dest:
            run_id = ingest_repo.current_run(dest, DEMO_TENANT_ID, DEMO_SITE_ID)
            if run_id is None:
                return 0
            cursor = ingest_repo.read_cursor(dest, DEMO_TENANT_ID, DEMO_CONNECTOR_ID, run_id)
            with self._source.connection() as source:
                records = sim_source.pull(source, run_id, cursor, BATCH_SIZE)
            if not records:
                return 0
            skipped = ingest_repo.apply_batch(dest, DEMO_TENANT_ID, DEMO_SITE_ID, DEMO_CONNECTOR_ID, records)
        if skipped:
            log.warning("skipped %d records of an ended run", skipped)
        with self._lock:
            self._applied += len(records)
            self._cursor = records[-1].sequence
        return len(records)

    def run_forever(self) -> None:
        while True:
            try:
                applied = self.run_once()
                with self._lock:
                    self._ready = True
                if applied == 0:
                    clock.sleep(IDLE_SECONDS)
            except Exception:
                # Not ready until a batch succeeds again. The pools discard broken connections,
                # and the failed batch rolled back, so the next attempt starts clean.
                log.exception("batch failed; retrying after a pause")
                with self._lock:
                    self._ready = False
                clock.sleep(5)


def main() -> int:
    configure_logging()
    worker = ConnectorWorker()
    serve_health(health_port(8081), worker.status)
    log.info("connector worker started")
    worker.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
