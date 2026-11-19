"""Logs (evals spec 13, identity spec 3): JSON lines on stdout, with trace and span ids.

The never-export list of traces applies: bearer and JWT tokens are redacted and email addresses
replaced in every message, extra field and traceback. The access log never records query strings:
the sign-in callback carries a one-time authorization code and state there.
"""

import json
import logging
import sys
import traceback
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace

from palletwise.telemetry.privacy import EMAIL, TOKEN

# Attributes every LogRecord has; anything else was passed with extra= and is logged as a field.
_STANDARD = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


def redact(text: str) -> str:
    return EMAIL.sub("[email]", TOKEN.sub("[redacted]", text))


def _plain(value: Any) -> Any:
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return redact(str(value))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            # Operational time, not domain time: the wall clock is right here (rule 6 covers domain code).
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        context = trace.get_current_span().get_span_context()
        if context.is_valid:
            line["trace_id"] = format(context.trace_id, "032x")
            line["span_id"] = format(context.span_id, "016x")
        for key, value in vars(record).items():
            if key not in _STANDARD and not key.startswith("_"):
                line[key] = _plain(value)
        if record.exc_info and record.exc_info[0] is not None:
            line["error_type"] = record.exc_info[0].__name__
            line["traceback"] = redact("".join(traceback.format_exception(*record.exc_info)))
        return json.dumps(line, ensure_ascii=False, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    """One JSON handler on the root logger; uvicorn's loggers propagate to it."""
    root = logging.getLogger()
    handler = next((h for h in root.handlers if isinstance(h.formatter, JsonFormatter)), None)
    if handler is None:
        for existing in list(root.handlers):
            root.removeHandler(existing)
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True


class DropQueryStrings(logging.Filter):
    """Uvicorn access records are (client, method, path with query, http version, status)."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str) and "?" in args[2]:
            record.args = (*args[:2], args[2].split("?", 1)[0], *args[3:])
        return True


def configure_api_logging() -> None:
    """JSON logs for the API. The request middleware writes one line per request, so uvicorn's
    access log is off; its query-string filter stays in case it is turned back on."""
    configure_logging()
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, DropQueryStrings) for f in access.filters):
        access.addFilter(DropQueryStrings())
    access.disabled = True
