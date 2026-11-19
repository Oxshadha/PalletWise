"""Migrations, calendar, isolation, roles and hardening (CWM 13, baseline 8.3)."""

import csv
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest

pytestmark = pytest.mark.integration

REPO = Path(__file__).resolve().parents[2]
Connect = Callable[[str], psycopg.Connection]


def test_calendar_matches_source_exactly(connect_as: Connect) -> None:
    with (REPO / "data" / "reference" / "calendar.csv").open(encoding="utf-8") as fh:
        source = list(csv.DictReader(fh))
    conn = connect_as("pw_app")
    rows = conn.execute(
        "SELECT date::text, is_operating, festival, festival_ramp::text FROM pw.business_calendar "
        "WHERE region_code = 'LK' ORDER BY date"
    ).fetchall()
    assert len(rows) == len(source)
    for (day, operating, festival, ramp), src in zip(rows, source, strict=True):
        assert day == src["date"]
        assert operating == (src["is_operating"] == "1")
        assert festival == (src["festival"] or None)
        assert float(ramp) == float(src["festival_ramp"])
    region = conn.execute(
        "SELECT coverage_start::text, coverage_end::text FROM pw.calendar_region WHERE region_code='LK'"
    ).fetchone()
    assert region == (source[0]["date"], source[-1]["date"])


def test_migration_round_trip(database: dict[str, Any]) -> None:
    from palletwise.db import migrate

    migrate.downgrade(database["settings"], "base")
    migrate.upgrade(database["settings"])
    migrate.run_calendar(database["settings"])


def test_the_prompt_migration_moves_only_system_seeded_agents(
    database: dict[str, Any], connect_as: Connect
) -> None:
    """Migration 0013 (prompt v2) changes the seed and the rows it seeded; an agent an
    administrator customized keeps its prompt version (Slice 2 step 5)."""
    from support import provision_site

    from palletwise.db import migrate

    db = connect_as("postgres")
    db.autocommit = True
    migrate.downgrade(database["settings"], "0012_query_blocks")
    try:
        seeded, _ = provision_site(connect_as)
        customized, _ = provision_site(connect_as)
        db.execute("UPDATE pw.agent SET updated_by = 'admin-1' WHERE tenant_id = %s", (customized,))
    finally:
        migrate.upgrade(database["settings"])
    prompts = dict(db.execute(
        "SELECT tenant_id, prompt_version FROM pw.agent WHERE tenant_id = ANY(%s)", ([seeded, customized],)
    ).fetchall())  # fmt: skip
    assert prompts == {seeded: "system.v2", customized: "system.v1"}


def test_missing_tenant_context_fails_closed(connect_as: Connect) -> None:
    conn = connect_as("pw_app")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("SELECT count(*) FROM pw.site").fetchone()


def test_tenant_context_does_not_leak_across_transactions(connect_as: Connect) -> None:
    conn = connect_as("pw_app")
    with conn.transaction():
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(UUID(int=1)),))
    with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
        conn.execute("SELECT count(*) FROM pw.site").fetchone()


@pytest.mark.parametrize(
    ("role", "statement"),
    [
        (
            "pw_ingest",
            "INSERT INTO pw.inventory_balance VALUES (gen_random_uuid(), gen_random_uuid(), "
            "gen_random_uuid(), gen_random_uuid(), 1, 0)",
        ),
        ("pw_ingest", "INSERT INTO pw.movement (id) VALUES (gen_random_uuid())"),
        ("pw_app", "DELETE FROM pw.movement"),
        ("pw_app", "SELECT pw.apply_record(NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL)"),
        ("pw_agent_read", "SELECT * FROM pw.product"),
        ("pw_sim_admin", "SELECT pw.apply_record(NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL)"),
        ("pw_app", "SELECT * FROM sim.outbox"),
        ("pw_ingest", "SELECT * FROM sim.outbox"),
        ("sim_outbox_reader", "SELECT * FROM pw.product"),
        ("sim_owner", "SELECT * FROM pw.product"),
    ],
)
def test_roles_cannot_leave_their_write_path(connect_as: Connect, role: str, statement: str) -> None:
    conn = connect_as(role)
    with pytest.raises(psycopg.errors.InsufficientPrivilege), conn.transaction():
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(UUID(int=1)),))
        conn.execute(statement)


def test_privileged_functions_are_hardened(connect_as: Connect) -> None:
    conn = connect_as("postgres")
    rows = conn.execute(
        """
        SELECT p.proname, p.proconfig, r.rolname, r.rolcanlogin, r.rolsuper, r.rolbypassrls,
               has_function_privilege('public', p.oid, 'EXECUTE') AS public_exec
          FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
          JOIN pg_roles r ON r.oid = p.proowner
         WHERE n.nspname = 'pw' AND p.prosecdef
        """
    ).fetchall()
    assert rows, "expected SECURITY DEFINER functions"
    for name, config, owner, can_login, superuser, bypass, public_exec in rows:
        assert owner == "pw_owner", name
        assert not (can_login or superuser or bypass), name
        assert config and any(c.startswith("search_path=") for c in config), name
        assert not public_exec, name


def test_rls_forced_on_every_tenant_table(connect_as: Connect) -> None:
    conn = connect_as("postgres")
    rows = conn.execute(
        """
        SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'pw' AND c.relkind = 'r'
           AND c.relname NOT IN ('calendar_region', 'business_calendar')
        """
    ).fetchall()
    assert rows
    for name, enabled, forced in rows:
        assert enabled and forced, name
