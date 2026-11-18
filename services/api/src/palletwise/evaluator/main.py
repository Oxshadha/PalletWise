"""Exception engine worker (role pw_evaluator). Slice 0: process, identity and health only."""

import logging
import os
import sys

from palletwise import clock
from palletwise.telemetry.health import serve_health

log = logging.getLogger("palletwise.evaluator")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    serve_health(int(os.environ.get("PW_HEALTH_PORT", "8083")), lambda: {"ready": True, "rules": 0})
    log.info("evaluator started (no rules until the exceptions slice)")
    while True:
        clock.sleep(3600)


if __name__ == "__main__":
    sys.exit(main())
