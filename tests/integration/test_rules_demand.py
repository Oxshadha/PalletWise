"""Demand and movement rules (semantic spec 3, 4.2, 4.3 and 6; Slice 2 step 1c).

Expected numbers are worked out by hand from the fixture rows. Days of cover is also checked
against an independent pure-Python reference (semantic spec 2) that reads the raw calendar.
"""

from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fixture_site import FixtureSite

pytestmark = pytest.mark.integration

Connect = Callable[[str], psycopg.Connection]
DAY = timedelta(days=1)


class Floor:
    """A site with a pick zone (prime), a reserve zone and a staging bin."""

    def __init__(self, connect_as: Connect) -> None:
        self.f = FixtureSite(connect_as)
        self.now = self.f.as_of()
        self.prime = self.f.zone("P", zone_type="FAST_PICK", prime=True)
        self.reserve = self.f.zone("R")
        self.stage_zone = self.f.zone("S", zone_type="STAGING")
        self.staging = self.f.bin(self.stage_zone, "S-01", role="STAGING")
        self._bins = 0

    def bin(self, prime: bool = True, role: str = "PICK") -> UUID:
        self._bins += 1
        zone = self.prime if prime else self.reserve
        return self.f.bin(zone, f"{'P' if prime else 'R'}-{self._bins:02d}", role=role)

    def pick(self, product: UUID, qty: int, ago: timedelta, *, cancelled: bool = False) -> UUID:
        return self.f.pick(product, qty, self.now - ago, self.bin(), self.staging, cancelled=cancelled)


def top(f: FixtureSite, days: int, limit: int = 20) -> list[tuple[str, int]]:
    from palletwise.db.connection import role_pool, tenant_session

    with tenant_session(role_pool("PW_AGENT_READ"), f.tenant, read_snapshot=True) as conn:
        rows = conn.execute(
            "SELECT sku, demand_units FROM sem.top_movers(%s, %s, %s, %s, %s)",
            (f.site, f.as_of(), f.settings(), days, limit),
        ).fetchall()
    return [(r[0], r[1]) for r in rows]


# ---------------------------------------------------------------- demand events (4.2)


def test_demand_counts_picks_of_live_orders_minus_their_reversals(connect_as: Connect) -> None:
    floor = Floor(connect_as)
    f, x = floor.f, floor.f.product("X")
    live = floor.pick(x, 10, DAY)  # +10
    dead = floor.pick(x, 5, DAY, cancelled=True)  # cancelled order: not demand
    back = floor.bin()
    f.movement("TRANSFER", x, 3, floor.now - DAY / 2, from_bin=floor.staging, to_bin=back,
               line=live, reason="PICK_REVERSAL")  # -3: offsets the live pick  # fmt: skip
    f.movement("TRANSFER", x, 2, floor.now - DAY / 2, from_bin=floor.staging, to_bin=back,
               line=dead, reason="PICK_REVERSAL")  # cancelled order: ignored  # fmt: skip
    f.movement("SHIP", x, 10, floor.now - DAY / 4, from_bin=floor.staging, line=live)  # never demand
    assert top(f, 7) == [("X", 7)]  # 10 - 3


def test_top_movers_use_the_last_n_calendar_days_ending_now(connect_as: Connect) -> None:
    floor = Floor(connect_as)
    f = floor.f
    a, b, c, d = (f.product(s) for s in ("A", "B", "C", "D"))
    floor.pick(a, 5, 7 * DAY)  # exactly 7 days ago: outside "the last 7 days"
    floor.pick(a, 4, 7 * DAY - timedelta(minutes=1))  # just inside
    floor.pick(b, 9, 2 * DAY)
    floor.pick(c, 4, DAY)
    floor.pick(d, 1, 10 * DAY)  # outside
    assert top(f, 7) == [("B", 9), ("A", 4), ("C", 4)]  # ties by SKU
    assert top(f, 7, limit=2) == [("B", 9), ("A", 4)]
    assert top(f, 30) == [("A", 9), ("B", 9), ("C", 4), ("D", 1)]
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        top(f, 0)


