"""The operational rule results (semantic spec 6 and 8; Slice 2 step 2), as result envelopes.

Every definition lives in a sem function (db/sql 0010 to 0012); this module only reads it once
per request at one moment and one settings version, and shapes the envelope: rows, highlights
by semantic spec 7.6, entities for the working set, and evidence with "how computed".
"""

from collections.abc import Callable, Iterable, Sequence
from datetime import datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from palletwise.contracts.results import (
    DEFAULT_SEVERITY,
    MAX_ENTITIES,
    MAX_HIGHLIGHTS,
    Column,
    EntityRef,
    Highlight,
    HighlightReason,
    ResultEnvelope,
    ResultStatus,
    Severity,
    exception_severity,
)
from palletwise.db.connection import Connection, query
from palletwise.semantic.locations import MAX_ROWS, Moment, read_moment
from palletwise.semantic.model import semantic_model, site_time

STOCK = ("MASTER", "MOVEMENTS", "ALLOCATIONS")
DEMAND = ("MASTER", "MOVEMENTS", "ORDERS")
STOCK_AND_DEMAND = ("MASTER", "MOVEMENTS", "ALLOCATIONS", "ORDERS")
ORDERS = ("MASTER", "ORDERS")

Row = dict[str, Any]


def _col(key: str, label: str, kind: str = "text", unit: str | None = None) -> Column:
    return Column(key=key, label=label, type=kind, unit=unit)  # type: ignore[arg-type]


SKU, NAME = _col("sku", "SKU"), _col("name", "Product")
UNITS = "units"


# ---------------------------------------------------------------- shared shaping


def _fetch(conn: Connection, name: str, sql: str, args: Sequence[Any]) -> tuple[list[Row], bool]:
    """Rows of one sem function as dicts, at most MAX_ROWS, and whether more existed."""
    cursor = query(conn, name, sql, args)
    names = [c.name for c in cursor.description or ()]
    rows = [dict(zip(names, r, strict=True)) for r in cursor.fetchmany(MAX_ROWS + 1)]
    return rows[:MAX_ROWS], len(rows) > MAX_ROWS


def _envelope(
    moment: Moment,
    tool: str,
    request: dict[str, Any],
    streams: tuple[str, ...],
    explain: dict[str, str] | None = None,
    **fields: Any,
) -> ResultEnvelope:
    how = semantic_model().explain(tool, as_of=moment.local_time, **(explain or {}))
    return ResultEnvelope(
        evidence=moment.evidence(streams, how), normalized_request={"tool": tool, **request}, **fields
    )


def _unavailable(
    moment: Moment,
    tool: str,
    request: dict[str, Any],
    streams: tuple[str, ...],
    explain: dict[str, str] | None,
) -> ResultEnvelope | None:
    if not moment.missing_streams(streams):
        return None
    return _envelope(
        moment,
        tool,
        request,
        streams,
        explain,
        status=ResultStatus.UNAVAILABLE,
        message="This data has not been loaded yet.",
    )


def distinct_highlights(
    targets: Iterable[tuple[str, UUID, Severity]], reason: HighlightReason
) -> tuple[tuple[Highlight, ...], bool]:
    """Distinct targets in order, at most MAX_HIGHLIGHTS, and whether more existed."""
    seen: dict[tuple[str, UUID], Highlight] = {}
    for kind, target, severity in targets:
        key = (kind, target)
        if key not in seen:
            seen[key] = Highlight(target_type=kind, target_id=target, reason=reason, severity=severity)  # type: ignore[arg-type]
    found = list(seen.values())
    return tuple(found[:MAX_HIGHLIGHTS]), len(found) > MAX_HIGHLIGHTS


def product_bins(
    conn: Connection, site: UUID, products: Sequence[UUID], reason: HighlightReason, severity: Severity
) -> tuple[tuple[Highlight, ...], bool]:
    """A product result without a location dimension highlights the storable bins holding
    those products, by on hand descending then bin code, at most 200 (semantic spec 7.6)."""
    if not products:
        return (), False
    rows = query(
        conn,
        "product_bins",
        """
        SELECT l.bin_id FROM sem.stock s JOIN sem.location l ON l.bin_id = s.bin_id
         WHERE s.site_id = %s AND l.storable AND s.on_hand > 0 AND s.product_id = ANY(%s)
         ORDER BY s.on_hand DESC, l.bin_code LIMIT %s
        """,
        (site, list(products), MAX_HIGHLIGHTS + 1),
    ).fetchall()
    return distinct_highlights((("bin", r[0], severity) for r in rows), reason)


