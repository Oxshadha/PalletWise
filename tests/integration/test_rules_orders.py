"""Order rules (semantic spec 4.5 and 6; Slice 2 step 1d). Expected numbers worked out by hand.

The fixture clock is 06:00 site time, so "now + 2 hours" is still today and "now + 30 hours"
is tomorrow.
"""

from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fixture_site import FixtureSite

from palletwise.db.connection import role_pool, tenant_session

pytestmark = pytest.mark.integration

Connect = Callable[[str], psycopg.Connection]
H = timedelta(hours=1)


def call(f: FixtureSite, sql: str, *args: Any) -> list[dict[str, Any]]:
    with tenant_session(role_pool("PW_AGENT_READ"), f.tenant, read_snapshot=True) as conn:
        cursor = conn.execute(sql, (f.site, f.as_of(), f.settings(), *args))
        names = [c.name for c in cursor.description or ()]
        return [dict(zip(names, r, strict=True)) for r in cursor.fetchall()]


@pytest.fixture
def risk_site(connect_as: Connect) -> FixtureSite:
    f = FixtureSite(connect_as)
    now = f.as_of()
    pick_zone, reserve_zone = f.zone("P", zone_type="FAST_PICK", prime=True), f.zone("R")
    products: dict[str, UUID] = {s: f.product(s) for s in ("A", "B", "C", "D")}

    def stock(sku: str, pick: int, reserve: int) -> None:
        f.stock(f.bin(pick_zone, f"P-{sku}"), products[sku], pick)
        if reserve:
            f.stock(f.bin(reserve_zone, f"R-{sku}", role="RESERVE"), products[sku], reserve)

    stock("A", 10, 50)  # P = 10, S = 60
    stock("B", 5, 0)  # P = 5, S = 5
    stock("C", 20, 0)  # P = 20
    stock("D", 0, 10)  # P = 0, S = 10 (a pick bin row with 0 on hand)

    o1 = f.order(deadline=now + 2 * H)  # today 08:00
    f.line(o1, products["A"], 12)  # unallocated 12
    f.line(o1, products["B"], 8)  # unallocated 8
    f.line(o1, products["A"], 7, allocated=7)  # fully allocated: no demand left
    o2 = f.order(deadline=now + 30 * H)  # tomorrow: outside H = 24
    f.line(o2, products["A"], 100)
    f.line(o2, products["C"], 15)
    o3 = f.order(deadline=now - 1 * H)  # overdue and still open
    f.line(o3, products["D"], 4)
    o4 = f.order(deadline=now + 1 * H, status="CANCELLED")  # cancelled: never demand
    f.line(o4, products["C"], 500)
    o5 = f.order(deadline=now + 3 * H)
    f.line(o5, products["C"], 15)  # C: D = 15 <= P = 20, not at risk
    f.risk_orders = {"O1": o1, "O3": o3}  # type: ignore[attr-defined]
    return f


# ---------------------------------------------------------------- STOCKOUT_RISK


def test_stockout_risk_with_the_fixable_flag(risk_site: FixtureSite) -> None:
    rows = call(risk_site, "SELECT * FROM sem.stockout_risk(%s, %s, %s, NULL)")  # H = 24 from settings
    got = [(r["sku"], r["demand_due"], r["pick_face_available"], r["shortfall"], r["reserve_available"],
            r["fixable"], r["true_shortage"]) for r in rows]  # fmt: skip
    assert got == [
        ("B", 8, 5, 3, 0, False, 3),  # true shortage first: 8 - 5 = 3 short, nothing in reserve
        ("D", 4, 0, 4, 10, True, 0),  # then fixable by earliest deadline: D is overdue
        ("A", 12, 10, 2, 50, True, 0),  # 12 - 10 = 2 short, reserve 60 - 10 = 50 covers it
    ]
    assert rows[0]["horizon_hours"] == Decimal(24) and rows[2]["orders"] == 1


def test_a_longer_horizon_turns_a_fixable_risk_into_a_shortage(risk_site: FixtureSite) -> None:
    rows = {r["sku"]: r for r in call(risk_site, "SELECT * FROM sem.stockout_risk(%s, %s, %s, %s)", 40)}
    # A: D = 12 + 100 = 112, shortfall 102, reserve 50: not fixable, 52 truly short.
    assert (rows["A"]["shortfall"], rows["A"]["fixable"], rows["A"]["true_shortage"]) == (102, False, 52)
    assert "C" in rows  # C: D = 15 + 15 = 30 > 20 once tomorrow's order is in range
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        call(risk_site, "SELECT * FROM sem.stockout_risk(%s, %s, %s, %s)", 0)


def test_orders_at_risk_lists_each_affected_order(risk_site: FixtureSite) -> None:
    rows = call(risk_site, "SELECT * FROM sem.orders_at_risk(%s, %s, %s, NULL)")
    got = [(r["order_id"], r["products_at_risk"], r["any_true_shortage"], r["hours_until_due"]) for r in rows]
    orders = risk_site.risk_orders  # type: ignore[attr-defined]
    assert got == [
        (orders["O3"], 1, False, Decimal("-1.0")),  # overdue, D fixable
        (orders["O1"], 2, True, Decimal("2.0")),  # A fixable, B truly short
    ]


