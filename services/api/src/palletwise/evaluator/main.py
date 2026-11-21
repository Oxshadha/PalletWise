"""Exception engine worker (role pw_evaluator; Slice 3 step 1, slice-3-acceptance 2.3).

Every few seconds it asks each site of the tenant to evaluate itself. The database decides
whether there is anything new: `pw.evaluate_site_exceptions` compares the site's evaluation key
(simulation run, data watermark, settings version, 15-minute clock boundary) with the last one,
evaluates under a per-site lock in one transaction, and returns 'unchanged' otherwise. So this
loop is the reconciliation of 2.3; the wake-ups from the change sequence arrive with step 2.

A failed evaluation rolls back, so the previous committed state stays visible; the site is tried
again after a backoff that doubles up to a minute.
"""

import logging
import os
import sys
import threading
from dataclasses import dataclass
from uuid import UUID

from palletwise import clock
from palletwise.db.connection import role_pool, tenant_session
from palletwise.settings import DEMO_TENANT_ID
from palletwise.telemetry import metrics
from palletwise.telemetry.health import health_port, serve_health, start_process
from palletwise.telemetry.tracing import tracer

log = logging.getLogger("palletwise.evaluator")

RECONCILE_SECONDS = float(os.environ.get("PW_EVALUATOR_RECONCILE_SECONDS", "5"))
MAX_BACKOFF_SECONDS = 60.0
RULES = 6  # semantic spec 6 without FESTIVAL_SHORTAGE (Slice 5)


@dataclass
class _Retry:
    failures: int = 0
    next_attempt: float = 0.0


class EvaluatorWorker:
    def __init__(self, tenant: UUID = DEMO_TENANT_ID) -> None:
        self._tenant = tenant
        self._pool = role_pool("PW_EVALUATOR", max_size=1)
        self._lock = threading.Lock()
        self._ready = True
        self._evaluations = 0
        self._retry: dict[UUID, _Retry] = {}

    def status(self) -> dict[str, object]:
        with self._lock:
            return {"ready": self._ready, "rules": RULES, "evaluations": self._evaluations}

    def sites(self) -> list[UUID]:
        with tenant_session(self._pool, self._tenant) as conn:
            rows = conn.execute("SELECT site_id FROM sem.exception_state ORDER BY site_id").fetchall()
        return [r[0] for r in rows]

    def evaluate(self, site: UUID) -> str:
        """One site; 'evaluated', 'unchanged', 'not_ready', or 'failed' (retried later)."""
        retry = self._retry.setdefault(site, _Retry())
        if clock.monotonic() < retry.next_attempt:
            return "waiting"
        started = clock.monotonic()
        try:
            with tracer.start_as_current_span("exceptions.evaluate") as span:
                with tenant_session(self._pool, self._tenant) as conn:
                    row = conn.execute("SELECT * FROM pw.evaluate_site_exceptions(%s)", (site,)).fetchone()
                assert row is not None
                outcome, opened, rescored, resolved = row
                span.set_attribute("pw.exceptions.outcome", outcome)
                span.set_attribute("pw.exceptions.opened", opened)
                span.set_attribute("pw.exceptions.rescored", rescored)
                span.set_attribute("pw.exceptions.resolved", resolved)
        except Exception:
            retry.failures += 1
            retry.next_attempt = clock.monotonic() + min(2.0**retry.failures, MAX_BACKOFF_SECONDS)
            log.exception(
                "exception evaluation failed; retrying after a pause", extra={"failures": retry.failures}
            )
            metrics.exception_evaluations.labels(outcome="failed").inc()
            return "failed"
        retry.failures, retry.next_attempt = 0, 0.0
        metrics.exception_evaluations.labels(outcome=outcome).inc()
        if outcome == "evaluated":
            metrics.exception_evaluation_duration.observe(clock.monotonic() - started)
            log.info(
                "exceptions evaluated", extra={"opened": opened, "rescored": rescored, "resolved": resolved}
            )
        return outcome

    def run_once(self) -> dict[UUID, str]:
        outcomes = {site: self.evaluate(site) for site in self.sites()}
        with self._lock:
            # Not ready while any site's last attempt failed, including while it waits out its
            # backoff ("waiting" is not success; code review).
            self._ready = not any(self._retry.get(site, _Retry()).failures for site in outcomes)
            self._evaluations += sum(o == "evaluated" for o in outcomes.values())
        return outcomes

    def run_forever(self) -> None:
        while True:
            try:
                self.run_once()
            except Exception:  # the site list itself failed (database unreachable)
                log.exception("could not list sites; retrying")
                with self._lock:
                    self._ready = False
            clock.sleep(RECONCILE_SECONDS)


def main() -> int:
    start_process("palletwise-evaluator")
    worker = EvaluatorWorker()
    serve_health(health_port(8083), worker.status)
    log.info("exception engine started")
    worker.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
