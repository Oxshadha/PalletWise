"""Bin states for the map (UX spec 5), from sem.bin_states at one site time."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from palletwise.db.connection import Connection, query
from palletwise.semantic.locations import read_moment


@dataclass(frozen=True, slots=True)
class BinState:
    bin_id: UUID
    state: str  # NORMAL or EMPTY in Slice 1
    on_hand: int


@dataclass(frozen=True, slots=True)
class MapState:
    as_of: datetime
    time_zone: str
    data_as_of: datetime | None
    bins: list[BinState]


def map_state(conn: Connection, site: UUID) -> MapState:
    moment = read_moment(conn, site)
    rows = query(
        conn, "bin_states", "SELECT bin_id, state, on_hand FROM sem.bin_states(%s, %s)", (site, moment.as_of)
    ).fetchall()
    evidence = moment.evidence(("MASTER", "MOVEMENTS"), ())
    return MapState(moment.as_of, moment.time_zone, evidence.data_as_of, [BinState(*r) for r in rows])
