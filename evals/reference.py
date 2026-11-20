"""Reference queries for gold highlights (evals spec 3).

Plain SQL over the canonical tables, written independently of the tools and the semantic layer,
so a tool bug cannot also corrupt its own gold answer. They run as `pw_eval_reader`, a test-only
role that exists only in the evaluation database: SELECT on the canonical tables, no writes, no
BYPASSRLS, always inside a tenant context and filtered by site.

Gold sets are location codes (stable across tenants), not ids: bins by code, zones as "zone:<code>".
"""

from collections.abc import Callable
from datetime import timedelta
from uuid import UUID

import psycopg
from psycopg import sql

EVAL_ROLE = "pw_eval_reader"
GOLDEN_TABLES = (
    "product",
    "bin",
    "inventory_balance",
    # Slice 2 rules (semantic spec 4 and 6)
    "zone",
    "site",
    "site_clock",
    "settings_version",
    "replenishment_policy",
    "movement",
    "outbound_order",
    "order_line",
)
MAX_HIGHLIGHTS = 200  # semantic spec 7.6


def create_eval_role(superuser: psycopg.Connection, password: str) -> None:
    """Creates the test-only reader in an evaluation database. Never part of a deployment."""
    exists = superuser.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (EVAL_ROLE,)).fetchone()
    if not exists:
        superuser.execute(
            sql.SQL("CREATE ROLE {} LOGIN NOBYPASSRLS PASSWORD {}").format(
                sql.Identifier(EVAL_ROLE), sql.Literal(password)
            )
        )
    # The database revokes CONNECT from PUBLIC (bootstrap hardening), so the role gets it explicitly.
    database = superuser.execute("SELECT current_database()").fetchone()
    assert database is not None
    superuser.execute(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            sql.Identifier(database[0]), sql.Identifier(EVAL_ROLE)
        )
    )
    superuser.execute(f"GRANT USAGE ON SCHEMA pw TO {EVAL_ROLE}")
    for table in GOLDEN_TABLES:
        superuser.execute(f"GRANT SELECT ON pw.{table} TO {EVAL_ROLE}")
    superuser.execute(f"GRANT EXECUTE ON FUNCTION pw.current_tenant() TO {EVAL_ROLE}")


def _product_locations(conn: psycopg.Connection, site: UUID, sku: str) -> list[str]:
    """Every location holding the product (stock on hand or allocated), as semantic spec 7.6."""
    rows = conn.execute(
        """
        SELECT b.code
          FROM pw.inventory_balance ib
          JOIN pw.product p ON p.id = ib.product_id AND p.tenant_id = ib.tenant_id
          JOIN pw.bin b ON b.id = ib.bin_id AND b.tenant_id = ib.tenant_id
         WHERE ib.site_id = %s AND lower(p.sku) = lower(%s) AND (ib.on_hand > 0 OR ib.allocated > 0)
         ORDER BY b.code
        """,
        (site, sku),
    ).fetchall()
    return [r[0] for r in rows]


def _bin(conn: psycopg.Connection, site: UUID, code: str) -> list[str]:
    """The one location itself, when it exists and is active."""
    row = conn.execute(
        "SELECT code FROM pw.bin WHERE site_id = %s AND lower(code) = lower(%s) AND is_active", (site, code)
    ).fetchone()
    return [row[0]] if row else []


# ---------------------------------------------------------------- Slice 2 rules
#
# Each query restates the rule from semantic spec 4 and 6 over the canonical tables. Zones are
# written "zone:<code>" so they never collide with a bin code. The site's moment is its clock, and
# thresholds come from its current settings version, as the tools read them.

STORABLE = "b.is_active AND b.bin_role IN ('PICK', 'RESERVE', 'BULK')"

# Demand events (spec 4.2): a PICK adds, a PICK_REVERSAL transfer subtracts, each only when its
# order is not cancelled.
DEMAND = """
    SELECT m.product_id, m.occurred_at,
           CASE WHEN m.movement_type = 'PICK' THEN m.qty ELSE -m.qty END AS qty
      FROM pw.movement m
      LEFT JOIN pw.order_line ol ON ol.id = m.order_line_id
      LEFT JOIN pw.outbound_order o ON o.id = ol.order_id
     WHERE m.site_id = %(site)s AND m.occurred_at <= %(as_of)s
       AND (m.movement_type = 'PICK' OR (m.movement_type = 'TRANSFER' AND m.reason_code = 'PICK_REVERSAL'))
       AND o.status IS DISTINCT FROM 'CANCELLED'
"""

