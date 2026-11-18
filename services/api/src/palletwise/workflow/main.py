"""Proposal submission and reconciliation worker (role pw_workflow).

Slice 0: process, identity and health only.
"""

import logging
import os
import sys

from palletwise import clock
from palletwise.telemetry.health import serve_health

log = logging.getLogger("palletwise.workflow")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    serve_health(int(os.environ.get("PW_HEALTH_PORT", "8084")), lambda: {"ready": True, "tasks": 0})
    log.info("workflow worker started (no tasks until the proposals slice)")
    while True:
        clock.sleep(3600)


if __name__ == "__main__":
    sys.exit(main())
