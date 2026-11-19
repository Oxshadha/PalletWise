"""Product locations and bin contents (semantic spec 4.1 and 8), as result envelopes.

Every query reads sem objects only and runs in the caller's transaction, which the executor
opens as a read-only snapshot bound to the tenant.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from palletwise.contracts.results import (
    DEFAULT_SEVERITY,
    MAX_ENTITIES,
    MAX_HIGHLIGHTS,
    Column,
    EntityRef,
    Evidence,
    Highlight,
    HighlightReason,
    ResultEnvelope,
    ResultStatus,
    StreamFreshness,
)
from palletwise.db.connection import Connection, query
from palletwise.semantic.model import semantic_model, site_time

MAX_ROWS = 200
CANDIDATES = 6
STOCK_STREAMS = ("MASTER", "MOVEMENTS", "ALLOCATIONS")

STOCK_COLUMNS = (
    Column(key="on_hand", label="On hand", type="integer", unit="units"),
    Column(key="allocated", label="Allocated", type="integer", unit="units"),
    Column(key="available", label="Available", type="integer", unit="units"),
)
LOCATION_COLUMNS = (
    Column(key="bin_code", label="Location", type="text"),
    Column(key="bin_role", label="Type", type="text"),
    Column(key="zone_code", label="Zone", type="text"),
    *STOCK_COLUMNS,
    Column(key="storable", label="Storable", type="boolean"),
    Column(key="last_movement_at", label="Last movement", type="datetime"),
)
CONTENT_COLUMNS = (
    Column(key="sku", label="SKU", type="text"),
    Column(key="name", label="Product", type="text"),
    *STOCK_COLUMNS,
    Column(key="last_movement_at", label="Last movement", type="datetime"),
)
# Names are not unique in every catalogue, so candidates also show brand and category.
CANDIDATE_COLUMNS = (
    Column(key="sku", label="SKU", type="text"),
    Column(key="name", label="Product", type="text"),
    Column(key="brand", label="Brand", type="text"),
    Column(key="category", label="Category", type="text"),
)


class SiteNotReady(Exception):
    """The site or its clock does not exist yet; the copilot pre-checks report this."""


@dataclass(frozen=True, slots=True)
class Moment:
    """One point in time for one answer (semantic spec 2: read once, passed to every call)."""

    site: UUID
    as_of: datetime
    time_zone: str
    freshness: tuple[StreamFreshness, ...]
    settings_version_id: UUID  # the version in effect when the request started (semantic spec 2)

    def evidence(self, streams: tuple[str, ...], how_computed: tuple[str, ...]) -> Evidence:
        used = tuple(f for f in self.freshness if f.stream in streams)
        times = [f.data_as_of for f in used]
        oldest = None if not times or any(t is None for t in times) else min(t for t in times if t)
        return Evidence(
            as_of=self.as_of,
            time_zone=self.time_zone,
            settings_version_id=self.settings_version_id,
            data_as_of=oldest,
            freshness=used,
            how_computed=how_computed,
        )

    def missing_streams(self, streams: tuple[str, ...]) -> list[str]:
        known = {f.stream: f.data_as_of for f in self.freshness}
        return [s for s in streams if known.get(s) is None]

    @property
    def local_time(self) -> str:
        return site_time(self.as_of, self.time_zone)


def read_moment(conn: Connection, site: UUID) -> Moment:
    row = query(
        conn, "site_now", "SELECT as_of, time_zone FROM sem.site_now WHERE site_id = %s", (site,)
    ).fetchone()
    if row is None or row[0] is None:
        raise SiteNotReady(str(site))
    streams = query(
        conn,
        "stream_freshness",
        "SELECT stream, data_as_of FROM sem.stream_freshness WHERE site_id = %s ORDER BY stream",
        (site,),
    ).fetchall()
    freshness = tuple(StreamFreshness(stream=s, data_as_of=t) for s, t in streams)
    settings = query(
        conn,
        "current_settings",
        "SELECT settings_version_id FROM sem.current_settings WHERE site_id = %s",
        (site,),
    ).fetchone()
    if settings is None:
        raise SiteNotReady(str(site))  # every site has settings from creation (CWM 9.1)
    return Moment(site, row[0], row[1], freshness, settings[0])


def _envelope(
    moment: Moment, result: str, request: dict[str, Any], explain: dict[str, str], **fields: Any
) -> ResultEnvelope:
    how = semantic_model().explain(result, as_of=moment.local_time, **explain)
    return ResultEnvelope(evidence=moment.evidence(STOCK_STREAMS, how), normalized_request=request, **fields)


def _unavailable(
    moment: Moment, result: str, request: dict[str, Any], explain: dict[str, str]
) -> ResultEnvelope | None:
    missing = moment.missing_streams(STOCK_STREAMS)
    if not missing:
        return None
    return _envelope(moment, result, request, explain, status=ResultStatus.UNAVAILABLE,
                     message="This data has not been loaded yet.")  # fmt: skip


# ---------------------------------------------------------------- find_product_locations


def product_locations(
    conn: Connection, site: UUID, *, sku: str | None = None, product_query: str | None = None
) -> ResultEnvelope:
    """Exactly one of `sku` (exact, case-insensitive) or `product_query` (name or SKU words)."""
    if (sku is None) == (product_query is None):
        raise ValueError("exactly one of sku or product_query")
    moment = read_moment(conn, site)
    request: dict[str, Any] = {"tool": "find_product_locations", "sku": sku, "product_query": product_query}
    explain = {"sku": sku or str(product_query)}
    if (blocked := _unavailable(moment, "find_product_locations", request, explain)) is not None:
        return blocked

    if sku is not None:
        found = query(
            conn,
            "product_by_sku",
            "SELECT product_id, sku, name FROM sem.product WHERE lower(sku) = lower(%s)",
            (sku,),
        ).fetchall()
        if not found:
            return _envelope(moment, "find_product_locations", request, explain,
                             status=ResultStatus.NOT_FOUND, message=f"No such SKU: {sku}.")  # fmt: skip
    else:
        found = _resolve_query(conn, str(product_query))
        if not found:
            return _envelope(
                moment,
                "find_product_locations",
                request,
                explain,
                status=ResultStatus.NOT_FOUND,
                message=f"No such product: {product_query}.",
            )
        if len(found) > 1:
            return _envelope(
                moment, "find_product_locations", request, explain, status=ResultStatus.AMBIGUOUS,
                message=f"{len(found)} products match \"{product_query}\". Which one do you mean?",
                columns=CANDIDATE_COLUMNS,
                rows=tuple({"sku": r[1], "name": r[2], "brand": r[3], "category": r[4]} for r in found),
                entities=tuple(EntityRef(type="product", id=r[0], label=r[1]) for r in found),
            )  # fmt: skip

    product_id, product_sku, product_name = found[0][:3]
    request = {**request, "sku": product_sku, "resolved_product": str(product_id)}
    explain = {"sku": product_sku}
    rows = query(
        conn,
        "product_locations",
        """
        SELECT bin_id, bin_code, bin_role, zone_code, on_hand, allocated, available, storable,
               last_movement_at, zone_id
          FROM sem.product_locations(%s, %s, %s)
        """,
        (site, moment.as_of, product_id),
    ).fetchmany(MAX_ROWS + 1)
    truncated = len(rows) > MAX_ROWS
    rows = rows[:MAX_ROWS]
    product_ref = EntityRef(type="product", id=product_id, label=product_sku)
    if not rows:
        return _envelope(moment, "find_product_locations", request, explain, status=ResultStatus.NO_RESULTS,
                         message=f"{product_sku} ({product_name}) is not stored in any location right now.",
                         entities=(product_ref,))  # fmt: skip
    keys = (
        "bin_code",
        "bin_role",
        "zone_code",
        "on_hand",
        "allocated",
        "available",
        "storable",
        "last_movement_at",
    )
    return _envelope(
        moment, "find_product_locations", request, explain,
        status=ResultStatus.OK,
        columns=LOCATION_COLUMNS,
        rows=tuple(dict(zip(keys, r[1:9], strict=True)) for r in rows),
        truncated=truncated,
        highlights=_bin_highlights(r[0] for r in rows),
        highlights_truncated=truncated,
        entities=(product_ref, *(EntityRef(type="bin", id=r[0], label=r[1]) for r in rows))[:MAX_ENTITIES],
    )  # fmt: skip


def _resolve_query(conn: Connection, words: str) -> list[tuple[Any, ...]]:
    """One product when the best match is unique by rank; otherwise every candidate."""
    found = query(
        conn,
        "search_products",
        "SELECT product_id, sku, name, brand, category_name, rank FROM sem.search_products(%s, %s)",
        (words, CANDIDATES),
    ).fetchall()
    if not found:
        return []
    best = [r for r in found if r[5] == found[0][5]]
    exact = found[0][5] <= 1  # exact SKU or exact name
    if len(found) == 1 or (exact and len(best) == 1):
        return [found[0][:5]]
    return [r[:5] for r in found]


# ---------------------------------------------------------------- get_bin_contents


def bin_contents(conn: Connection, site: UUID, bin_code: str) -> ResultEnvelope:
    moment = read_moment(conn, site)
    request: dict[str, Any] = {"tool": "get_bin_contents", "bin_code": bin_code}
    explain = {"bin_code": bin_code}
    if (blocked := _unavailable(moment, "get_bin_contents", request, explain)) is not None:
        return blocked
    location = query(
        conn,
        "location_by_code",
        "SELECT bin_id, bin_code FROM sem.location WHERE site_id = %s AND lower(bin_code) = lower(%s)",
        (site, bin_code),
    ).fetchone()
    if location is None:
        return _envelope(moment, "get_bin_contents", request, explain,
                         status=ResultStatus.NOT_FOUND, message=f"No such location: {bin_code}.")  # fmt: skip
    bin_id, code = location
    request = {**request, "bin_code": code}
    explain = {"bin_code": code}
    rows = query(
        conn,
        "bin_contents",
        """
        SELECT product_id, sku, name, on_hand, allocated, available, last_movement_at
          FROM sem.bin_contents(%s, %s, %s)
        """,
        (site, moment.as_of, bin_id),
    ).fetchmany(MAX_ROWS + 1)
    truncated = len(rows) > MAX_ROWS
    rows = rows[:MAX_ROWS]
    bin_ref = EntityRef(type="bin", id=bin_id, label=code)
    highlights = _bin_highlights([bin_id])
    if not rows:
        return _envelope(
            moment,
            "get_bin_contents",
            request,
            explain,
            status=ResultStatus.NO_RESULTS,
            message=f"Location {code} is empty.",
            highlights=highlights,
            entities=(bin_ref,),
        )
    keys = ("sku", "name", "on_hand", "allocated", "available", "last_movement_at")
    return _envelope(
        moment, "get_bin_contents", request, explain,
        status=ResultStatus.OK,
        columns=CONTENT_COLUMNS,
        rows=tuple(dict(zip(keys, r[1:], strict=True)) for r in rows),
        truncated=truncated,
        highlights=highlights,
        entities=(bin_ref, *(EntityRef(type="product", id=r[0], label=r[1]) for r in rows))[:MAX_ENTITIES],
    )  # fmt: skip


def _bin_highlights(bin_ids: Any) -> tuple[Highlight, ...]:
    """A result with a bin dimension highlights those bins (semantic spec 7.6)."""
    reason = HighlightReason.LOCATION
    return tuple(
        Highlight(target_type="bin", target_id=b, reason=reason, severity=DEFAULT_SEVERITY[reason])
        for b in list(bin_ids)[:MAX_HIGHLIGHTS]
    )
