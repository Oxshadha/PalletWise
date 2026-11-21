"""The exceptions inbox (slice-3-acceptance step 3, FR-EXC-02): the stored records, ranked.

Reads only what the engine stored (`sem.exception*` views); nothing is recalculated here. The
score and its components come from the evaluation (semantic 6.1), the severity from the shared
table (agent spec 10).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from palletwise.contracts.results import (
    EXCEPTION_REASON,
    Column,
    EntityRef,
    Highlight,
    ResultEnvelope,
    ResultStatus,
    Severity,
    exception_severity,
)
from palletwise.db.connection import Connection, query
from palletwise.semantic.locations import read_moment
from palletwise.semantic.model import semantic_model
from palletwise.semantic.rules import STOCK_AND_DEMAND, distinct_entities, distinct_highlights


@dataclass(frozen=True, slots=True)
class AffectedOrder:
    order_id: UUID | None  # None once a simulation reset replaced the order (CWM 9.2)
    order_no: str


@dataclass(frozen=True, slots=True)
class InboxException:
    exception_id: UUID
    exception_type: str
    status: str
    subject_key: str
    subject_label: str
    product_id: UUID | None
    zone_id: UUID | None
    order_id: UUID | None
    severity: Severity
    score: float
    components: dict[str, Any]
    settings_version_id: UUID
    first_detected_at: datetime
    last_evaluated_at: datetime
    acknowledged_by: str | None
    acknowledged_at: datetime | None
    highlights: tuple[Highlight, ...]
    orders: tuple[AffectedOrder, ...]


@dataclass(frozen=True, slots=True)
class Inbox:
    as_of: datetime
    time_zone: str
    exceptions: list[InboxException]


def _label(row: dict[str, Any], orders: tuple[AffectedOrder, ...]) -> str:
    if row["sku"] is not None:
        return f"{row['sku']} {row['product_name']}"
    if row["zone_code"] is not None:
        return f"Zone {row['zone_code']}"
    if orders:
        return f"Order {orders[0].order_no}"
    return row["subject_key"]


def _highlights(row: dict[str, Any], bins: list[UUID], severity: Severity) -> tuple[Highlight, ...]:
    targets: list[tuple[str, UUID, Severity]] = [("bin", b, severity) for b in bins]
    if row["zone_id"] is not None:
        targets.append(("zone", row["zone_id"], severity))
    found, _ = distinct_highlights(targets, EXCEPTION_REASON[row["exception_type"]])
    return found


def inbox(conn: Connection, site: UUID) -> Inbox:
    """Open and acknowledged exceptions of this site, highest score first (row-level security
    adds the tenant)."""
    moment = read_moment(conn, site)
    cursor = query(
        conn,
        "exception_inbox",
        """
        SELECT x.exception_id, x.exception_type, x.status, x.subject_key, x.product_id, x.zone_id,
               x.order_id, x.critical, x.score, x.score_components, x.settings_version_id,
               x.first_detected_at, x.last_evaluated_at, x.acknowledged_by, x.acknowledged_at,
               p.sku, p.name AS product_name, z.zone_code
          FROM sem.exception x
          LEFT JOIN sem.product p ON p.product_id = x.product_id
          LEFT JOIN sem.zone z ON z.zone_id = x.zone_id
         WHERE x.site_id = %s AND x.status IN ('OPEN', 'ACKNOWLEDGED')
         ORDER BY x.score DESC, x.critical DESC, x.first_detected_at, x.exception_id
        """,
        (site,),
    )
    names = [c.name for c in cursor.description or ()]
    rows = [dict(zip(names, r, strict=True)) for r in cursor.fetchall()]
    ids = [r["exception_id"] for r in rows]
    bins: dict[UUID, list[UUID]] = {}
    for exception_id, bin_id in query(
        conn,
        "exception_bins",
        "SELECT exception_id, bin_id FROM sem.exception_bin WHERE site_id = %s"
        " AND exception_id = ANY(%s) ORDER BY exception_id, bin_id",
        (site, ids),
    ).fetchall():
        bins.setdefault(exception_id, []).append(bin_id)
    orders: dict[UUID, list[AffectedOrder]] = {}
    for exception_id, order_id, order_no in query(
        conn,
        "exception_orders",
        "SELECT exception_id, order_id, order_no FROM sem.exception_order WHERE site_id = %s"
        " AND exception_id = ANY(%s) ORDER BY exception_id, order_no",
        (site, ids),
    ).fetchall():
        orders.setdefault(exception_id, []).append(AffectedOrder(order_id, order_no))
    found: list[InboxException] = []
    for r in rows:
        severity = exception_severity(r["exception_type"], r["critical"])
        affected = tuple(orders.get(r["exception_id"], ()))
        found.append(
            InboxException(
                exception_id=r["exception_id"],
                exception_type=r["exception_type"],
                status=r["status"],
                subject_key=r["subject_key"],
                subject_label=_label(r, affected),
                product_id=r["product_id"],
                zone_id=r["zone_id"],
                order_id=r["order_id"],
                severity=severity,
                score=float(r["score"]),
                components=r["score_components"],
                settings_version_id=r["settings_version_id"],
                first_detected_at=r["first_detected_at"],
                last_evaluated_at=r["last_evaluated_at"],
                acknowledged_by=r["acknowledged_by"],
                acknowledged_at=r["acknowledged_at"],
                highlights=_highlights(r, bins.get(r["exception_id"], []), severity),
                orders=affected,
            )
        )
    return Inbox(moment.as_of, moment.time_zone, found)


# ---------------------------------------------------------------- get_open_exceptions (D5, step 7)

TYPE_TEXT = {
    "STOCKOUT_RISK": "Stockout risk",
    "DELAYED_ORDER": "Delayed order",
    "LOW_STOCK": "Low stock",
    "DEAD_STOCK": "Dead stock",
    "SLOW_IN_PRIME": "Slow stock in a prime location",
    "CAPACITY_HOTSPOT": "Capacity hotspot",
    "FESTIVAL_SHORTAGE": "Festival shortage",
}


def _col(key: str, label: str, kind: str = "text", unit: str | None = None) -> Column:
    return Column(key=key, label=label, type=kind, unit=unit)  # type: ignore[arg-type]


OPEN_EXCEPTION_COLUMNS = (
    _col("rank", "Rank", "integer"),
    _col("exception_type", "Type"),
    _col("subject", "Subject"),
    _col("score", "Score", "number"),
    _col("severity", "Severity"),
    _col("status", "Status"),
    _col("lines", "Order lines affected", "integer"),
    _col("hours_until_due", "Hours until due", "number", "hours"),
    _col("value_at_risk", "Value at risk", "number"),
)


def open_exceptions(
    conn: Connection,
    site: UUID,
    limit: int,
    exception_type: str | None = None,
    exception_id: UUID | None = None,
) -> ResultEnvelope:
    """The inbox's own records for the copilot (D5): the stored exceptions, ranked exactly as the
    inbox ranks them, with the components behind each score. Nothing is recalculated. `rank` is the
    position in the whole inbox, so "rank 2" means the same thing to the copilot and the Manager."""
    request: dict[str, Any] = {"tool": "get_open_exceptions", "limit": limit}
    if exception_type is not None:
        request["exception_type"] = exception_type
    if exception_id is not None:
        request["exception_id"] = str(exception_id)
    every = inbox(conn, site)
    ranked = list(enumerate(every.exceptions, start=1))
    if exception_id is not None:
        chosen = [(r, x) for r, x in ranked if x.exception_id == exception_id]
    else:
        chosen = [(r, x) for r, x in ranked if exception_type is None or x.exception_type == exception_type]
    total = len(chosen)
    shown = chosen[:limit]
    moment = read_moment(conn, site)
    how = semantic_model().explain("get_open_exceptions", as_of=moment.local_time)
    evidence = moment.evidence(STOCK_AND_DEMAND, how)
    if not shown:
        message = (
            "That exception is not open at this site."
            if exception_id is not None
            else "No exception of that type is open."
            if exception_type is not None
            else "No exception is open."
        )
        status = ResultStatus.NOT_FOUND if exception_id is not None else ResultStatus.NO_RESULTS
        return ResultEnvelope(status=status, message=message, evidence=evidence, normalized_request=request)
    rows = tuple(
        {
            "rank": rank,
            "exception_type": TYPE_TEXT[x.exception_type],
            "subject": x.subject_label,
            "score": x.score,
            "severity": x.severity.value,
            "status": x.status,
            "lines": x.components.get("lines"),
            "hours_until_due": x.components.get("hours_until_due"),
            "value_at_risk": x.components.get("value_at_risk"),
            "lines_normalized": x.components.get("lines_normalized"),
            "urgency_normalized": x.components.get("urgency_normalized"),
            "value_normalized": x.components.get("value_normalized"),
            "order_nos": [o.order_no for o in x.orders],
            # How many matched in all: a fact of the answer the model must see, not a parameter.
            "open_total": total,
        }
        for rank, x in shown
    )
    seen: dict[tuple[str, UUID], Highlight] = {}
    for _, x in shown:
        for h in x.highlights:
            seen.setdefault((h.target_type, h.target_id), h)
    entities = distinct_entities(
        [EntityRef(type="product", id=x.product_id, label=x.subject_label) for _, x in shown if x.product_id],
        [EntityRef(type="zone", id=x.zone_id, label=x.subject_label) for _, x in shown if x.zone_id],
    )
    return ResultEnvelope(
        status=ResultStatus.OK,
        columns=OPEN_EXCEPTION_COLUMNS,
        rows=rows,
        truncated=total > len(shown),
        highlights=tuple(seen.values()),
        evidence=evidence,
        entities=entities,
        normalized_request=request,
    )
