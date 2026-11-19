"""Log hygiene for the API process (identity spec 3: no secrets in logs).

The access log never records query strings: the sign-in callback carries a one-time
authorization code and state there.
"""

import logging


class DropQueryStrings(logging.Filter):
    """Uvicorn access records are (client, method, path with query, http version, status)."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str) and "?" in args[2]:
            record.args = (*args[:2], args[2].split("?", 1)[0], *args[3:])
        return True


def configure_api_logging() -> None:
    """PalletWise loggers at INFO next to uvicorn's, and query strings out of the access log."""
    from palletwise.telemetry.health import configure_logging

    configure_logging()
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, DropQueryStrings) for f in access.filters):
        access.addFilter(DropQueryStrings())
