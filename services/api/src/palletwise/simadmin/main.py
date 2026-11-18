"""Simulation admin service (simulator spec 7, CWM 7.4), role pw_sim_admin.

Slice 0 responsibility: the initial load.
  1. Create tenant, site, clock, connector and a BUILDING run (site LOADING).
  2. Ask the simulator to generate the run's history into its outbox.
  3. Wait until the connector has ingested everything up to LOAD_COMPLETE.
  4. Compute the dataset hash; compare with the golden hash for the seed if one exists.
  5. Mark the run ACTIVE and the site READY, or both FAILED.
Play, pause, speed, jump, scenarios and reset arrive in a later slice.
"""

import json
import logging
import os
import sys
import threading
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from palletwise import clock
from palletwise.db import dataset_hash, site_repo, status_repo
from palletwise.db.connection import Reconnecting, dsn_for_role
from palletwise.db.ids import uuid7
from palletwise.settings import (
    DEMO_CONNECTOR_ID,
    DEMO_SITE_ID,
    DEMO_TENANT_ID,
    SimulationSettings,
    SiteSettings,
)
from palletwise.telemetry.health import serve_health

log = logging.getLogger("palletwise.simadmin")

SITE_WIDTH_M = 80.0
SITE_DEPTH_M = 50.0
LOAD_TIMEOUT_SECONDS = float(os.environ.get("PW_SIM_LOAD_TIMEOUT_SECONDS", "1800"))


def golden_path(sim: SimulationSettings) -> Path:
    return Path(sim.golden_dir) / f"{sim.dataset_version}-seed{sim.seed}.json"


def request_run(sim: SimulationSettings, payload: dict[str, object]) -> None:
    """POST /runs on the simulator control API. Idempotent on the simulator side."""
    req = urllib.request.Request(  # noqa: S310 (internal service URL from configuration)
        f"{sim.control_url}/runs",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        if resp.status not in (200, 202):
            raise RuntimeError(f"simulator refused the run: HTTP {resp.status}")


def simulator_error(sim: SimulationSettings) -> str | None:
    """The simulator's last generation error, if it reported one."""
    try:
        with urllib.request.urlopen(f"{sim.control_url}/health", timeout=5) as resp:  # noqa: S310
            body = json.loads(resp.read())
    except OSError:
        return None  # unreachable is not a generation error; the timeout covers a dead simulator
    error = body.get("last_error")
    return str(error) if error else None


class SimAdmin:
    def __init__(self) -> None:
        self._db = Reconnecting(dsn_for_role("PW_SIM_ADMIN"))
        self._sim = SimulationSettings()
        self._site = SiteSettings()
        self._lock = threading.Lock()
        self._status: dict[str, object] = {"ready": False, "data_status": "UNKNOWN"}

    def status(self) -> dict[str, object]:
        with self._lock:
            return dict(self._status)

    def _set(self, **values: object) -> None:
        with self._lock:
            self._status.update(values)

    def ensure_run(self) -> site_repo.RunState:
        existing = site_repo.latest_run(self._db.get(), DEMO_TENANT_ID, DEMO_SITE_ID)
        if existing and existing.status in ("BUILDING", "ACTIVE"):
            return existing
        clock_start = datetime.fromisoformat(self._sim.clock_start)
        history_start = clock_start - timedelta(days=self._sim.history_days)
        run_id = uuid7()
        site_repo.bootstrap_site(
            self._db.get(),
            site_repo.SiteBootstrap(
                tenant_id=DEMO_TENANT_ID,
                tenant_code=self._site.tenant_code,
                tenant_name=self._site.tenant_name,
                site_id=DEMO_SITE_ID,
                site_code=self._site.code,
                site_name=self._site.name,
                region_code=self._site.region_code,
                time_zone=self._site.time_zone,
                width_m=SITE_WIDTH_M,
                depth_m=SITE_DEPTH_M,
                connector_id=DEMO_CONNECTOR_ID,
                run_id=run_id,
                seed=self._sim.seed,
                dataset_version=self._sim.dataset_version,
                history_start=history_start,
                clock_start=clock_start,
                started_by="system:initial-load",
            ),
        )
        log.info("run %s created (seed %d)", run_id, self._sim.seed)
        state = site_repo.latest_run(self._db.get(), DEMO_TENANT_ID, DEMO_SITE_ID)
        assert state is not None
        return state

    def start_generation(self, run: site_repo.RunState) -> None:
        clock_start = datetime.fromisoformat(self._sim.clock_start)
        request_run(
            self._sim,
            {
                "run_id": str(run.run_id),
                "seed": run.seed,
                "dataset_version": run.dataset_version,
                "site_code": self._site.code,
                "clock_start": clock_start.isoformat(),
                "history_days": self._sim.history_days,
            },
        )

    def finalize(self, run: site_repo.RunState) -> None:
        digest, counts = dataset_hash.compute(self._db.get(), DEMO_TENANT_ID, DEMO_SITE_ID)
        log.info("dataset hash %s, rows %s", digest, counts)
        path = golden_path(self._sim)
        if path.exists():
            expected = json.loads(path.read_text(encoding="utf-8"))["dataset_hash"]
            if expected != digest:
                raise RuntimeError(f"dataset hash {digest} does not match golden {expected}")
        else:
            log.warning("no golden hash at %s; recording this run's hash is a manual step", path)
        site_repo.complete_load(self._db.get(), DEMO_TENANT_ID, DEMO_SITE_ID, run.run_id, digest)
        log.info("site READY")

    def wait_for_load(self, run: site_repo.RunState) -> site_repo.RunState:
        """Wait until the run's history is ingested, the simulator fails, or the timeout passes."""
        deadline = clock.monotonic() + LOAD_TIMEOUT_SECONDS
        while True:
            try:
                current = site_repo.latest_run(self._db.get(), DEMO_TENANT_ID, DEMO_SITE_ID) or run
            except Exception:
                log.exception("status read failed; reconnecting")
                self._db.reset()
                current = run
            if current.history_loaded:
                return current
            error = simulator_error(self._sim)
            if error:
                raise RuntimeError(f"simulator generation failed: {error}")
            if clock.monotonic() > deadline:
                raise TimeoutError(f"initial load did not finish within {LOAD_TIMEOUT_SECONDS:.0f} seconds")
            clock.sleep(2)

    def run(self) -> None:
        run = self.ensure_run()
        if run.status == "BUILDING":
            try:
                self.start_generation(run)
                run = self.wait_for_load(run)
                self.finalize(run)
            except Exception as exc:
                log.exception("initial load failed")
                self._db.reset()
                site_repo.fail_load(self._db.get(), DEMO_TENANT_ID, DEMO_SITE_ID, run.run_id, str(exc))
                self._set(ready=False, data_status="FAILED", detail=str(exc))
                return
        site = status_repo.site_status(self._db.get(), DEMO_TENANT_ID, DEMO_SITE_ID)
        self._set(
            ready=bool(site and site.data_status == "READY"),
            data_status=site.data_status if site else "UNKNOWN",
        )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    admin = SimAdmin()
    serve_health(int(os.environ.get("PW_HEALTH_PORT", "8082")), admin.status)
    admin.run()
    while True:  # stays up for the admin controls added in a later slice
        clock.sleep(3600)


if __name__ == "__main__":
    sys.exit(main())