# Whole days since the latest pick of an order that is not cancelled; reversals ignored (4.2).
LAST_PICK = """
    SELECT m.product_id,
           floor(extract(epoch FROM %(as_of)s - max(m.occurred_at)) / 86400)::integer AS days
      FROM pw.movement m
      LEFT JOIN pw.order_line ol ON ol.id = m.order_line_id
      LEFT JOIN pw.outbound_order o ON o.id = ol.order_id
     WHERE m.site_id = %(site)s AND m.movement_type = 'PICK' AND m.occurred_at <= %(as_of)s
       AND o.status IS DISTINCT FROM 'CANCELLED'
     GROUP BY m.product_id
"""


def _moment(conn: psycopg.Connection, site: UUID) -> dict[str, object]:
    row = conn.execute(
        """
        SELECT c.sim_time, s.time_zone, v."values"
          FROM pw.site s JOIN pw.site_clock c ON c.site_id = s.id
          JOIN LATERAL (SELECT "values" FROM pw.settings_version WHERE site_id = s.id
                         ORDER BY version_no DESC LIMIT 1) v ON true
         WHERE s.id = %s
        """,
        (site,),
    ).fetchone()
    assert row is not None, "the site has no clock or settings"
    return {"site": site, "as_of": row[0], "tz": row[1], "settings": row[2]}


def _codes(conn: psycopg.Connection, sql_text: str, args: dict[str, object]) -> list[str]:
    return [r[0] for r in conn.execute(sql_text, args).fetchall()]  # type: ignore[arg-type]


def _product_bins(conn: psycopg.Connection, m: dict[str, object], products: str) -> list[str]:
    """Spec 7.6: the storable bins holding the products, by on hand descending then code, at most
    200. `products` is a query returning product ids."""
    return _codes(
        conn,
        f"""
        SELECT b.code FROM pw.inventory_balance ib JOIN pw.bin b ON b.id = ib.bin_id
         WHERE ib.site_id = %(site)s AND ib.on_hand > 0 AND {STORABLE}
           AND ib.product_id IN ({products})
         GROUP BY b.code ORDER BY max(ib.on_hand) DESC, b.code LIMIT {MAX_HIGHLIGHTS}
        """,  # noqa: S608 - fixed fragments of this module; every value is bound
        m,
    )


def _low_stock(conn: psycopg.Connection, site: UUID, _: str) -> list[str]:
    """LOW_STOCK: a policy whose pick bin has available (on hand minus allocated) <= min_qty."""
    return _codes(
        conn,
        f"""
        SELECT b.code FROM pw.replenishment_policy rp
          JOIN pw.bin b ON b.id = rp.pick_bin_id
          LEFT JOIN pw.inventory_balance ib ON ib.bin_id = rp.pick_bin_id AND ib.product_id = rp.product_id
         WHERE rp.site_id = %(site)s AND coalesce(ib.on_hand - ib.allocated, 0) <= rp.min_qty
         GROUP BY b.code ORDER BY min(coalesce(ib.on_hand - ib.allocated, 0) - rp.min_qty), b.code
         LIMIT {MAX_HIGHLIGHTS}
        """,  # noqa: S608 - fixed fragments of this module; every value is bound
        _moment(conn, site),
    )


def _top_movers(conn: psycopg.Connection, site: UUID, argument: str) -> list[str]:
    """Top N products by demand over the last D calendar days ("N" or "N/D"; D defaults to 30)."""
    limit, _, days = argument.partition("/")
    m = {**_moment(conn, site), "limit": int(limit), "days": int(days or 30)}
    top = f"""
        SELECT d.product_id FROM ({DEMAND}) d JOIN pw.product p ON p.id = d.product_id
         WHERE d.occurred_at > %(as_of)s - make_interval(days => %(days)s)
         GROUP BY d.product_id, p.sku HAVING sum(d.qty) > 0
         ORDER BY sum(d.qty) DESC, p.sku LIMIT %(limit)s
    """  # noqa: S608 - fixed fragments of this module; every value is bound
    return _product_bins(conn, m, top)


