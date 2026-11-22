"""Bin states for the map (UX spec 5), from sem.bin_states at one site time."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from palletwise.db.connection import Connection, query
from palletwise.semantic.locations import read_moment


@dataclass(frozen=True, slots=True)
class BinState:
    bin_id: UUID
    state: str  # NORMAL, EMPTY, LOW_STOCK or OVER_CAPACITY (ux-screens 5)
    on_hand: int
    picked_7d: int = 0  # units picked from it in the last 7 site days (the activity heatmap)


@dataclass(frozen=True, slots=True)
class MapState:
    as_of: datetime
    time_zone: str
    data_as_of: datetime | None
    bins: list[BinState]
    total_units: int = 0
    stocked_locations: int = 0
    active_zones: int = 0
    open_exceptions: int = 0


def map_state(conn: Connection, site: UUID) -> MapState:
    moment = read_moment(conn, site)
    rows = query(
        conn,
        "bin_states",
        "SELECT bin_id, state, on_hand FROM sem.bin_states(%s, %s, %s)",
        (site, moment.as_of, moment.settings_version_id),
    ).fetchall()
    picked = dict(
        query(
            conn,
            "bin_pick_activity",
            "SELECT bin_id, picked_units FROM sem.bin_pick_activity(%s, %s, 7)",
            (site, moment.as_of),
        ).fetchall()
    )
    summary = query(
        conn,
        "site_summary",
        "SELECT total_units, stocked_locations, active_zones, open_exceptions FROM sem.site_summary(%s, %s)",
        (site, moment.as_of),
    ).fetchone()
    evidence = moment.evidence(("MASTER", "MOVEMENTS"), ())
    bins = [BinState(r[0], r[1], r[2], int(picked.get(r[0], 0))) for r in rows]
    figures = [int(v) for v in summary] if summary else [0, 0, 0, 0]
    return MapState(moment.as_of, moment.time_zone, evidence.data_as_of, bins, *figures)
