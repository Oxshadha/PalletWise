"""The daily brief's items (slice-3-acceptance D6, step 5; semantic spec 4.6).

Four items, chosen and ranked by code, never by the model: the top three exceptions, the orders
at risk today, yesterday's throughput against the 7 operating days before it, and data freshness.
Each item is a result envelope like a tool's, with rows, evidence and highlights, so the wording is
checked by the same grounding and key-fact rules as the copilot's answers, and the stored brief
keeps the grounded snapshot it was worded from.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from palletwise.contracts.results import (
    Column,
    Highlight,
    ResultEnvelope,
    ResultStatus,
)
from palletwise.db.connection import Connection, query
from palletwise.semantic import exceptions, rules
from palletwise.semantic.exceptions import TYPE_TEXT
from palletwise.semantic.locations import Moment, SiteNotReady, read_moment
from palletwise.semantic.model import semantic_model

ItemKind = Literal["top_exceptions", "orders_at_risk_today", "throughput", "data_freshness"]
TOP_EXCEPTIONS = 3

STREAM_TEXT = {
    "MASTER": "Master data",
    "MOVEMENTS": "Stock movements",
    "ORDERS": "Orders",
    "ALLOCATIONS": "Allocations",
}
ALL_STREAMS = ("MASTER", "MOVEMENTS", "ALLOCATIONS", "ORDERS")


@dataclass(frozen=True, slots=True)
class BriefItem:
    kind: ItemKind
    result_name: str  # the template and key facts that apply (agent templates)
    result: ResultEnvelope
    why: str  # why it was selected (D6)
    link: dict[str, Any] | None  # where it leads (FR-BRF-02): the inbox, or a copilot fast path


@dataclass(frozen=True, slots=True)
class BriefFacts:
    business_date: date
    simulation_run_id: UUID | None  # a reset starts a new run, and the day gets a new brief
    as_of: datetime
    time_zone: str
    settings_version_id: UUID
    data_watermark: str
    items: list[BriefItem]


def _col(key: str, label: str, kind: str = "text", unit: str | None = None) -> Column:
    return Column(key=key, label=label, type=kind, unit=unit)  # type: ignore[arg-type]


def _envelope(
    moment: Moment,
    name: str,
    request: dict[str, Any],
    explain: dict[str, str],
    columns: tuple[Column, ...],
    rows: list[dict[str, Any]],
    empty: str,
    highlights: tuple[Highlight, ...] = (),
) -> ResultEnvelope:
    how = semantic_model().explain(name, **explain)
    evidence = moment.evidence(ALL_STREAMS, how)
    if not rows:
        return ResultEnvelope(
            status=ResultStatus.NO_RESULTS, message=empty, evidence=evidence, normalized_request=request
        )
    return ResultEnvelope(
        status=ResultStatus.OK,
        columns=columns,
        rows=tuple(rows),
        highlights=highlights,
        evidence=evidence,
        normalized_request=request,
    )


EXCEPTION_COLUMNS = (
    _col("rank", "Rank", "integer"),
    _col("exception_type", "Type"),
    _col("subject", "Subject"),
    _col("score", "Score", "number"),
    _col("severity", "Severity"),
)


def _top_exceptions(conn: Connection, site: UUID, moment: Moment) -> BriefItem:
    inbox = exceptions.inbox(conn, site)
    top = inbox.exceptions[:TOP_EXCEPTIONS]
    rows = [
        {
            "rank": rank,
            "exception_type": TYPE_TEXT[x.exception_type],
            "subject": x.subject_label,
            "score": x.score,
            "severity": x.severity.value,
        }
        for rank, x in enumerate(top, start=1)
    ]
    seen: dict[tuple[str, UUID], Highlight] = {}
    for x in top:
        for h in x.highlights:
            seen.setdefault((h.target_type, h.target_id), h)
    total = len(inbox.exceptions)
    result = _envelope(
        moment,
        "brief_top_exceptions",
        {"open_exceptions": total, "shown": len(top)},
        {"as_of": moment.local_time},
        EXCEPTION_COLUMNS,
        rows,
        "No exception is open.",
        tuple(seen.values()),
    )
    why = (
        f"The {len(top)} highest impact scores of the {total} open exceptions, as the inbox ranks them."
        if top
        else "No exception is open."
    )
    return BriefItem("top_exceptions", "brief_top_exceptions", result, why, {"to": "inbox"})


def _orders_at_risk_today(conn: Connection, site: UUID, moment: Moment) -> BriefItem:
    # The copilot's own tool with its exact "today" window: the item and the answer its link opens
    # are the same result (orders due before midnight, site time, overdue included).
    result = rules.order_stockout_risks(conn, site, "orders", None, today=True)
    suggestion = {
        "label": "Which orders are at risk today?",
        "tool": "get_order_stockout_risks",
        "arguments": {"view": "orders", "today": True},
    }
    why = "Open orders due before midnight, site time, with products short in their pick locations."
    return BriefItem(
        "orders_at_risk_today",
        "get_order_stockout_risks",
        result,
        why,
        {"to": "copilot", "suggestion": suggestion},
    )


THROUGHPUT_COLUMNS = (
    _col("units_picked", "Units picked yesterday", "integer", "units"),
    _col("avg_units_picked", "Average units picked", "number", "units"),
    _col("units_change_pct", "Units change", "number", "%"),
    _col("units_direction", "Units against the average"),
    _col("orders_shipped", "Orders shipped yesterday", "integer"),
    _col("avg_orders_shipped", "Average orders shipped", "number"),
    _col("orders_change_pct", "Orders change", "number", "%"),
    _col("orders_direction", "Orders against the average"),
)


def _throughput(conn: Connection, site: UUID, moment: Moment) -> BriefItem:
    cursor = query(conn, "throughput", "SELECT * FROM sem.throughput(%s, %s)", (site, moment.as_of))
    names = [c.name for c in cursor.description or ()]
    found = [dict(zip(names, r, strict=True)) for r in cursor.fetchall()]
    yesterday = found[0]["yesterday"].isoformat() if found else ""
    rows = [{c.key: r[c.key] for c in THROUGHPUT_COLUMNS} for r in found]
    days = found[0]["baseline_days"] if found else 7
    result = _envelope(
        moment,
        "brief_throughput",
        {"baseline_operating_days": days},
        {"yesterday": yesterday},
        THROUGHPUT_COLUMNS,
        rows,
        "The business calendar has no operating day before today.",
    )
    why = f"Yesterday's picking and shipping against the {days} operating days before it."
    return BriefItem("throughput", "brief_throughput", result, why, None)


FRESHNESS_COLUMNS = (
    _col("stream", "Data"),
    _col("data_as_of", "Last synced", "datetime"),
    _col("minutes_behind", "Minutes behind site time", "integer", "minutes"),
)


def _data_freshness(conn: Connection, site: UUID, moment: Moment) -> BriefItem:
    found = query(
        conn,
        "data_freshness",
        "SELECT stream, data_as_of, minutes_behind FROM sem.data_freshness(%s, %s)",
        (site, moment.as_of),
    ).fetchall()
    # Oldest first: the stream furthest behind decides how far the brief can be trusted.
    ordered = sorted(found, key=lambda r: (r[2] is not None, -(r[2] or 0)))
    rows = [{"stream": STREAM_TEXT.get(s, s), "data_as_of": t, "minutes_behind": m} for s, t, m in ordered]
    result = _envelope(
        moment,
        "brief_data_freshness",
        {},
        {"as_of": moment.local_time},
        FRESHNESS_COLUMNS,
        rows,
        "No data stream is connected.",
    )
    return BriefItem(
        "data_freshness", "brief_data_freshness", result, "How current the data behind this brief is.", None
    )


def brief_facts(conn: Connection, site: UUID) -> BriefFacts:
    """Every fact of today's brief, in one read snapshot. Raises SiteNotReady unless READY."""
    state = query(
        conn,
        "brief_state",
        "SELECT n.data_status, e.evaluation_key, sem.business_date(n.site_id, n.as_of), e.simulation_run_id"
        " FROM sem.site_now n JOIN sem.exception_state e ON e.site_id = n.site_id WHERE n.site_id = %s",
        (site,),
    ).fetchone()
    if state is None or state[0] != "READY":
        raise SiteNotReady(str(site))
    moment = read_moment(conn, site)
    items = [
        _top_exceptions(conn, site, moment),
        _orders_at_risk_today(conn, site, moment),
        _throughput(conn, site, moment),
        _data_freshness(conn, site, moment),
    ]
    return BriefFacts(
        state[2], state[3], moment.as_of, moment.time_zone, moment.settings_version_id, state[1], items
    )
