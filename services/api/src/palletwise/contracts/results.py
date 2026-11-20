"""The result envelope every tool returns (semantic spec 7.5) and the highlight contract
(agent spec 10). Defined once here; the API, the agent and the web app all use these shapes.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

MAX_HIGHLIGHTS = 200  # semantic spec 7.6
MAX_ENTITIES = 500  # working set limit, semantic spec 7.5


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class HighlightReason(StrEnum):
    QUERY = "query"
    LOCATION = "location"
    FAST_MOVER = "fast_mover"
    LOW_STOCK = "low_stock"
    STOCKOUT_RISK = "stockout_risk"
    DELAYED_ORDER = "delayed_order"
    DEAD_STOCK = "dead_stock"
    SLOW_IN_PRIME = "slow_in_prime"
    CAPACITY = "capacity"
    FESTIVAL_RISK = "festival_risk"
    PROPOSAL = "proposal"


# Default severity per reason (agent spec 10). Reasons whose severity depends on the case
# (stock-out risk, capacity, festival risk) are set by their rule and have no default.
DEFAULT_SEVERITY: dict[HighlightReason, Severity] = {
    HighlightReason.QUERY: Severity.INFO,
    HighlightReason.LOCATION: Severity.INFO,
    HighlightReason.FAST_MOVER: Severity.INFO,
    HighlightReason.LOW_STOCK: Severity.WARNING,
    HighlightReason.DELAYED_ORDER: Severity.CRITICAL,
    HighlightReason.DEAD_STOCK: Severity.INFO,
    HighlightReason.SLOW_IN_PRIME: Severity.WARNING,
    HighlightReason.PROPOSAL: Severity.INFO,
}


class RequestRejected(ValueError):
    """A tool request refused for a reason the agent can act on (semantic spec 7.4): reported as a
    validation failure with this message, and counted toward the step limit (NFR-REL-03)."""


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Highlight(Frozen):
    target_type: Literal["bin", "rack", "zone"]
    target_id: UUID
    reason: HighlightReason
    severity: Severity


class Column(Frozen):
    key: str
    label: str
    type: Literal["text", "integer", "number", "boolean", "datetime"]
    unit: str | None = None

    @property
    def numeric(self) -> bool:
        return self.type in ("integer", "number")


class StreamFreshness(Frozen):
    stream: Literal["MASTER", "MOVEMENTS", "ORDERS", "ALLOCATIONS"]
    data_as_of: datetime | None  # None: never synced, so unavailable (CWM 10.2)


class Evidence(Frozen):
    as_of: datetime
    time_zone: str
    # The settings version in effect for the whole answer (semantic spec 2, Slice 2 acceptance).
    settings_version_id: UUID | None
    settings_version_no: int | None = None  # the same version as people read it ("settings v2")
    data_as_of: datetime | None  # the oldest data_as_of among the streams used
    freshness: tuple[StreamFreshness, ...]
    how_computed: tuple[str, ...]


class EntityRef(Frozen):
    type: Literal["product", "bin", "zone", "order"]
    id: UUID
    label: str


class ResultStatus(StrEnum):
    OK = "OK"  # rows answer the question
    NO_RESULTS = "NO_RESULTS"  # a valid question with no matching data
    NOT_FOUND = "NOT_FOUND"  # "no such {dimension}: {value}" (semantic spec 7.4)
    AMBIGUOUS = "AMBIGUOUS"  # several candidates; rows list them, nothing is guessed
    UNAVAILABLE = "UNAVAILABLE"  # a required stream has never synced (CWM 10.2)


class ResultEnvelope(Frozen):
    status: ResultStatus
    message: str | None = None  # plain explanation for every status except OK
    columns: tuple[Column, ...] = ()
    rows: tuple[dict[str, Any], ...] = ()
    truncated: bool = False
    highlights: tuple[Highlight, ...] = ()
    highlights_truncated: bool = False
    evidence: Evidence
    entities: tuple[EntityRef, ...] = ()
    normalized_request: dict[str, Any]

    @property
    def row_count(self) -> int:
        return len(self.rows)


class WorkingSet(Frozen):
    """The previous answer's identifiers for follow-up questions (semantic spec 7.5, Slice 2 D5).

    Only typed identifiers, never values: a follow-up re-reads current data for them. Scoped to
    one conversation, owner, site and tenant by the conversation store; at most MAX_ENTITIES.
    """

    run_id: UUID  # provenance: the run that produced the result
    tool: str | None  # provenance: the tool that produced it
    created_at: datetime
    products: tuple[UUID, ...] = ()
    bins: tuple[UUID, ...] = ()
    zones: tuple[UUID, ...] = ()

    def ids(self, kind: str) -> tuple[UUID, ...]:
        return {"product": self.products, "bin": self.bins, "zone": self.zones}.get(kind, ())

    @property
    def size(self) -> int:
        return len(self.products) + len(self.bins) + len(self.zones)