# ---------------------------------------------------------------- DEAD_STOCK (6)


def test_dead_stock_by_whole_days_and_receipt_age(connect_as: Connect) -> None:
    floor = Floor(connect_as)
    f = floor.f
    recent, old, never_old, never_new, no_receipt, staged = (
        f.product(s, value=10) for s in ("RECENT", "OLD", "NEVEROLD", "NEVERNEW", "NORECEIPT", "STAGED")
    )
    for product, qty in ((recent, 10), (old, 20), (never_old, 30), (never_new, 40), (no_receipt, 50)):
        f.stock(floor.bin(prime=False, role="RESERVE"), product, qty)
    f.stock(floor.staging, staged, 99)  # only in staging: not storable, never dead stock
    for product in (recent, old, never_old, staged):
        f.movement("RECEIPT", product, 1, floor.now - 90 * DAY, to_bin=floor.staging)
    f.movement("RECEIPT", never_new, 1, floor.now - 10 * DAY, to_bin=floor.staging)  # new item
    floor.pick(recent, 1, 30 * DAY + timedelta(hours=23))  # 30 whole days: not more than 30
    floor.pick(old, 1, 31 * DAY)  # 31 whole days: dead
    rows = f.rows("dead_stock")
    # Ordered by value tied up: NORECEIPT 500, NEVEROLD 300, OLD 200.
    assert [(r["sku"], r["days_since_last_pick"], r["stock_value"]) for r in rows] == [
        ("NORECEIPT", None, Decimal(500)),
        ("NEVEROLD", None, Decimal(300)),
        ("OLD", 31, Decimal(200)),
    ]
    assert rows[0]["first_receipt_at"] is None and rows[0]["period_days"] == 30


def test_the_dead_stock_period_comes_from_the_settings_version(connect_as: Connect) -> None:
    floor = Floor(connect_as)
    f, p = floor.f, floor.f.product("P")
    f.stock(floor.bin(prime=False, role="RESERVE"), p, 5)
    f.movement("RECEIPT", p, 5, floor.now - 90 * DAY, to_bin=floor.staging)
    floor.pick(p, 1, 20 * DAY)
    assert f.rows("dead_stock") == []  # 20 days, period 30
    assert [r["sku"] for r in f.rows("dead_stock", f.new_settings(dead_stock_days=14))] == ["P"]


# ---------------------------------------------------------------- SLOW_IN_PRIME (6, D1)


def test_slow_products_with_stock_in_prime_bins(connect_as: Connect) -> None:
    floor = Floor(connect_as)
    f = floor.f
    slow, fast, never = f.product("SLOW"), f.product("FAST"), f.product("NEVER")
    slow_prime, slow_reserve = floor.bin(), floor.bin(prime=False, role="RESERVE")
    f.stock(slow_prime, slow, 6)
    f.stock(slow_reserve, slow, 60)  # not prime: not listed
    f.stock(floor.bin(), fast, 8)
    f.stock(floor.bin(), never, 3)
    floor.pick(slow, 1, 20 * DAY)  # product level: 20 > 14
    floor.pick(fast, 1, 2 * DAY)  # picked recently (from another bin): not slow
    rows = f.rows("slow_in_prime")
    assert [(r["sku"], r["on_hand"], r["days_since_last_pick"]) for r in rows] == [
        ("NEVER", 3, None),  # never picked first
        ("SLOW", 6, 20),
    ]
    assert {r["zone_code"] for r in rows} == {"P"} and rows[0]["period_days"] == 14


# ---------------------------------------------------------------- days of cover (4.3)


