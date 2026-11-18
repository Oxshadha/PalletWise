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
from palletwise.db.connection import Reconnecting, dsn_for_role
from palletwise.settings import DEMO_CONNECTOR_ID, DEMO_SITE_ID, DEMO_TENANT_ID
from palletwise.telemetry.health import serve_health

log = logging.getLogger("palletwise.connector")

BATCH_SIZE = int(os.environ.get("PW_CONNECTOR_BATCH_SIZE", "2000"))
IDLE_SECONDS = float(os.environ.get("PW_CONNECTOR_IDLE_SECONDS", "1.0"))


class ConnectorWorker:
    def __init__(self) -> None:
        self._source = Reconnecting(dsn_for_role("SIM_OUTBOX_READER"))
        self._dest = Reconnecting(dsn_for_role("PW_INGEST"))
        self._lock = threading.Lock()
        self._state: dict[str, object] = {"ready": True, "applied": 0, "cursor": 0}

    def status(self) -> dict[str, object]:
        with self._lock:
            return dict(self._state)

    def run_once(self) -> int:
        """Apply at most one batch. Returns the number of records applied."""
        dest = self._dest.get()
        run_id = ingest_repo.current_run(dest, DEMO_TENANT_ID, DEMO_SITE_ID)
        if run_id is None:
            return 0
        cursor = ingest_repo.read_cursor(dest, DEMO_TENANT_ID, DEMO_CONNECTOR_ID)
        records = sim_source.pull(self._source.get(), run_id, cursor, BATCH_SIZE)
        if not records:
            return 0
        skipped = ingest_repo.apply_batch(dest, DEMO_TENANT_ID, DEMO_SITE_ID, DEMO_CONNECTOR_ID, records)
        if skipped:
            log.warning("skipped %d records of an ended run", skipped)
        with self._lock:
            self._state["applied"] = int(self._state["applied"]) + len(records)  # type: ignore[arg-type]
            self._state["cursor"] = records[-1].sequence
        return len(records)

    def run_forever(self) -> None:
        while True:
            try:
                applied = self.run_once()
                with self._lock:
                    self._state["ready"] = True
                if applied == 0:
                    clock.sleep(IDLE_SECONDS)
            except Exception:
                # Not ready until a batch succeeds again; connections are replaced, since a
                # database restart leaves them closed (the batch transaction rolled back).
                log.exception("batch failed; reconnecting and retrying after a pause")
                with self._lock:
                    self._state["ready"] = False
                self._source.reset()
                self._dest.reset()
                clock.sleep(5)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    worker = ConnectorWorker()
    serve_health(int(os.environ.get("PW_HEALTH_PORT", "8081")), worker.status)
    log.info("connector worker started")
    worker.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