def test_replenish_before_the_first_dispatch_keeps_only_fixable_products(risk_site: FixtureSite) -> None:
    rows = call(risk_site, "SELECT * FROM sem.replenish_before_dispatch(%s, %s, %s)")
    # First open deadline today after 06:00 is O1 at 08:00: H = 2 hours. Due within it: O1 and
    # the overdue O3. Fixable: D (shortfall 4) and A (shortfall 2); B is a true shortage.
    assert [(r["sku"], r["shortfall"]) for r in rows] == [("D", 4), ("A", 2)]
    assert rows[0]["horizon_hours"] == Decimal(2)


def test_no_dispatch_left_today_means_nothing_to_replenish_before_it(connect_as: Connect) -> None:
    f = FixtureSite(connect_as)
    product = f.product("T")
    f.stock(f.bin(f.zone("P"), "P-01"), product, 1)
    f.line(f.order(deadline=f.as_of() + 30 * H), product, 50)  # tomorrow only
    assert call(f, "SELECT * FROM sem.replenish_before_dispatch(%s, %s, %s)") == []


# ---------------------------------------------------------------- delayed orders (4.5)


def test_delayed_orders_open_overdue_and_shipped_late_since_from(connect_as: Connect) -> None:
    f = FixtureSite(connect_as)
    now = f.as_of()
    product = f.product("X")
    open_late = f.order(deadline=now - 1 * H)
    shipped_late = f.order(deadline=now - 5 * H, status="SHIPPED", shipped_at=now - 3 * H)  # 01:00 today
    shipped_late_yesterday = f.order(deadline=now - 30 * H, status="SHIPPED", shipped_at=now - 20 * H)
    f.order(deadline=now - 5 * H, status="SHIPPED", shipped_at=now - 6 * H)  # on time
    f.order(deadline=now - 2 * H, status="CANCELLED")  # cancelled: never delayed
    f.order(deadline=now + 1 * H)  # not due yet
    for order in (open_late, shipped_late, shipped_late_yesterday):
        f.line(order, product, 3)
    start_of_today = datetime.combine(now.astimezone(ZoneInfo("Asia/Colombo")).date(), datetime.min.time(),
                                      ZoneInfo("Asia/Colombo"))  # fmt: skip
    today = call(f, "SELECT * FROM sem.delayed_orders(%s, %s, %s, %s)", start_of_today)
    assert [(r["order_id"], r["status"], r["hours_late"], r["lines"]) for r in today] == [
        (open_late, "OPEN", Decimal("1.0"), 1),  # open first
        (shipped_late, "SHIPPED", Decimal("2.0"), 1),  # shipped 2 hours after its deadline
    ]
    since_two_days = call(f, "SELECT * FROM sem.delayed_orders(%s, %s, %s, %s)", now - 48 * H)
    assert shipped_late_yesterday in {r["order_id"] for r in since_two_days}


# ---------------------------------------------------------------- "today" (daily brief, step 5)


def test_today_means_before_midnight_site_time_with_overdue_orders(connect_as: Connect) -> None:
    """The exact site-local day (product owner review of step 5): an order due at 23:59 is today's,
    one due at 00:00 is tomorrow's, an overdue one is still at risk; the cutoff follows the site's
    time zone, not UTC."""
    from palletwise.semantic import rules

    f = FixtureSite(connect_as)
    now = f.as_of()
    zone = f.db.execute("SELECT time_zone FROM pw.site WHERE id = %s", (f.site,)).fetchone()
    assert zone is not None
    tz = ZoneInfo(zone[0])
    midnight = datetime.combine(now.astimezone(tz).date() + timedelta(days=1), datetime.min.time(), tz)
    assert midnight.utcoffset() != timedelta(0), "the fixture site is not on UTC"
    pick = f.zone("P", zone_type="FAST_PICK", prime=True)
    product = f.product("T")
    f.stock(f.bin(pick, "P-T"), product, 0)  # nothing on the pick face: every due line is at risk
    due = {
        "late_evening": f.order(deadline=midnight - timedelta(minutes=1)),  # 23:59 today
        "midnight": f.order(deadline=midnight),  # 00:00: tomorrow
        "just_before": f.order(deadline=midnight - timedelta(microseconds=1)),  # still today
        "overdue": f.order(deadline=now - H),
    }
    for order in due.values():
        f.line(order, product, 3)
    with tenant_session(role_pool("PW_AGENT_READ"), f.tenant, read_snapshot=True) as conn:
        today = rules.order_stockout_risks(conn, f.site, "orders", None, today=True)
        window = rules.order_stockout_risks(conn, f.site, "orders", 48)

    def orders(result: Any) -> set[UUID]:
        return {e.id for e in result.entities if e.type == "order"}

    assert orders(today) == {due["late_evening"], due["just_before"], due["overdue"]}
    assert today.row_count == 3
    assert due["midnight"] in orders(window)  # due, just not today
    assert today.normalized_request == {
        "tool": "get_order_stockout_risks", "view": "orders", "hours": None, "today": True,
    }  # fmt: skip
    assert "before midnight, site time" in today.evidence.how_computed[0]
