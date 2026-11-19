"""Stock and capacity rules (semantic spec 4.1, 4.4 and 6; Slice 2 step 1b).

Every expected number is worked out by hand from the fixture rows, in the comments next to it.
"""

from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal

import psycopg
import pytest
from fixture_site import FixtureSite

pytestmark = pytest.mark.integration

Connect = Callable[[str], psycopg.Connection]


@pytest.fixture
def capacity_site(connect_as: Connect) -> FixtureSite:
    """Zone A: two bins, one over capacity. Zone B: one bin at 92 percent. Zone C: half full.
    Zone D: active, but its only bin is inactive."""
    f = FixtureSite(connect_as)
    a, b, c, d = f.zone("A"), f.zone("B"), f.zone("C"), f.zone("D")
    light = f.product("LIGHT", volume_m3=0.01, weight_kg=2.0, value=50)  # bulky by weight
    bulky = f.product("BULKY", volume_m3=0.05, weight_kg=0.5, value=200)  # bulky by volume
    a1 = f.bin(a, "A-01")  # capacity 1.0 m3, 100 kg
    a2 = f.bin(a, "A-02")
    b1 = f.bin(b, "B-01")
    c1 = f.bin(c, "C-01")
    d1 = f.bin(d, "D-01", active=False)
    f.stock(a1, light, 30)  # 0.30 m3 = 30%; 60 kg = 60%      -> 60.0
    f.stock(a2, bulky, 25)  # 1.25 m3 = 125%; 12.5 kg = 12.5% -> 125.0, over capacity
    f.stock(b1, light, 46)  # 0.46 m3 = 46%; 92 kg = 92%      -> 92.0
    f.stock(c1, bulky, 10)  # 0.50 m3 = 50%; 5 kg = 5%        -> 50.0
    f.stock(d1, light, 5)  # inactive bin: never counted
    return f


def by(rows: list[dict[str, object]], key: str) -> dict[object, dict[str, object]]:
    return {r[key]: r for r in rows}


# ---------------------------------------------------------------- capacity (4.4)


def test_bin_utilization_is_the_larger_of_volume_and_weight(capacity_site: FixtureSite) -> None:
    bins = by(capacity_site.rows("bin_utilization"), "bin_code")
    assert set(bins) == {"A-01", "A-02", "B-01", "C-01"}  # the inactive D-01 is left out
    assert (bins["A-01"]["utilization_pct"], bins["A-01"]["over_capacity"]) == (Decimal("60.0"), False)
    assert (bins["A-02"]["utilization_pct"], bins["A-02"]["over_capacity"]) == (Decimal("125.0"), True)
    assert bins["B-01"]["utilization_pct"] == Decimal("92.0")
    assert bins["C-01"]["utilization_pct"] == Decimal("50.0")


def test_zone_utilization_is_a_ratio_of_sums_and_null_without_active_bins(capacity_site: FixtureSite) -> None:
    zones = {r["zone_code"]: r for r in capacity_site.rows("zone_summary")}
    # Zone A: volume (0.30 + 1.25) / 2.0 = 77.5%; weight (60 + 12.5) / 200 = 36.25% -> 77.5.
    # Not the average of 60 and 125 (92.5), which would overstate a half-empty zone.
    assert zones["A"]["utilization_pct"] == Decimal("77.5")
    assert (zones["A"]["active_bins"], zones["A"]["over_capacity_bins"]) == (2, 1)
    assert zones["D"]["utilization_pct"] is None and zones["D"]["active_bins"] == 0


def test_capacity_hotspots_follow_the_threshold_or_an_over_capacity_bin(capacity_site: FixtureSite) -> None:
    hot = by(capacity_site.rows("capacity_hotspots"), "zone_code")
    # A: 77.5 < 90 but has an over-capacity bin. B: 92 >= 90. C: 50, no bin over. D: no storage.
    assert list(hot) == ["B", "A"]  # highest utilization first
    assert hot["B"]["threshold_pct"] == Decimal(90)


def test_a_new_settings_version_changes_hotspots_and_the_old_version_still_answers(
    capacity_site: FixtureSite,
) -> None:
    old = capacity_site.settings()
    new = capacity_site.new_settings(capacity_threshold_pct=95)
    assert [r["zone_code"] for r in capacity_site.rows("capacity_hotspots", new)] == ["A"]  # B (92) drops out
    assert [r["zone_code"] for r in capacity_site.rows("capacity_hotspots", old)] == ["B", "A"]


# ---------------------------------------------------------------- LOW_STOCK (6, D2)


def test_low_stock_is_available_in_the_pick_bin_at_or_below_min_qty(connect_as: Connect) -> None:
    f = FixtureSite(connect_as)
    zone = f.zone("P", zone_type="FAST_PICK", prime=True)
    below, equal, above, empty, no_policy = (
        f.product(s) for s in ("BELOW", "EQUAL", "ABOVE", "EMPTY", "NOPOL")
    )
    pick = {p: f.bin(zone, f"P-{i:02d}") for i, p in enumerate((below, equal, above, empty, no_policy))}
    reserve = f.bin(f.zone("R"), "R-01", role="RESERVE")
    f.stock(pick[below], below, 30, allocated=22)  # available 8 <= 10: low
    f.stock(pick[equal], equal, 10)  # available 10 <= 10: low (at the minimum)
    f.stock(pick[above], above, 11)  # available 11 > 10: fine
    f.stock(reserve, empty, 500)  # plenty in reserve, nothing in the pick bin: still low
    f.stock(pick[no_policy], no_policy, 1)  # no policy: never low stock
    for product in (below, equal, above, empty):
        f.policy(product, pick[product], 10, 50)
    low = f.rows("low_stock")
    assert [(r["sku"], r["available"], r["min_qty"]) for r in low] == [
        ("EMPTY", 0, 10),  # ordered by how far below the minimum: 0 - 10 = -10
        ("BELOW", 8, 10),  # -2
        ("EQUAL", 10, 10),  # 0
    ]
    assert low[1]["on_hand"] == 30 and low[1]["allocated"] == 22


# ---------------------------------------------------------------- zone summary (8)


def test_zone_summary_sums_stock_and_value_by_zone_largest_first(capacity_site: FixtureSite) -> None:
    rows = capacity_site.rows("zone_summary")
    # On hand: A 30 + 25 = 55; B 46; C 10; D 0 (its stock sits in an inactive bin, but stock
    # in a zone is what the balances say, so D-01's 5 units still count for D).
    assert [(r["zone_code"], r["on_hand"]) for r in rows] == [("A", 55), ("B", 46), ("C", 10), ("D", 5)]
    zones = by(rows, "zone_code")
    assert zones["A"]["stock_value"] == Decimal(30 * 50 + 25 * 200)  # 6,500
    assert zones["A"]["products"] == 2 and zones["B"]["products"] == 1


# ---------------------------------------------------------------- inputs and isolation


def test_rules_refuse_a_stale_as_of(capacity_site: FixtureSite) -> None:
    stale = capacity_site.as_of() - timedelta(minutes=15)
    for function in ("bin_utilization", "low_stock", "zone_summary", "capacity_hotspots"):
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            capacity_site.rows(function, as_of=stale)


def test_another_tenant_sees_none_of_the_sites_rules(capacity_site: FixtureSite, connect_as: Connect) -> None:
    other = FixtureSite(connect_as)
    other.site = capacity_site.site  # ask about a site of another tenant
    with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
        other.rows("zone_summary", other.settings(), as_of=capacity_site.as_of())
