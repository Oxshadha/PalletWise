"""Governed views and the query role (slice-5-acceptance step 1, ADR 0002 sections 2 and 3)."""

from collections.abc import Callable
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
import yaml
from support import load_site

pytestmark = pytest.mark.integration

Connect = Callable[[str], psycopg.Connection]
CATALOG = Path(__file__).resolve().parents[2] / "services/api/src/palletwise/semantic/gq_catalog.yaml"
VIEWS = ("site_now", "products", "locations", "stock", "movements", "orders", "order_lines",
         "allocations", "exceptions", "calendar")  # fmt: skip


@pytest.fixture(scope="module")
def site(connect_session: Connect) -> tuple[UUID, UUID]:
    tenant = uuid4()
    site_id, _, _ = load_site(connect_session, tenant)
    return tenant, site_id


def count(conn: psycopg.Connection, view: str, tenant: object, site: object) -> int:
    with conn.transaction():
        if tenant is not None:
            conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        if site is not None:
            conn.execute("SELECT set_config('app.site_id', %s, true)", (str(site),))
        row = conn.execute(f"SELECT count(*) FROM gq.{view}").fetchone()
    assert row is not None
    return int(row[0])


def test_the_site_and_tenant_come_from_the_session_only(connect_as: Connect, site: tuple[UUID, UUID]) -> None:
    tenant, site_id = site
    q = connect_as("pw_query_read")
    for view in ("stock", "orders", "order_lines", "movements", "locations", "calendar", "site_now"):
        assert count(q, view, tenant, site_id) > 0, view
        assert count(q, view, tenant, uuid4()) == 0, view  # another site: nothing
    assert count(q, "products", tenant, site_id) > 0
    assert count(q, "products", uuid4(), site_id) == 0  # another tenant: nothing


def test_a_missing_tenant_or_site_fails_closed(connect_as: Connect, site: tuple[UUID, UUID]) -> None:
    tenant, site_id = site
    q = connect_as("pw_query_read")
    with pytest.raises(psycopg.Error):
        count(q, "stock", None, site_id)
    with pytest.raises(psycopg.Error):
        count(q, "stock", tenant, None)


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT 1 FROM pw.movement LIMIT 1",
        "SELECT 1 FROM sem.stock LIMIT 1",
        "SELECT 1 FROM pw.audit_event LIMIT 1",
        "SELECT pw.current_subject()",
        "SELECT pw.current_tenant()",  # executable by row-level security, not by name
        "CREATE TABLE gq.x (a int)",
        "CREATE TEMP TABLE x (a int)",
        "CREATE FUNCTION public.f() RETURNS int LANGUAGE sql AS 'SELECT 1'",
        "SET ROLE pw_app",
    ],
)
def test_the_query_role_reaches_nothing_else(connect_as: Connect, statement: str) -> None:
    q = connect_as("pw_query_read")
    with pytest.raises(psycopg.Error), q.transaction():
        q.execute(statement)  # pyright: ignore[reportArgumentType, reportCallIssue]


def test_the_query_role_is_locked_down(connect_as: Connect) -> None:
    """ADR 0002 section 2: names resolve to built-ins only while these hold; a later grant fails here."""
    su = connect_as("postgres")
    role = su.execute(
        "SELECT rolbypassrls, rolsuper, rolcreaterole, rolcreatedb, rolconfig FROM pg_roles"
        " WHERE rolname = 'pw_query_read'"
    ).fetchone()
    assert role is not None and role[:4] == (False, False, False, False)
    assert "search_path=pg_catalog, gq" in (role[4] or [])
    schemas = su.execute(
        "SELECT nspname FROM pg_namespace WHERE has_schema_privilege('pw_query_read', oid, 'USAGE')"
        " AND nspname NOT IN ('pg_catalog', 'information_schema') AND nspname NOT LIKE 'pg_toast%'"
        " AND nspname NOT LIKE 'pg_temp%'"
    ).fetchall()
    assert {s[0] for s in schemas} <= {"gq", "public"}
    creates = su.execute(
        "SELECT nspname FROM pg_namespace WHERE has_schema_privilege('pw_query_read', oid, 'CREATE')"
    ).fetchall()
    assert creates == []
    temp = su.execute(
        "SELECT has_database_privilege('pw_query_read', current_database(), 'TEMPORARY')"
    ).fetchone()
    assert temp == (False,)
    tables = su.execute(
        "SELECT table_schema, table_name FROM information_schema.role_table_grants"
        " WHERE grantee = 'pw_query_read' AND table_schema <> 'gq'"
    ).fetchall()
    assert tables == []


def test_the_catalog_describes_every_view_and_column(connect_as: Connect) -> None:
    catalog = yaml.safe_load(CATALOG.read_text(encoding="utf-8"))["views"]
    assert set(catalog) == set(VIEWS)
    su = connect_as("postgres")
    for view in VIEWS:
        live = [r[0] for r in su.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'gq' AND table_name = %s"
            " ORDER BY ordinal_position", (view,),
        ).fetchall()]  # fmt: skip
        assert live == list(catalog[view]["columns"]), view
