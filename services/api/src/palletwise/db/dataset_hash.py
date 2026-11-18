"""Dataset hash over natural keys (simulator spec 5.3, FR-SIM-02).

Generated UUIDv7 identifiers contain random bits and are excluded, as are processing
columns (`recorded_at`, `row_version`). References are expressed through natural keys.
Values are rendered as text by PostgreSQL, so decimals keep their column scale and
timestamps are ISO 8601 UTC with microseconds.
"""

import hashlib
import json
from uuid import UUID

import psycopg

from palletwise.db.connection import tenant_transaction

# Each query returns one JSON object per row, already ordered by natural key.
# Timestamps render as ISO 8601 UTC with microseconds.
_QUERIES: tuple[tuple[str, str], ...] = (
    (
        "zone",
        """
        SELECT json_build_object('code', z.code, 'name', z.name, 'type', z.zone_type,
               'temp', z.temperature_class, 'prime', z.is_prime, 'outline', z.outline::text)
          FROM pw.zone z WHERE z.site_id = %(site)s ORDER BY z.code""",
    ),
    (
        "rack",
        """
        SELECT json_build_object('code', k.code, 'zone', z.code, 'x', k.origin_x::text,
               'y', k.origin_y::text, 'rot', k.rotation_deg::text, 'bays', k.bay_count,
               'levels', k.level_count)
          FROM pw.rack k JOIN pw.zone z ON z.id = k.zone_id
         WHERE k.site_id = %(site)s ORDER BY k.code""",
    ),
    (
        "bin",
        """
        SELECT json_build_object('code', b.code, 'zone', z.code, 'rack', k.code, 'bay', b.bay,
               'level', b.level, 'role', b.bin_role, 'x', b.x::text, 'y', b.y::text,
               'z', b.z::text, 'vol', b.capacity_volume_m3::text,
               'kg', b.capacity_weight_kg::text, 'single', b.single_sku)
          FROM pw.bin b JOIN pw.zone z ON z.id = b.zone_id LEFT JOIN pw.rack k ON k.id = b.rack_id
         WHERE b.site_id = %(site)s ORDER BY b.code""",
    ),
    (
        "category",
        """
        SELECT json_build_object('code', c.code, 'name', c.name)
          FROM pw.category c ORDER BY c.code""",
    ),
    (
        "product",
        """
        SELECT json_build_object('sku', p.sku, 'name', p.name, 'category', c.code,
               'brand', p.brand, 'uom', p.uom, 'vol', p.unit_volume_m3::text,
               'kg', p.unit_weight_kg::text, 'value', p.unit_value::text,
               'temp', p.temperature_class)
          FROM pw.product p JOIN pw.category c ON c.id = p.category_id ORDER BY p.sku""",
    ),
    (
        "customer",
        """
        SELECT json_build_object('code', c.code, 'brand', c.brand, 'district', c.district,
               'dock', c.dock_type, 'open', c.window_open::text, 'close', c.window_close::text)
          FROM pw.customer c ORDER BY c.code""",
    ),
    (
        "replenishment_policy",
        """
        SELECT json_build_object('sku', p.sku, 'bin', b.code, 'min', r.min_qty, 'max', r.max_qty)
          FROM pw.replenishment_policy r JOIN pw.product p ON p.id = r.product_id
          JOIN pw.bin b ON b.id = r.pick_bin_id
         WHERE r.site_id = %(site)s ORDER BY p.sku, b.code""",
    ),
    (
        "outbound_order",
        """
        SELECT json_build_object('order', o.order_no, 'customer', c.code,
               'delivery', o.delivery_date::text, 'deadline',
               to_char(o.dispatch_deadline AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
               'status', o.status, 'shipped',
               to_char(o.shipped_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'))
          FROM pw.outbound_order o JOIN pw.customer c ON c.id = o.customer_id
         WHERE o.site_id = %(site)s ORDER BY o.order_no""",
    ),
    (
        "order_line",
        """
        SELECT json_build_object('line', l.external_id, 'sku', p.sku, 'ordered', l.qty_ordered,
               'allocated', l.qty_allocated, 'picked', l.qty_picked, 'shipped', l.qty_shipped)
          FROM pw.order_line l JOIN pw.product p ON p.id = l.product_id
         WHERE l.site_id = %(site)s ORDER BY l.external_id""",
    ),
    (
        "allocation",
        """
        SELECT json_build_object('id', a.external_id, 'line', l.external_id, 'bin', b.code,
               'qty', a.qty, 'consumed', a.qty_consumed, 'released', a.qty_released,
               'status', a.status, 'created',
               to_char(a.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'))
          FROM pw.allocation a JOIN pw.order_line l ON l.id = a.order_line_id
          JOIN pw.bin b ON b.id = a.bin_id
         WHERE a.site_id = %(site)s ORDER BY a.external_id""",
    ),
    (
        "movement",
        """
        SELECT json_build_object('id', m.external_id, 'type', m.movement_type, 'sku', p.sku,
               'qty', m.qty, 'from', fb.code, 'to', tb.code, 'line', l.external_id,
               'reason', m.reason_code, 'at',
               to_char(m.occurred_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'))
          FROM pw.movement m JOIN pw.product p ON p.id = m.product_id
          LEFT JOIN pw.bin fb ON fb.id = m.from_bin_id LEFT JOIN pw.bin tb ON tb.id = m.to_bin_id
          LEFT JOIN pw.order_line l ON l.id = m.order_line_id
         WHERE m.site_id = %(site)s ORDER BY m.external_id""",
    ),
    (
        "inventory_balance",
        """
        SELECT json_build_object('bin', b.code, 'sku', p.sku, 'on_hand', i.on_hand,
               'allocated', i.allocated)
          FROM pw.inventory_balance i JOIN pw.bin b ON b.id = i.bin_id
          JOIN pw.product p ON p.id = i.product_id
         WHERE i.site_id = %(site)s ORDER BY b.code, p.sku""",
    ),
)


def compute(conn: psycopg.Connection, tenant_id: UUID, site_id: UUID) -> tuple[str, dict[str, int]]:
    """Return the SHA-256 dataset hash and the row count per table."""
    digest = hashlib.sha256()
    counts: dict[str, int] = {}
    with tenant_transaction(conn, tenant_id), conn.cursor() as cur:
        for table, query in _QUERIES:
            cur.execute(query, {"site": site_id})  # pyright: ignore[reportArgumentType]
            n = 0
            for (row,) in cur:
                line = json.dumps(
                    {"t": table, **row}, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                )
                digest.update(line.encode("utf-8"))
                digest.update(b"\n")
                n += 1
            counts[table] = n
    return digest.hexdigest(), counts