def _dead_stock(conn: psycopg.Connection, site: UUID, _: str) -> list[str]:
    """DEAD_STOCK: storable stock, not picked for more than D days (or never), and first received
    more than D days ago (a product with no receipt in the data is not new)."""
    m = _moment(conn, site)
    m["d"] = int(m["settings"]["dead_stock_days"])  # type: ignore[index]
    dead = f"""
        SELECT ib.product_id FROM pw.inventory_balance ib JOIN pw.bin b ON b.id = ib.bin_id
          LEFT JOIN ({LAST_PICK}) lp ON lp.product_id = ib.product_id
          LEFT JOIN (SELECT product_id, min(occurred_at) AS first_at FROM pw.movement
                      WHERE site_id = %(site)s AND movement_type = 'RECEIPT' AND occurred_at <= %(as_of)s
                      GROUP BY product_id) r ON r.product_id = ib.product_id
         WHERE ib.site_id = %(site)s AND {STORABLE}
           AND (lp.days IS NULL OR lp.days > %(d)s)
           AND (r.first_at IS NULL OR r.first_at < %(as_of)s - make_interval(days => %(d)s))
         GROUP BY ib.product_id HAVING sum(ib.on_hand) > 0
    """  # noqa: S608 - fixed fragments of this module; every value is bound
    return _product_bins(conn, m, dead)


def _slow_in_prime(conn: psycopg.Connection, site: UUID, _: str) -> list[str]:
    """SLOW_IN_PRIME: the prime bins holding stock of a product not picked for more than P days."""
    m = _moment(conn, site)
    m["p"] = int(m["settings"]["slow_in_prime_days"])  # type: ignore[index]
    return _codes(
        conn,
        f"""
        SELECT DISTINCT b.code FROM pw.inventory_balance ib
          JOIN pw.bin b ON b.id = ib.bin_id JOIN pw.zone z ON z.id = b.zone_id
          LEFT JOIN ({LAST_PICK}) lp ON lp.product_id = ib.product_id
         WHERE ib.site_id = %(site)s AND ib.on_hand > 0 AND b.is_active AND z.is_prime AND z.is_active
           AND (lp.days IS NULL OR lp.days > %(p)s)
        """,  # noqa: S608 - fixed fragments of this module; every value is bound
        m,
    )


# Utilization (spec 4.4) per active bin, from the volume and weight its stock uses.
BIN_USE = """
    SELECT b.id, b.code, b.zone_id, b.capacity_volume_m3 AS cap_v, b.capacity_weight_kg AS cap_w,
           coalesce(sum(ib.on_hand * p.unit_volume_m3), 0) AS vol,
           coalesce(sum(ib.on_hand * p.unit_weight_kg), 0) AS wt
      FROM pw.bin b
      LEFT JOIN pw.inventory_balance ib ON ib.bin_id = b.id
      LEFT JOIN pw.product p ON p.id = ib.product_id
     WHERE b.site_id = %(site)s AND b.is_active
     GROUP BY b.id
"""


def _zone_summary(conn: psycopg.Connection, site: UUID, _: str) -> list[str]:
    """The three zones holding the most stock (every balance in the zone), ties by code."""
    return _codes(
        conn,
        """
        SELECT 'zone:' || z.code FROM pw.zone z
          LEFT JOIN pw.bin b ON b.zone_id = z.id
          LEFT JOIN pw.inventory_balance ib ON ib.bin_id = b.id
         WHERE z.site_id = %(site)s
         GROUP BY z.code ORDER BY coalesce(sum(ib.on_hand), 0) DESC, z.code LIMIT 3
        """,
        _moment(conn, site),
    )


def _capacity_hotspots(conn: psycopg.Connection, site: UUID, _: str) -> list[str]:
    """CAPACITY_HOTSPOT: zones at or over T percent, or holding a bin over 100 percent; the
    over-capacity bins of those zones and the zones themselves."""
    m = _moment(conn, site)
    m["t"] = float(m["settings"]["capacity_threshold_pct"])  # type: ignore[index]
    return _codes(
        conn,
        f"""
        WITH use AS ({BIN_USE}),
        hot AS (
          SELECT u.zone_id FROM use u GROUP BY u.zone_id
          HAVING 100 * greatest(sum(u.vol) / sum(u.cap_v), sum(u.wt) / sum(u.cap_w)) >= %(t)s
              OR bool_or(100 * greatest(u.vol / u.cap_v, u.wt / u.cap_w) > 100)
        )
        SELECT u.code FROM use u JOIN hot h ON h.zone_id = u.zone_id
         WHERE 100 * greatest(u.vol / u.cap_v, u.wt / u.cap_w) > 100
        UNION
        SELECT 'zone:' || z.code FROM pw.zone z JOIN hot h ON h.zone_id = z.id
        """,  # noqa: S608 - fixed fragments of this module; every value is bound
        m,
    )


