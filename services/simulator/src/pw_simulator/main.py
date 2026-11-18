"""Simulator entry point: serves the control API."""

import logging
import os
import sys

import uvicorn


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    uvicorn.run(
        "pw_simulator.control:app",
        host="0.0.0.0",  # noqa: S104 (container network)
        port=int(os.environ.get("SIM_PORT", "8000")),
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
