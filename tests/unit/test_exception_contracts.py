"""One severity per exception, as agent spec 10 states (inbox, map and cards share it)."""

from typing import get_args

import pytest

from palletwise.contracts.results import (
    EXCEPTION_REASON,
    ExceptionType,
    HighlightReason,
    Severity,
    exception_severity,
)


@pytest.mark.parametrize(
    ("exception_type", "critical", "expected"),
    [
        ("STOCKOUT_RISK", True, Severity.CRITICAL),  # true shortage or due within 4 hours
        ("STOCKOUT_RISK", False, Severity.WARNING),
        ("DELAYED_ORDER", True, Severity.CRITICAL),
        ("LOW_STOCK", False, Severity.WARNING),
        ("DEAD_STOCK", False, Severity.INFO),
        ("SLOW_IN_PRIME", False, Severity.WARNING),
        ("CAPACITY_HOTSPOT", True, Severity.CRITICAL),  # a bin over capacity
        ("CAPACITY_HOTSPOT", False, Severity.WARNING),
    ],
)
def test_severity_follows_the_shared_table(exception_type: str, critical: bool, expected: Severity) -> None:
    assert exception_severity(exception_type, critical) is expected


def test_every_exception_type_has_one_reason() -> None:
    assert set(EXCEPTION_REASON) == set(get_args(ExceptionType))
    assert EXCEPTION_REASON["CAPACITY_HOTSPOT"] is HighlightReason.CAPACITY
