"""Simulator unit tests (simulator spec 9): no database needed."""

import collections
import hashlib
import itertools
import json
from datetime import datetime, timedelta
from functools import cache
from zoneinfo import ZoneInfo

from pw_simulator.catalogue import INJECTION_SKU, WIRELESS_MOUSE_SKU, build_catalogue
from pw_simulator.history import OutRecord, RunParams, Simulation, generate
from pw_simulator.layout import build_layout
from pw_simulator.reference import load_calendar, load_outlets

COLOMBO = ZoneInfo("Asia/Colombo")
CLOCK_START = datetime(2026, 4, 1, 6, 0, tzinfo=COLOMBO)


@cache
def full_run() -> tuple[OutRecord, ...]:
    return tuple(generate(RunParams("run-full", 42, "sim-v1", CLOCK_START)))


def digest(records: list[OutRecord] | tuple[OutRecord, ...]) -> str:
    h = hashlib.sha256()
    for r in records:
        h.update(
            json.dumps([r.seq, r.record_type, r.sim_time.isoformat(), r.payload], sort_keys=True).encode()
        )
    return h.hexdigest()


def test_layout_matches_spec() -> None:
    layout = build_layout()
    assert len(layout.zones) == 6
    assert len(layout.racks) == 26
    assert len(layout.bins) == 676
    roles = collections.Counter(b.bin_role for b in layout.bins)
    assert roles["PICK"] == 192 + 84 + 24
    assert len({b.code for b in layout.bins}) == 676
    assert "B2-03-02" in {b.code for b in layout.bins}


def test_racks_do_not_overlap() -> None:
    racks = build_layout().racks
    boxes = [
        (r.origin_x, r.origin_y, r.origin_x + r.bay_count * r.bay_width_m, r.origin_y + r.depth_m)
        for r in racks
    ]
    for i, a in enumerate(boxes):
        for b in boxes[i + 1 :]:
            assert a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1], (a, b)


def test_catalogue_anchors() -> None:
    products = {p.sku: p for p in build_catalogue(42)}
    assert len(products) == 300
    assert products[WIRELESS_MOUSE_SKU].name == "Wireless Mouse"
    assert "Ignore previous instructions" in products[INJECTION_SKU].name
    assert sum(1 for p in products.values() if p.temperature_class == "CHILLED") == 24


def test_same_seed_same_records_different_seed_different_records() -> None:
    short = RunParams("r", 42, "sim-v1", CLOCK_START, history_days=21)
    assert digest(generate(short)) == digest(generate(short))
    other = RunParams("r", 43, "sim-v1", CLOCK_START, history_days=21)
    assert digest(generate(short)) != digest(generate(other))


def test_volumes_within_acceptance_ranges() -> None:
    counts = collections.Counter(r.record_type for r in full_run())
    assert 7_500 <= counts["ORDER"] <= 9_000
    assert 35_000 <= counts["ORDER_LINE"] <= 55_000
    assert 35_000 <= counts["ALLOCATION"] <= 55_000
    assert 80_000 <= counts["MOVEMENT"] <= 130_000


def test_only_peliyagoda_outlets_order() -> None:
    peliyagoda = {o.code for o in load_outlets() if o.depot == "Peliyagoda"}
    customers = {r.payload["customer"] for r in full_run() if r.record_type == "ORDER"}
    assert customers == peliyagoda
    assert len(customers) == 75


def test_no_deliveries_on_non_operating_days() -> None:
    calendar = load_calendar()
    for r in full_run():
        if r.record_type == "ORDER":
            day = datetime.fromisoformat(r.payload["delivery_date"]).date()
            assert calendar[day].is_operating, day


def test_records_are_ordered_and_clock_markers_never_lead() -> None:
    records = full_run()
    assert [r.seq for r in records] == list(range(1, len(records) + 1))
    assert all(a.sim_time <= b.sim_time for a, b in itertools.pairwise(records))
    last_tick = None
    for r in records:
        if r.record_type == "TICK":
            last_tick = r.sim_time
        elif last_tick is not None and r.record_type != "LOAD_COMPLETE":
            assert r.sim_time > last_tick, "an event appears after the tick that covers its time"
    assert records[-1].record_type == "LOAD_COMPLETE"
    assert records[-1].sim_time == CLOCK_START


def test_parents_precede_children() -> None:
    seen_orders: set[str] = set()
    seen_lines: set[str] = set()
    seen_allocations: set[str] = set()
    for r in full_run():
        p = r.payload
        if r.record_type == "ORDER":
            seen_orders.add(p["order_no"])
        elif r.record_type == "ORDER_LINE":
            assert p["order_no"] in seen_orders
            seen_lines.add(p["external_id"])
        elif r.record_type == "ALLOCATION":
            assert p["order_line"] in seen_lines
            seen_allocations.add(p["order_line"])
        elif r.record_type == "MOVEMENT" and p["movement_type"] == "PICK":
            assert p["order_line"] in seen_allocations


def test_hidden_parameters_never_emitted() -> None:
    text = json.dumps([r.payload for r in full_run()])
    assert "uplift" not in text


def test_dead_stock_anchors_have_no_recent_picks() -> None:
    sim = Simulation(RunParams("r", 42, "sim-v1", CLOCK_START))
    records = sim.run()
    assert len(sim.dead_skus) == 6
    assert all(sim.policies[s].pick_bin.startswith("A") for s in sim.dead_skus)
    cutoff = CLOCK_START - timedelta(days=30)
    for r in records:
        if r.record_type == "MOVEMENT" and r.payload["movement_type"] == "PICK" and r.sim_time > cutoff:
            assert r.payload["sku"] not in sim.dead_skus