def distinct_entities(*groups: Iterable[EntityRef]) -> tuple[EntityRef, ...]:
    seen: dict[tuple[str, UUID], EntityRef] = {}
    for group in groups:
        for e in group:
            seen.setdefault((e.type, e.id), e)
    return tuple(seen.values())[:MAX_ENTITIES]


def _products(rows: Iterable[Row]) -> list[EntityRef]:
    return [EntityRef(type="product", id=r["product_id"], label=r["sku"]) for r in rows]


def _rows(rows: Iterable[Row], keys: Sequence[str]) -> tuple[Row, ...]:
    return tuple({k: r[k] for k in keys} for r in rows)


def _result(
    moment: Moment,
    tool: str,
    request: dict[str, Any],
    streams: tuple[str, ...],
    explain: dict[str, str] | None,
    columns: tuple[Column, ...],
    rows: list[Row],
    truncated: bool,
    empty: str,
    highlights: tuple[tuple[Highlight, ...], bool],
    entities: tuple[EntityRef, ...],
) -> ResultEnvelope:
    if not rows:
        return _envelope(
            moment, tool, request, streams, explain, status=ResultStatus.NO_RESULTS, message=empty
        )
    return _envelope(
        moment,
        tool,
        request,
        streams,
        explain,
        status=ResultStatus.OK,
        columns=columns,
        rows=_rows(rows, [c.key for c in columns]),
        truncated=truncated,
        highlights=highlights[0],
        highlights_truncated=highlights[1],
        entities=entities,
    )


def _run(
    conn: Connection,
    site: UUID,
    tool: str,
    request: dict[str, Any],
    streams: tuple[str, ...],
    explain: Callable[[Moment], dict[str, str]] | None,
    shape: Callable[[Moment, dict[str, str] | None], ResultEnvelope],
) -> ResultEnvelope:
    moment = read_moment(conn, site)
    values = explain(moment) if explain else None
    blocked = _unavailable(moment, tool, request, streams, values)
    return blocked if blocked is not None else shape(moment, values)


def _setting(conn: Connection, moment: Moment, key: str) -> str:
    row = query(conn, "setting", "SELECT sem.setting(%s, %s)", (moment.settings_version_id, key)).fetchone()
    assert row is not None
    return f"{row[0]:g}"


# ---------------------------------------------------------------- LOW_STOCK


LOW_STOCK_COLUMNS = (
    SKU,
    NAME,
    _col("bin_code", "Pick location"),
    _col("zone_code", "Zone"),
    _col("available", "Available", "integer", UNITS),
    _col("min_qty", "Minimum", "integer", UNITS),
    _col("max_qty", "Maximum", "integer", UNITS),
    _col("on_hand", "On hand", "integer", UNITS),
)


def low_stock(conn: Connection, site: UUID) -> ResultEnvelope:
    def shape(moment: Moment, explain: dict[str, str] | None) -> ResultEnvelope:
        rows, truncated = _fetch(
            conn,
            "low_stock",
            "SELECT * FROM sem.low_stock(%s, %s, %s)",
            (site, moment.as_of, moment.settings_version_id),
        )
        reason = HighlightReason.LOW_STOCK
        bins = distinct_highlights((("bin", r["bin_id"], DEFAULT_SEVERITY[reason]) for r in rows), reason)
        located = [EntityRef(type="bin", id=r["bin_id"], label=r["bin_code"]) for r in rows]
        return _result(
            moment,
            "get_low_stock",
            {},
            STOCK,
            explain,
            LOW_STOCK_COLUMNS,
            rows,
            truncated,
            "No product is at or below its minimum in its pick location right now.",
            bins,
            distinct_entities(_products(rows), located),
        )

    return _run(conn, site, "get_low_stock", {}, STOCK, None, shape)


# ---------------------------------------------------------------- top movers


TOP_MOVER_COLUMNS = (
    SKU,
    NAME,
    _col("category_name", "Category"),
    _col("demand_units", "Units picked", "integer", UNITS),
)


def top_movers(conn: Connection, site: UUID, days: int, limit: int) -> ResultEnvelope:
    request = {"days": days, "limit": limit}

    def shape(moment: Moment, explain: dict[str, str] | None) -> ResultEnvelope:
        rows, truncated = _fetch(
            conn,
            "top_movers",
            "SELECT * FROM sem.top_movers(%s, %s, %s, %s, %s)",
            (site, moment.as_of, moment.settings_version_id, days, limit),
        )
        reason = HighlightReason.FAST_MOVER
        bins = product_bins(conn, site, [r["product_id"] for r in rows], reason, DEFAULT_SEVERITY[reason])
        return _result(
            moment,
            "get_top_movers",
            request,
            DEMAND,
            explain,
            TOP_MOVER_COLUMNS,
            rows,
            truncated,
            f"No product was picked in the last {days} days.",
            bins,
            distinct_entities(_products(rows)),
        )

    return _run(
        conn,
        site,
        "get_top_movers",
        request,
        DEMAND,
        lambda m: {"days": str(days), "limit": str(limit)},
        shape,
    )


