"""The only module that reads wall-clock time.

Business rules never use wall time: they receive `as_of` from the site clock
(canonical model 7.3). Wall time here serves infrastructure only: timeouts,
polling intervals and log timestamps (engineering baseline 4.1).
"""

import time
from datetime import UTC, datetime


def wall_now() -> datetime:
    """Current UTC wall time, for infrastructure use only."""
    return datetime.now(UTC)


def monotonic() -> float:
    """Monotonic seconds, for timeouts and intervals."""
    return time.monotonic()


def sleep(seconds: float) -> None:
    time.sleep(seconds)