def reference_cover(db: psycopg.Connection, site: UUID, now: datetime, w: int, demand: dict[date, int],
                    storable: int) -> tuple[date, date, Decimal | None]:  # fmt: skip
    """Pure Python, from the raw calendar: the last W operating days before today, demand summed
    over every date from the first to the last of them, divided by W."""
    region, zone = db.execute("SELECT region_code, time_zone FROM pw.site WHERE id = %s", (site,)).fetchone()  # type: ignore[misc]
    today = now.astimezone(ZoneInfo(zone)).date()
    days = [r[0] for r in db.execute(
        "SELECT date FROM pw.business_calendar WHERE region_code = %s AND is_operating AND date < %s"
        " ORDER BY date DESC LIMIT %s", (region, today, w),
    ).fetchall()]  # fmt: skip
    first, last = min(days), max(days)
    total = sum(q for d, q in demand.items() if first <= d <= last)
    if total <= 0:
        return first, last, None
    return first, last, (Decimal(storable) / (Decimal(total) / w)).quantize(Decimal("0.1"), ROUND_HALF_UP)


def test_days_of_cover_matches_the_reference_and_ignores_non_storable_stock(connect_as: Connect) -> None:
    floor = Floor(connect_as)
    f = floor.f
    settings = f.new_settings(trailing_window_operating_days=5)
    zone = ZoneInfo("Asia/Colombo")
    cover, idle = f.product("COVER"), f.product("IDLE")
    f.stock(floor.bin(prime=False, role="RESERVE"), cover, 100)
    f.stock(floor.staging, cover, 50)  # in staging: not storable, not cover
    f.stock(floor.bin(prime=False, role="RESERVE"), idle, 40)

    first, last, _ = reference_cover(f.db, f.site, floor.now, 5, {}, 0)
    local_noon = lambda d: datetime.combine(d, time(12), zone)  # noqa: E731
    today = floor.now.astimezone(zone).date()
    demand = {first: 6, last: 9, first - DAY: 100, today: 100}  # only first and last are in the window
    if (first + DAY) < last:
        demand[first + DAY] = 5  # a date inside the window (operating or not) also counts
    for d, qty in demand.items():
        floor.pick(cover, qty, floor.now - local_noon(d))
    expected_first, expected_last, expected = reference_cover(f.db, f.site, floor.now, 5, demand, 100)

    rows = {r["sku"]: r for r in f.rows("days_of_cover", settings)}
    assert (rows["COVER"]["window_from"], rows["COVER"]["window_to"]) == (expected_first, expected_last)
    assert rows["COVER"]["operating_days"] == 5 and rows["COVER"]["storable_on_hand"] == 100
    assert rows["COVER"]["days_of_cover"] == expected
    assert rows["IDLE"]["days_of_cover"] is None and rows["IDLE"]["window_demand"] == 0  # no recent demand


def test_days_of_cover_agrees_with_the_reference_on_random_demand(connect_as: Connect) -> None:
    import random

    floor = Floor(connect_as)
    f = floor.f
    rng = random.Random(7)
    w = 10
    settings = f.new_settings(trailing_window_operating_days=w)
    zone = ZoneInfo("Asia/Colombo")
    today = floor.now.astimezone(zone).date()
    for n in range(6):
        product = f.product(f"R{n}")
        storable = rng.randint(0, 500)
        if storable:
            f.stock(floor.bin(prime=False, role="RESERVE"), product, storable)
        demand: dict[date, int] = {}
        for _ in range(rng.randint(0, 12)):
            d = today - rng.randint(0, 25) * DAY
            qty = rng.randint(1, 30)
            demand[d] = demand.get(d, 0) + qty
            floor.pick(product, qty, floor.now - datetime.combine(d, time(10), zone))
        _, _, expected = reference_cover(f.db, f.site, floor.now, w, demand, storable)
        got = {r["sku"]: r for r in f.rows("days_of_cover", settings)}
        if storable or demand:
            assert got[f"R{n}"]["days_of_cover"] == expected, (n, demand, storable)
