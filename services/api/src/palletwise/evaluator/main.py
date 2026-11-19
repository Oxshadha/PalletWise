"""Exception engine worker (role pw_evaluator). Slice 0: process, identity and health only."""

import sys

from palletwise.telemetry.health import idle_worker


def main() -> int:
    return idle_worker("palletwise.evaluator", 8083, {"ready": True, "rules": 0})


if __name__ == "__main__":
    sys.exit(main())
