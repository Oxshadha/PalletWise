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
from palletwise.db.connection import connect
from palletwise.settings import (
    DEMO_CONNECTOR_ID,
    DEMO_SITE_ID,
    DEMO_TENANT_ID,
    DatabaseSettings,
    role_credentials,
)
from palletwise.telemetry.health import serve_health

log = logging.getLogger("palletwise.connector")

BATCH_SIZE = int(os.environ.get("PW_CONNECTOR_BATCH_SIZE", "2000"))
IDLE_SECONDS = float(os.environ.get("PW_CONNECTOR_IDLE_SECONDS", "1.0"))


class ConnectorWorker:
    def __init__(self) -> None:
        db = DatabaseSettings()
        source_creds = role_credentials("SIM_OUTBOX_READER")
        dest_creds = role_credentials("PW_INGEST")
        self._source = connect(db.dsn(source_creds.user, source_creds.password))
        self._dest = connect(db.dsn(dest_creds.user, dest_creds.password))
        self._lock = threading.Lock()
        self._state: dict[str, object] = {"ready": True, "applied": 0, "cursor": 0}

    def status(self) -> dict[str, object]:
        with self._lock:
            return dict(self._state)

    def run_once(self) -> int:
        """Apply at most one batch. Returns the number of records applied."""
        run_id = ingest_repo.current_run(self._dest, DEMO_TENANT_ID, DEMO_SITE_ID)
        if run_id is None:
            return 0
        cursor = ingest_repo.read_cursor(self._dest, DEMO_TENANT_ID, DEMO_CONNECTOR_ID)
        records = sim_source.pull(self._source, run_id, cursor, BATCH_SIZE)
        if not records:
            return 0
        skipped = ingest_repo.apply_batch(
            self._dest, DEMO_TENANT_ID, DEMO_SITE_ID, DEMO_CONNECTOR_ID, records
        )
        if skipped:
            log.warning("skipped %d records of an ended run", skipped)
        with self._lock:
            self._state["applied"] = int(self._state["applied"]) + len(records)  # type: ignore[arg-type]
            self._state["cursor"] = records[-1].sequence
        return len(records)

    def run_forever(self) -> None:
        while True:
            try:
                if self.run_once() == 0:
                    clock.sleep(IDLE_SECONDS)
            except Exception:
                log.exception("batch failed; retrying after a pause")
                with self._lock:
                    self._state["ready"] = False
                clock.sleep(5)
                with self._lock:
                    self._state["ready"] = True


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    worker = ConnectorWorker()
    serve_health(int(os.environ.get("PW_HEALTH_PORT", "8081")), worker.status)
    log.info("connector worker started")
    worker.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