def _stockout(conn: psycopg.Connection, site: UUID, view: str) -> list[str]:
    """STOCKOUT_RISK with H hours (spec 6): open demand due within H (overdue included) above the
    pick face. Highlights are the pick locations of the products at risk (active pick bins holding
    them, and their policy pick bins). Before the first dispatch window, H runs to today's earliest
    open deadline still ahead, and only fixable products count."""
    m = _moment(conn, site)
    if view == "replenish_before_first_dispatch":
        first = conn.execute(
            """
            SELECT min(o.dispatch_deadline) FROM pw.outbound_order o
             WHERE o.site_id = %(site)s AND o.status NOT IN ('SHIPPED', 'CANCELLED')
               AND o.dispatch_deadline > %(as_of)s
               AND (o.dispatch_deadline AT TIME ZONE %(tz)s)::date = (%(as_of)s AT TIME ZONE %(tz)s)::date
               AND EXISTS (SELECT 1 FROM pw.order_line ol WHERE ol.order_id = o.id)
            """,
            m,
        ).fetchone()
        if first is None or first[0] is None:
            return []
        m["until"], m["fixable_only"] = first[0], True
    else:
        m["until"] = m["as_of"] + timedelta(hours=float(m["settings"]["stockout_horizon_hours"]))  # type: ignore[index,operator]
        m["fixable_only"] = False
    risk = """
        WITH due AS (
          SELECT ol.product_id, sum(ol.qty_ordered - ol.qty_allocated) AS d
            FROM pw.order_line ol JOIN pw.outbound_order o ON o.id = ol.order_id
           WHERE o.site_id = %(site)s AND o.status NOT IN ('SHIPPED', 'CANCELLED')
             AND ol.qty_ordered > ol.qty_allocated AND o.dispatch_deadline <= %(until)s
           GROUP BY ol.product_id
        ), stock AS (
          SELECT ib.product_id,
                 sum(ib.on_hand - ib.allocated) FILTER (WHERE b.bin_role = 'PICK') AS p,
                 sum(ib.on_hand - ib.allocated) AS s
            FROM pw.inventory_balance ib JOIN pw.bin b ON b.id = ib.bin_id
           WHERE ib.site_id = %(site)s AND b.is_active AND b.bin_role IN ('PICK', 'RESERVE', 'BULK')
           GROUP BY ib.product_id
        )
        SELECT due.product_id FROM due LEFT JOIN stock ON stock.product_id = due.product_id
         WHERE due.d > coalesce(stock.p, 0)
           AND (NOT %(fixable_only)s OR coalesce(stock.s, 0) >= due.d)
    """
    return _codes(
        conn,
        f"""
        WITH risk AS ({risk})
        SELECT b.code FROM pw.inventory_balance ib JOIN pw.bin b ON b.id = ib.bin_id
         WHERE ib.site_id = %(site)s AND b.bin_role = 'PICK' AND b.is_active
           AND ib.product_id IN (SELECT product_id FROM risk)
        UNION
        SELECT b.code FROM pw.replenishment_policy rp JOIN pw.bin b ON b.id = rp.pick_bin_id
         WHERE rp.site_id = %(site)s AND rp.product_id IN (SELECT product_id FROM risk)
        """,  # noqa: S608 - fixed fragments of this module; every value is bound
        m,
    )


REFERENCES: dict[str, Callable[[psycopg.Connection, UUID, str], list[str]]] = {
    "find_product_locations": _product_locations,
    "get_bin_contents": _bin,
    "get_low_stock": _low_stock,
    "get_top_movers": _top_movers,
    "get_dead_stock": _dead_stock,
    "get_slow_in_prime": _slow_in_prime,
    "get_zone_summary": _zone_summary,
    "get_capacity_hotspots": _capacity_hotspots,
    "get_order_stockout_risks": _stockout,
}
# References whose argument is optional (the rule takes its parameters from the site's settings).
NO_ARGUMENT = {
    "get_low_stock",
    "get_dead_stock",
    "get_slow_in_prime",
    "get_zone_summary",
    "get_capacity_hotspots",
}


def run_reference(conn: psycopg.Connection, tenant: UUID, site: UUID, ref: str) -> list[str]:
    """Gold location codes for a reference such as "find_product_locations:SKU-102" or "none"."""
    if ref == "none":
        return []
    name, _, argument = ref.partition(":")
    query = REFERENCES.get(name)
    if query is None or (not argument and name not in NO_ARGUMENT):
        raise ValueError(f"unknown reference {ref!r}")
    with conn.transaction():
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        return sorted(query(conn, site, argument))