# ---------------------------------------------------------------- DEAD_STOCK


DEAD_STOCK_COLUMNS = (
    SKU,
    NAME,
    _col("category_name", "Category"),
    _col("storable_on_hand", "On hand", "integer", UNITS),
    _col("stock_value", "Stock value", "number", "LKR"),
    _col("days_since_last_pick", "Days since last pick", "integer", "days"),
    _col("last_pick_at", "Last pick", "datetime"),
)


def dead_stock(conn: Connection, site: UUID) -> ResultEnvelope:
    def shape(moment: Moment, explain: dict[str, str] | None) -> ResultEnvelope:
        rows, truncated = _fetch(
            conn,
            "dead_stock",
            "SELECT * FROM sem.dead_stock(%s, %s, %s)",
            (site, moment.as_of, moment.settings_version_id),
        )
        reason = HighlightReason.DEAD_STOCK
        bins = product_bins(conn, site, [r["product_id"] for r in rows], reason, DEFAULT_SEVERITY[reason])
        period = (explain or {}).get("period", "")
        return _result(
            moment,
            "get_dead_stock",
            {},
            STOCK_AND_DEMAND,
            explain,
            DEAD_STOCK_COLUMNS,
            rows,
            truncated,
            f"No stocked product has gone without a pick for more than {period} days.",
            bins,
            distinct_entities(_products(rows)),
        )

    return _run(
        conn,
        site,
        "get_dead_stock",
        {},
        STOCK_AND_DEMAND,
        lambda m: {"period": _setting(conn, m, "dead_stock_days")},
        shape,
    )


# ---------------------------------------------------------------- SLOW_IN_PRIME


SLOW_IN_PRIME_COLUMNS = (
    SKU,
    NAME,
    _col("bin_code", "Location"),
    _col("zone_code", "Zone"),
    _col("on_hand", "On hand", "integer", UNITS),
    _col("days_since_last_pick", "Days since last pick", "integer", "days"),
)


def slow_in_prime(conn: Connection, site: UUID) -> ResultEnvelope:
    def shape(moment: Moment, explain: dict[str, str] | None) -> ResultEnvelope:
        rows, truncated = _fetch(
            conn,
            "slow_in_prime",
            "SELECT * FROM sem.slow_in_prime(%s, %s, %s)",
            (site, moment.as_of, moment.settings_version_id),
        )
        reason = HighlightReason.SLOW_IN_PRIME
        bins = distinct_highlights((("bin", r["bin_id"], DEFAULT_SEVERITY[reason]) for r in rows), reason)
        located = [EntityRef(type="bin", id=r["bin_id"], label=r["bin_code"]) for r in rows]
        period = (explain or {}).get("period", "")
        return _result(
            moment,
            "get_slow_in_prime",
            {},
            STOCK_AND_DEMAND,
            explain,
            SLOW_IN_PRIME_COLUMNS,
            rows,
            truncated,
            f"No product in a fast-pick zone has gone more than {period} days without a pick.",
            bins,
            distinct_entities(_products(rows), located),
        )

    return _run(
        conn,
        site,
        "get_slow_in_prime",
        {},
        STOCK_AND_DEMAND,
        lambda m: {"period": _setting(conn, m, "slow_in_prime_days")},
        shape,
    )


# ---------------------------------------------------------------- zones and capacity


ZONE_COLUMNS = (
    _col("zone_code", "Zone"),
    _col("zone_name", "Name"),
    _col("on_hand", "On hand", "integer", UNITS),
    _col("stock_value", "Stock value", "number", "LKR"),
    _col("products", "Products", "integer"),
    _col("utilization_pct", "Utilization", "number", "%"),
)


def _zones(rows: Iterable[Row]) -> list[EntityRef]:
    return [EntityRef(type="zone", id=r["zone_id"], label=r["zone_code"]) for r in rows]


ZONE_HIGHLIGHTS = 3


