"""Reference queries for gold highlights (evals spec 3).

Plain SQL over the canonical tables, written independently of the tools and the semantic layer,
so a tool bug cannot also corrupt its own gold answer. They run as `pw_eval_reader`, a test-only
role that exists only in the evaluation database: SELECT on the canonical tables, no writes, no
BYPASSRLS, always inside a tenant context and filtered by site.

Gold sets are location codes (stable across tenants), not ids.
"""

from collections.abc import Callable
from uuid import UUID

import psycopg
from psycopg import sql

EVAL_ROLE = "pw_eval_reader"
GOLDEN_TABLES = ("product", "bin", "inventory_balance")


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


REFERENCES: dict[str, Callable[[psycopg.Connection, UUID, str], list[str]]] = {
    "find_product_locations": _product_locations,
    "get_bin_contents": _bin,
}


def run_reference(conn: psycopg.Connection, tenant: UUID, site: UUID, ref: str) -> list[str]:
    """Gold location codes for a reference such as "find_product_locations:SKU-102" or "none"."""
    if ref == "none":
        return []
    name, _, argument = ref.partition(":")
    query = REFERENCES.get(name)
    if query is None or not argument:
        raise ValueError(f"unknown reference {ref!r}")
    with conn.transaction():
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        return sorted(query(conn, site, argument))
