"""Proposal submission and reconciliation worker (role pw_workflow).

Slice 0: process, identity and health only.
"""

import sys

from palletwise.telemetry.health import idle_worker


def main() -> int:
    return idle_worker("palletwise.workflow", 8084, {"ready": True, "tasks": 0})


if __name__ == "__main__":
    sys.exit(main())