def zone_summary(conn: Connection, site: UUID) -> ResultEnvelope:
    def shape(moment: Moment, explain: dict[str, str] | None) -> ResultEnvelope:
        rows, truncated = _fetch(
            conn,
            "zone_summary",
            "SELECT * FROM sem.zone_summary(%s, %s, %s)",
            (site, moment.as_of, moment.settings_version_id),
        )
        reason = HighlightReason.QUERY
        # The zones holding the most stock, not every zone: a highlight points at the answer.
        top = rows[:ZONE_HIGHLIGHTS]
        zones = distinct_highlights((("zone", r["zone_id"], DEFAULT_SEVERITY[reason]) for r in top), reason)
        return _result(
            moment,
            "get_zone_summary",
            {},
            STOCK,
            explain,
            ZONE_COLUMNS,
            rows,
            truncated,
            "This site has no active zones.",
            zones,
            distinct_entities(_zones(rows)),
        )

    return _run(conn, site, "get_zone_summary", {}, STOCK, None, shape)


HOTSPOT_COLUMNS = (
    _col("zone_code", "Zone"),
    _col("zone_name", "Name"),
    _col("utilization_pct", "Utilization", "number", "%"),
    _col("over_capacity_bins", "Locations over capacity", "integer"),
)


def capacity_hotspots(conn: Connection, site: UUID) -> ResultEnvelope:
    def shape(moment: Moment, explain: dict[str, str] | None) -> ResultEnvelope:
        args = (site, moment.as_of, moment.settings_version_id)
        rows, truncated = _fetch(
            conn, "capacity_hotspots", "SELECT * FROM sem.capacity_hotspots(%s, %s, %s)", args
        )
        over = query(
            conn,
            "over_capacity_bins",
            "SELECT bin_id, bin_code, zone_id FROM sem.bin_utilization(%s, %s, %s) WHERE over_capacity"
            " ORDER BY utilization_pct DESC, bin_code",
            args,
        ).fetchall()
        hot = {r["zone_id"] for r in rows}
        # One severity per exception (agent spec 10), as the inbox shows it: a zone with a bin
        # over capacity is critical even when its utilization is low.
        targets = [("bin", b[0], Severity.CRITICAL) for b in over if b[2] in hot]
        targets += [
            ("zone", r["zone_id"], exception_severity("CAPACITY_HOTSPOT", r["over_capacity_bins"] > 0))
            for r in rows
        ]
        located = [EntityRef(type="bin", id=b[0], label=b[1]) for b in over if b[2] in hot]
        return _result(
            moment,
            "get_capacity_hotspots",
            {},
            STOCK,
            explain,
            HOTSPOT_COLUMNS,
            rows,
            truncated,
            f"No zone is at or above {(explain or {}).get('threshold', '')} percent and no location"
            " is over capacity.",
            distinct_highlights(targets, HighlightReason.CAPACITY),
            distinct_entities(_zones(rows), located),
        )

    return _run(
        conn,
        site,
        "get_capacity_hotspots",
        {},
        STOCK,
        lambda m: {"threshold": _setting(conn, m, "capacity_threshold_pct")},
        shape,
    )


# ---------------------------------------------------------------- orders


DELAYED_COLUMNS = (
    _col("order_no", "Order"),
    _col("customer_code", "Outlet"),
    _col("status", "Status"),
    _col("dispatch_deadline", "Dispatch deadline", "datetime"),
    _col("hours_late", "Hours late", "number", "hours"),
    _col("lines", "Lines", "integer"),
)


def _orders(rows: Iterable[Row]) -> list[EntityRef]:
    return [EntityRef(type="order", id=r["order_id"], label=r["order_no"]) for r in rows]


def delayed_orders(conn: Connection, site: UUID, since_days: int) -> ResultEnvelope:
    request = {"since_days": since_days}

    def start(moment: Moment) -> datetime:
        """Start of today in site time, minus since_days whole days."""
        zone = ZoneInfo(moment.time_zone)
        return datetime.combine(moment.as_of.astimezone(zone).date(), time.min, zone) - timedelta(
            days=since_days
        )

    def shape(moment: Moment, explain: dict[str, str] | None) -> ResultEnvelope:
        rows, truncated = _fetch(
            conn,
            "delayed_orders",
            "SELECT * FROM sem.delayed_orders(%s, %s, %s, %s)",
            (site, moment.as_of, moment.settings_version_id, start(moment)),
        )
        return _result(
            moment,
            "get_delayed_orders",
            request,
            ORDERS,
            explain,
            DELAYED_COLUMNS,
            rows,
            truncated,
            "No order is past its dispatch deadline, and none shipped late in this period.",
            ((), False),
            distinct_entities(_orders(rows)),
        )

    return _run(
        conn,
        site,
        "get_delayed_orders",
        request,
        ORDERS,
        lambda m: {"since": site_time(start(m), m.time_zone)},
        shape,
    )


