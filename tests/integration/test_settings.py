"""Versioned site settings (CWM 9.1; Slice 2 acceptance step 1, D3).

Real PostgreSQL with the real roles and forced row-level security.
"""

import json
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from support import provision_site

from palletwise.db.connection import role_pool, tenant_session, tenant_transaction

pytestmark = pytest.mark.integration

Connect = Callable[[str], psycopg.Connection]

DEFAULTS = {
    "dead_stock_days": 30,
    "slow_in_prime_days": 14,
    "capacity_threshold_pct": 90,
    "stockout_horizon_hours": 24,
    "trailing_window_operating_days": 28,
}


def create(conn: psycopg.Connection, tenant: UUID, site: UUID, values: Any, schema_version: int = 1) -> UUID:
    with tenant_transaction(conn, tenant):
        row = conn.execute(
            "SELECT pw.create_settings_version(%s, %s, %s, %s, %s, %s)",
            (tenant, site, schema_version, json.dumps(values), "admin-1", "test"),
        ).fetchone()
    assert row is not None
    return row[0]


def current(tenant: UUID, site: UUID) -> tuple[Any, ...] | None:
    with tenant_session(role_pool("PW_AGENT_READ"), tenant, read_snapshot=True) as conn:
        return conn.execute(
            'SELECT version_no, schema_version, "values" FROM sem.current_settings WHERE site_id = %s',
            (site,),
        ).fetchone()


def test_every_new_site_starts_with_the_default_settings(connect_as: Connect) -> None:
    tenant, site = provision_site(connect_as)
    assert current(tenant, site) == (1, 1, DEFAULTS)


def test_a_new_version_takes_effect_and_the_old_one_stays(connect_as: Connect) -> None:
    tenant, site = provision_site(connect_as)
    first = current(tenant, site)
    new_id = create(connect_as("pw_app"), tenant, site, {**DEFAULTS, "dead_stock_days": 45})
    assert current(tenant, site) == (2, 1, {**DEFAULTS, "dead_stock_days": 45})
    with tenant_session(role_pool("PW_AGENT_READ"), tenant, read_snapshot=True) as conn:
        versions = conn.execute(
            "SELECT version_no FROM sem.settings_version WHERE site_id = %s ORDER BY version_no", (site,)
        ).fetchall()
        assert versions == [(1,), (2,)] and first is not None
        assert conn.execute("SELECT sem.setting(%s, 'dead_stock_days')", (new_id,)).fetchone() == (45,)


@pytest.mark.parametrize(
    "values",
    [
        {**DEFAULTS, "surprise": 1},  # unknown key
        {k: v for k, v in DEFAULTS.items() if k != "dead_stock_days"},  # missing key
        {**DEFAULTS, "dead_stock_days": 0},  # out of range
        {**DEFAULTS, "dead_stock_days": 30.5},  # not a whole number of days
        {**DEFAULTS, "capacity_threshold_pct": 101},
        {**DEFAULTS, "stockout_horizon_hours": "24"},  # wrong type
        [1, 2, 3],  # not an object
    ],
)
def test_invalid_settings_are_refused(connect_as: Connect, values: Any) -> None:
    tenant, site = provision_site(connect_as)
    with pytest.raises(psycopg.errors.CheckViolation):
        create(connect_as("pw_app"), tenant, site, values)


def test_an_unknown_schema_version_is_refused(connect_as: Connect) -> None:
    tenant, site = provision_site(connect_as)
    with pytest.raises(psycopg.errors.CheckViolation):
        create(connect_as("pw_app"), tenant, site, DEFAULTS, schema_version=2)


def test_versions_are_append_only_even_for_the_owner(connect_as: Connect) -> None:
    _, site = provision_site(connect_as)
    owner = connect_as("postgres")
    owner.autocommit = True
    for statement in (
        "UPDATE pw.settings_version SET note = 'x' WHERE site_id = %s",
        "DELETE FROM pw.settings_version WHERE site_id = %s",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            owner.execute(statement, (site,))


def test_another_tenant_can_neither_read_nor_write_a_sites_settings(connect_as: Connect) -> None:
    tenant, site = provision_site(connect_as)
    other, _ = provision_site(connect_as)
    assert current(other, site) is None
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        create(connect_as("pw_app"), other, site, DEFAULTS)  # the site is not visible to that tenant
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn = connect_as("pw_app")
        with tenant_transaction(conn, other):
            conn.execute(
                "SELECT pw.create_settings_version(%s, %s, 1, %s, 'x', NULL)",
                (tenant, site, json.dumps(DEFAULTS)),
            )


def test_the_reader_sees_settings_only_through_sem(connect_as: Connect) -> None:
    tenant, _ = provision_site(connect_as)
    with (
        pytest.raises(psycopg.errors.InsufficientPrivilege),
        tenant_session(role_pool("PW_AGENT_READ"), tenant) as conn,
    ):
        conn.execute("SELECT * FROM pw.settings_version")


def test_a_missing_setting_is_an_error_never_a_default(connect_as: Connect) -> None:
    tenant, _ = provision_site(connect_as)
    with (
        pytest.raises(psycopg.errors.NoDataFound),
        tenant_session(role_pool("PW_AGENT_READ"), tenant) as conn,
    ):
        conn.execute("SELECT sem.setting(%s, 'dead_stock_days')", (uuid4(),))


def test_concurrent_saves_get_distinct_version_numbers(connect_as: Connect) -> None:
    from concurrent.futures import ThreadPoolExecutor

    tenant, site = provision_site(connect_as)

    def save(n: int) -> None:
        with role_pool("PW_APP").connection() as conn:
            create(conn, tenant, site, {**DEFAULTS, "dead_stock_days": 30 + n})

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(save, range(12)))
    with tenant_session(role_pool("PW_AGENT_READ"), tenant, read_snapshot=True) as conn:
        numbers = [r[0] for r in conn.execute(
            "SELECT version_no FROM sem.settings_version WHERE site_id = %s ORDER BY version_no", (site,)
        ).fetchall()]  # fmt: skip
    assert numbers == list(range(1, 14))
