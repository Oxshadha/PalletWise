"""Site geometry for the map (CWM 5): read as pw_app, bound to the session's tenant."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from palletwise.db.connection import tenant_transaction


@dataclass(frozen=True, slots=True)
class Layout:
    site: dict[str, Any]
    zones: list[dict[str, Any]]
    racks: list[dict[str, Any]]
    bins: list[dict[str, Any]]
    features: list[dict[str, Any]]


def site_layout(conn: psycopg.Connection, tenant: UUID, site: UUID) -> Layout | None:
    """Active structure only: a deactivated bin disappears from the map without code changes."""
    with tenant_transaction(conn, tenant), conn.cursor(row_factory=dict_row) as cur:
        head = cur.execute(
            "SELECT id, code, name, width_m::float AS width_m, depth_m::float AS depth_m"
            " FROM pw.site WHERE id = %s",
            (site,),
        ).fetchone()
        if head is None:
            return None
        zones = cur.execute(
            "SELECT id, code, name, zone_type, is_prime, outline FROM pw.zone"
            " WHERE site_id = %s AND is_active ORDER BY code",
            (site,),
        ).fetchall()
        racks = cur.execute(
            """
            SELECT id, code, zone_id, origin_x::float AS origin_x, origin_y::float AS origin_y,
                   rotation_deg::float AS rotation_deg, bay_count, level_count,
                   bay_width_m::float AS bay_width_m, depth_m::float AS depth_m,
                   level_height_m::float AS level_height_m
              FROM pw.rack WHERE site_id = %s AND is_active ORDER BY code
            """,
            (site,),
        ).fetchall()
        bins = cur.execute(
            """
            SELECT id, code, zone_id, rack_id, bin_role, bay, level,
                   x::float AS x, y::float AS y, z::float AS z,
                   width_m::float AS width_m, depth_m::float AS depth_m, height_m::float AS height_m
              FROM pw.bin WHERE site_id = %s AND is_active ORDER BY code
            """,
            (site,),
        ).fetchall()
        features = cur.execute(
            "SELECT id, feature_type, label, outline FROM pw.site_feature WHERE site_id = %s ORDER BY label",
            (site,),
        ).fetchall()
    return Layout(head, zones, racks, bins, features)