RISK_PRODUCT_COLUMNS = (
    SKU,
    NAME,
    _col("demand_due", "Due", "integer", UNITS),
    _col("pick_face_available", "Pick face", "integer", UNITS),
    _col("shortfall", "Short", "integer", UNITS),
    _col("reserve_available", "In reserve", "integer", UNITS),
    _col("fixable", "Replenishment fixes it", "boolean"),
    _col("true_shortage", "Still short", "integer", UNITS),
    _col("earliest_deadline", "Earliest deadline", "datetime"),
)
RISK_ORDER_COLUMNS = (
    _col("order_no", "Order"),
    _col("customer_code", "Outlet"),
    _col("dispatch_deadline", "Dispatch deadline", "datetime"),
    _col("hours_until_due", "Hours until due", "number", "hours"),
    _col("products_at_risk", "Products at risk", "integer"),
    _col("any_true_shortage", "Any true shortage", "boolean"),
)
REPLENISH_COLUMNS = (
    SKU,
    NAME,
    _col("shortfall", "Short", "integer", UNITS),
    _col("reserve_available", "In reserve", "integer", UNITS),
    _col("pick_face_available", "Pick face", "integer", UNITS),
    _col("first_deadline", "First dispatch", "datetime"),
)


def _pick_bins(conn: Connection, site: UUID, rows: list[Row]) -> tuple[tuple[Highlight, ...], bool]:
    """The pick locations of the products at risk; critical for a true shortage."""
    critical = [r["product_id"] for r in rows if not r.get("fixable", True)]
    warning = [r["product_id"] for r in rows if r.get("fixable", True)]
    found = query(
        conn,
        "risk_pick_bins",
        """
        SELECT l.bin_id, s.product_id FROM sem.stock s JOIN sem.location l ON l.bin_id = s.bin_id
         WHERE s.site_id = %s AND l.bin_role = 'PICK' AND l.is_active AND s.product_id = ANY(%s)
         UNION
        SELECT rp.pick_bin_id, rp.product_id FROM sem.replenishment_policy rp
         WHERE rp.site_id = %s AND rp.product_id = ANY(%s)
        """,
        (site, critical + warning, site, critical + warning),
    ).fetchall()
    by_product: dict[UUID, list[UUID]] = {}
    for bin_id, product in found:
        by_product.setdefault(product, []).append(bin_id)
    targets = [("bin", b, Severity.CRITICAL) for p in critical for b in sorted(by_product.get(p, []))]
    targets += [("bin", b, Severity.WARNING) for p in warning for b in sorted(by_product.get(p, []))]
    return distinct_highlights(targets, HighlightReason.STOCKOUT_RISK)


def order_stockout_risks(conn: Connection, site: UUID, view: str, hours: int | None) -> ResultEnvelope:
    request = {"view": view, "hours": hours}

    def shape(moment: Moment, explain: dict[str, str] | None) -> ResultEnvelope:
        args: tuple[Any, ...] = (site, moment.as_of, moment.settings_version_id)
        risks, _ = _fetch(
            conn, "stockout_risk", "SELECT * FROM sem.stockout_risk(%s, %s, %s, %s)", (*args, hours)
        )
        if view == "orders":
            rows, truncated = _fetch(
                conn, "orders_at_risk", "SELECT * FROM sem.orders_at_risk(%s, %s, %s, %s)", (*args, hours)
            )
            columns, entities, base = (
                RISK_ORDER_COLUMNS,
                distinct_entities(_orders(rows), _products(risks)),
                risks,
            )
            empty = "No open order due in this window is at risk of a stockout."
        elif view == "replenish_before_first_dispatch":
            rows, truncated = _fetch(
                conn,
                "replenish_before_dispatch",
                "SELECT * FROM sem.replenish_before_dispatch(%s, %s, %s)",
                args,
            )
            columns, entities, base = REPLENISH_COLUMNS, distinct_entities(_products(rows)), rows
            empty = "Nothing needs replenishing before today's next dispatch deadline."
        else:
            rows, truncated = risks, len(risks) >= MAX_ROWS
            columns, entities, base = RISK_PRODUCT_COLUMNS, distinct_entities(_products(rows)), rows
            empty = "No product is short for the open orders due in this window."
        return _result(
            moment,
            "get_order_stockout_risks",
            request,
            STOCK_AND_DEMAND,
            explain,
            columns,
            rows,
            truncated,
            empty,
            _pick_bins(conn, site, base),
            entities,
        )

    def explain(moment: Moment) -> dict[str, str]:
        horizon = str(hours) if hours is not None else _setting(conn, moment, "stockout_horizon_hours")
        return {"hours": horizon}

    return _run(conn, site, "get_order_stockout_risks", request, STOCK_AND_DEMAND, explain, shape)
