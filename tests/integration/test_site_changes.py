"""Site change sequence and live stream (Slice 3 step 2; agent spec 9.2, slice-3-acceptance 2.1)."""

import select
from collections.abc import Callable
from datetime import timedelta
from uuid import UUID

import psycopg
import pytest
from fixture_site import FixtureSite

from palletwise.db.connection import role_pool, tenant_session
from palletwise.db.site_changes import changes_since

pytestmark = pytest.mark.integration

Connect = Callable[[str], psycopg.Connection]
TICK = "UPDATE pw.site_clock SET sim_time = sim_time + interval '1 minute' WHERE site_id = %s"


@pytest.fixture
def site(connect_as: Connect) -> FixtureSite:
    return FixtureSite(connect_as)


def since(f: FixtureSite, after: int) -> tuple[int, tuple[str, ...], bool]:
    found = changes_since(role_pool("PW_APP"), f.tenant, f.site, after)
    return found.seq, found.kinds, found.complete


def stocked(f: FixtureSite) -> tuple[UUID, UUID]:
    zone = f.zone("P")
    product, bin_id = f.product("A"), f.bin(zone, "P-1")
    return product, bin_id


def write_stock(
    f: FixtureSite, statements: list[tuple[str, tuple[object, ...]]], *, commit: bool = True
) -> None:
    """Writes as a writer does: one transaction with the tenant set."""

    class Rollback(Exception):
        pass

    try:
        with f.db.transaction():
            f.db.execute("SELECT set_config('app.tenant_id', %s, true)", (str(f.tenant),))
            for sql, args in statements:
                f.db.execute(sql, args)
            if not commit:
                raise Rollback
    except Rollback:
        pass


def insert_stock(f: FixtureSite, product: UUID, bin_id: UUID, qty: int) -> tuple[str, tuple[object, ...]]:
    return (
        "INSERT INTO pw.inventory_balance (tenant_id, site_id, bin_id, product_id, on_hand, allocated,"
        " last_movement_at) VALUES (%s, %s, %s, %s, %s, 0, now())"
        " ON CONFLICT (tenant_id, site_id, bin_id, product_id)"
        " DO UPDATE SET on_hand = EXCLUDED.on_hand",
        (f.tenant, f.site, bin_id, product, qty),
    )


def test_a_change_advances_the_sequence_with_its_kind(site: FixtureSite) -> None:
    before, _, _ = since(site, 0)
    product, bin_id = stocked(site)
    write_stock(site, [insert_stock(site, product, bin_id, 5)])
    seq, kinds, complete = since(site, before)
    assert seq == before + 1 and kinds == ("inventory",) and complete


def test_one_transaction_is_one_change_whatever_its_size(site: FixtureSite) -> None:
    product, bin_id = stocked(site)
    before, _, _ = since(site, 0)
    write_stock(site, [insert_stock(site, product, bin_id, n) for n in range(1, 50)]
                + [(TICK, (site.site,))])  # fmt: skip
    seq, kinds, _ = since(site, before)
    assert seq == before + 1 and kinds == ("clock", "inventory")


def test_a_rolled_back_change_leaves_no_trace(site: FixtureSite, connect_as: Connect) -> None:
    product, bin_id = stocked(site)
    before, _, _ = since(site, 0)
    listener = connect_as("postgres")
    listener.autocommit = True
    listener.execute("LISTEN pw_site_changes")
    write_stock(site, [insert_stock(site, product, bin_id, 5)], commit=False)
    assert since(site, before)[0] == before
    assert select.select([listener], [], [], 0.5) == ([], [], [])  # no notification either
    write_stock(site, [insert_stock(site, product, bin_id, 5)])
    assert select.select([listener], [], [], 2.0)[0], "a committed change notifies"
    notes = list(listener.notifies(timeout=0.5, stop_after=1))
    assert notes and notes[0].payload == str(site.site)
    listener.close()


def test_the_history_tells_what_was_missed_or_to_refetch_everything(site: FixtureSite) -> None:
    product, bin_id = stocked(site)
    start, _, _ = since(site, 0)
    write_stock(site, [insert_stock(site, product, bin_id, 1)])
    write_stock(site, [(TICK, (site.site,))])
    seq, kinds, complete = since(site, start)
    assert seq == start + 2 and kinds == ("clock", "inventory") and complete
    assert since(site, seq) == (seq, (), True)  # up to date
    assert since(site, seq + 5)[2] is False  # ahead of the server: the database was rebuilt
    site.db.execute("DELETE FROM pw.site_change WHERE site_id = %s AND seq = %s", (site.site, start + 1))
    assert since(site, start)[2] is False  # history no longer reaches back: refetch everything


def test_another_tenant_cannot_read_a_sites_changes(site: FixtureSite, connect_as: Connect) -> None:
    product, bin_id = stocked(site)
    write_stock(site, [insert_stock(site, product, bin_id, 1)])
    other = FixtureSite(connect_as)
    assert changes_since(role_pool("PW_APP"), other.tenant, site.site, 0).seq == 0
    with tenant_session(role_pool("PW_APP"), other.tenant) as conn:
        assert conn.execute(
            "SELECT count(*) FROM pw.site_change WHERE site_id = %s", (site.site,)
        ).fetchone() == (0,)


def test_only_visible_exception_changes_count(site: FixtureSite) -> None:
    from test_exception_engine import Engine

    engine = Engine(site)
    zone = site.zone("P", zone_type="FAST_PICK")
    product, pick = site.product("L"), site.bin(zone, "P-L")
    site.stock(pick, product, 2)
    site.policy(product, pick, 5, 20)
    site.pick(product, 1, site.as_of() - timedelta(days=1), pick, site.bin(zone, "S-1", role="STAGING"))
    start, _, _ = since(site, 0)
    engine.evaluate()
    seq, kinds, _ = since(site, start)
    assert "exceptions" in kinds
    engine.load()  # same condition, same score: the record is only touched (last_evaluated_at)
    engine.evaluate()
    assert "exceptions" not in since(site, seq)[1]
