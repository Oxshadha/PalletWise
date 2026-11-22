"""The WMS connector contract (ADR 0003): every warehouse system plugs in behind one interface.

A connector reads its WMS and returns canonical records: the same record types and payload fields
the connector worker applies through `pw.apply_record`, whatever the source system looks like.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class CanonicalRecord:
    """One change in the warehouse, in PalletWise's own vocabulary."""

    record_type: str  # PRODUCT, BIN, ORDER, ORDER_LINE, RECEIPT, PICK, SHIP, ...
    occurred_at: datetime
    payload: dict[str, Any]
    source_system: str
    external_id: str  # the WMS's own id, kept for lineage and idempotent replays


@dataclass(frozen=True, slots=True)
class Batch:
    records: list[CanonicalRecord]
    cursor: str  # opaque position to resume from; stored with the applied batch


@dataclass(frozen=True, slots=True)
class Health:
    ok: bool
    detail: str = ""
    streams: dict[str, str] = field(default_factory=dict[str, str])


class WmsConnector(Protocol):
    """What the connector worker needs from any WMS."""

    source_system: str

    def pull(self, cursor: str | None, limit: int) -> Batch:
        """The next records after `cursor`, oldest first, at most `limit`."""
        ...

    def health(self) -> Health: ...
