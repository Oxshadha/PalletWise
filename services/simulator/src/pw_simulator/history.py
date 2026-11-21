"""History generation (simulator spec 4, 5): a discrete-event simulation of the warehouse.

Every state change happens at its event time, in time order, so the emitted records are
causally and chronologically consistent. Randomness comes from independent streams per
subsystem and per simulated day, so a day's events depend only on the seed, the calendar and
the state at that day's start (simulator spec 5.2, 5.3).
"""

import heapq
import math
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from pw_simulator.catalogue import CATEGORIES, Product, build_catalogue, true_uplift
from pw_simulator.layout import Bin, Layout, build_layout
from pw_simulator.reference import Day, Outlet, load_calendar, load_outlets

STREAM_DEMAND = 3
STREAM_ORDERING = 4
STREAM_OPERATIONS = 5

SITE_DEPOT = "Peliyagoda"
TICK = timedelta(minutes=15)
DISPATCH_LEAD = timedelta(minutes=120)
AUTO_REPLEN_LAG = timedelta(minutes=60)
ORDER_PROBABILITY = {"Fresh": 0.9, "Style": 0.4, "Tech": 0.3}
WEEKDAY = (1.0, 0.95, 0.95, 1.0, 1.1, 1.2, 1.0)  # Monday..Sunday
AVG_LINE_QTY = 3.5
LATE_SHARE = 0.02
DEAD_STOCK_DAYS = 45
DEAD_STOCK_COUNT = 6

# Same-time ordering: parents before children (simulator spec 2.1).
PRIORITY = {
    "RUN_STARTED": 0,
    "CATEGORY": 1,
    "PRODUCT": 2,
    "CUSTOMER": 3,
    "ZONE": 4,
    "RACK": 5,
    "BIN": 6,
    "SITE_FEATURE": 7,
    "REPLENISHMENT_POLICY": 8,
    "ORDER": 10,
    "ORDER_LINE": 11,
    "ALLOCATION": 20,
    "ALLOCATION_RELEASE": 21,
    "MOVEMENT": 30,
    "ORDER_STATUS": 40,
}


@dataclass(frozen=True, slots=True)
class Input:
    """One entry of the run's input log (simulator spec 5.3, 6): applied at `at`, after the
    period already emitted, and replayed whenever the run is regenerated."""

    at: datetime
    type: str  # set_param, create_order, delay_inbound, demand_shock (spec 6, a closed list)
    args: dict[str, Any]


INPUT_TYPES = ("set_param", "create_order", "delay_inbound", "demand_shock")
PARAMS = ("replenishment_lag_minutes", "lead_time_multiplier", "receiving_capacity", "putaway_delay_minutes")


@dataclass(frozen=True, slots=True)
class RunParams:
    run_id: str
    seed: int
    dataset_version: str
    clock_start: datetime
    history_days: int = 180
    time_zone: str = "Asia/Colombo"
    inputs: tuple[Input, ...] = ()


@dataclass(frozen=True, slots=True)
class OutRecord:
    seq: int
    record_type: str
    sim_time: datetime
    payload: dict[str, Any]


@dataclass(slots=True)
class _Line:
    external_id: str
    line_no: int
    sku: str
    qty: int
    allocated: int = 0
    picked: int = 0


@dataclass(slots=True)
class _Order:
    order_no: str
    customer: str
    delivery: date
    deadline: datetime
    ship_time: datetime
    lane: str
    lines: list[_Line] = field(default_factory=list[_Line])


@dataclass(frozen=True, slots=True)
class Policy:
    pick_bin: str
    min_qty: int
    max_qty: int


class Simulation:
    def __init__(
        self, params: RunParams, calendar: dict[date, Day] | None = None, outlets: list[Outlet] | None = None
    ) -> None:
        self.p = params
        self.tz = ZoneInfo(params.time_zone)
        self.calendar = calendar or load_calendar()
        self.outlets = outlets or load_outlets()
        self.layout: Layout = build_layout()
        self.products = build_catalogue(params.seed)
        self.by_sku = {pr.sku: pr for pr in self.products}
        self.bins = {b.code: b for b in self.layout.bins}
        self.history_start = params.clock_start - timedelta(days=params.history_days)

        self.records: list[tuple[datetime, int, int, str, dict[str, Any]]] = []
        self._serial = 0
        self._movement_no = 0
        self._events: list[tuple[datetime, int, int, Callable[[], None]]] = []
        self._event_serial = 0

        self.on_hand: dict[tuple[str, str], int] = defaultdict(int)
        self.allocated: dict[tuple[str, str], int] = defaultdict(int)
        self.on_order: dict[str, int] = defaultdict(int)
        self.pending_replen: set[str] = set()
        self.sku_bins: dict[str, set[str]] = defaultdict(set)  # bins holding stock of a sku
        self.bin_volume: dict[str, float] = defaultdict(float)  # used volume per bin
        self._receiving_rr = 0
        # Operating parameters a scenario may change (spec 6 set_param); the defaults are the
        # history's behaviour, so a run without inputs is unchanged.
        self.replen_lag = AUTO_REPLEN_LAG
        self.lead_time_multiplier = 1.0
        self.receiving_capacity = 1.0
        self.putaway_delay = timedelta(0)
        self.shocks: list[tuple[date, date, set[str], float]] = []
        self._staged = 0

        self.policies: dict[str, Policy] = {}
        self.dead_skus: set[str] = set()
        self.dead_from = (params.clock_start - timedelta(days=DEAD_STOCK_DAYS)).date()
        self._arrivals: dict[date, list[tuple[str, int]]] = defaultdict(list)
        self.receiving_bins = sorted(b.code for b in self.layout.bins if b.bin_role == "RECEIVING")
        self._now = self.history_start
        # Reserve and bulk bins per temperature class, in layout order (putaway candidates).
        self.storage_bins: dict[str, list[str]] = defaultdict(list)
        for b in self.layout.bins:
            if b.bin_role in ("RESERVE", "BULK"):
                self.storage_bins[b.temperature_class].append(b.code)

    # ------------------------------------------------------------------ emission

    def emit(self, at: datetime, record_type: str, payload: dict[str, Any]) -> None:
        self._serial += 1
        self.records.append((at, PRIORITY[record_type], self._serial, record_type, payload))

    def schedule(self, at: datetime, action: Callable[[], None], order: int = 50) -> None:
        self._event_serial += 1
        heapq.heappush(self._events, (at, order, self._event_serial, action))

    def local(self, d: date, t: time) -> datetime:
        return datetime.combine(d, t, tzinfo=self.tz)

    # ------------------------------------------------------------------ stock helpers

    def _unit_cap(self, b: Bin, pr: Product) -> int:
        return math.floor(
            0.9 * min(b.capacity_volume_m3 / pr.unit_volume_m3, b.capacity_weight_kg / pr.unit_weight_kg)
        )

    def _bin_free_volume(self, code: str) -> float:
        return self.bins[code].capacity_volume_m3 * 0.9 - self.bin_volume[code]

    def move(
        self,
        at: datetime,
        mtype: str,
        sku: str,
        qty: int,
        from_bin: str | None,
        to_bin: str | None,
        order_line: str | None = None,
        reason: str | None = None,
    ) -> None:
        if qty <= 0:
            return
        if from_bin is not None:
            key = (from_bin, sku)
            if self.on_hand[key] < qty:
                raise AssertionError(f"negative stock {sku} in {from_bin}")
            self.on_hand[key] -= qty
            self.bin_volume[from_bin] -= qty * self.by_sku[sku].unit_volume_m3
            if self.on_hand[key] == 0:
                self.sku_bins[sku].discard(from_bin)
        if to_bin is not None:
            self.on_hand[(to_bin, sku)] += qty
            self.bin_volume[to_bin] += qty * self.by_sku[sku].unit_volume_m3
            self.sku_bins[sku].add(to_bin)
        self._movement_no += 1
        self.emit(
            at,
            "MOVEMENT",
            {
                "external_id": f"MV-{self._movement_no:07d}",
                "movement_type": mtype,
                "sku": sku,
                "qty": qty,
                "from_bin": from_bin,
                "to_bin": to_bin,
                "order_line": order_line,
                "reason_code": reason,
            },
        )

    def storable_on_hand(self, sku: str) -> int:
        return sum(
            self.on_hand[(b, sku)]
            for b in self.sku_bins[sku]
            if self.bins[b].bin_role in ("PICK", "RESERVE", "BULK")
        )

    def reserve_bins_with(self, sku: str) -> list[str]:
        held = [b for b in self.sku_bins[sku] if self.bins[b].bin_role in ("RESERVE", "BULK")]
        return sorted(held, key=lambda b: (-self.on_hand[(b, sku)], b))

    def putaway_bin(self, pr: Product, qty: int) -> str:
        temp = pr.temperature_class
        candidates = [
            b for b in self.layout.bins if b.bin_role in ("RESERVE", "BULK") and b.temperature_class == temp
        ]
        need = qty * pr.unit_volume_m3
        holding = [b.code for b in candidates if b.code in self.sku_bins[pr.sku]]
        for code in sorted(holding) + [b.code for b in candidates]:
            if self._bin_free_volume(code) >= need:
                return code
        return candidates[0].code  # overflow: the simulator never refuses a receipt

    # ------------------------------------------------------------------ setup

    def slot_and_open(self) -> None:
        at = self.history_start
        self.emit(
            at,
            "RUN_STARTED",
            {
                "seed": self.p.seed,
                "dataset_version": self.p.dataset_version,
                "history_start": at.isoformat(),
                "clock_start": self.p.clock_start.isoformat(),
            },
        )
        for c in CATEGORIES:
            self.emit(at, "CATEGORY", {"code": c.code, "name": c.name})
        for pr in self.products:
            self.emit(
                at,
                "PRODUCT",
                {
                    "sku": pr.sku,
                    "name": pr.name,
                    "category": pr.category,
                    "brand": pr.brand,
                    "uom": pr.uom,
                    "unit_volume_m3": pr.unit_volume_m3,
                    "unit_weight_kg": pr.unit_weight_kg,
                    "unit_value": pr.unit_value,
                    "temperature_class": pr.temperature_class,
                },
            )
        for o in self.outlets:
            self.emit(
                at,
                "CUSTOMER",
                {
                    "code": o.code,
                    "brand": o.brand,
                    "district": o.district,
                    "dock_type": o.dock_type,
                    "parking_constraint": o.parking_constraint,
                    "window_open": o.window_open.isoformat(),
                    "window_close": o.window_close.isoformat(),
                },
            )
        for z in self.layout.zones:
            self.emit(
                at,
                "ZONE",
                {
                    "code": z.code,
                    "name": z.name,
                    "zone_type": z.zone_type,
                    "temperature_class": z.temperature_class,
                    "outline": z.outline,
                    "is_prime": z.is_prime,
                },
            )
        for r in self.layout.racks:
            self.emit(
                at,
                "RACK",
                {
                    "code": r.code,
                    "zone": r.zone,
                    "origin_x": r.origin_x,
                    "origin_y": r.origin_y,
                    "rotation_deg": r.rotation_deg,
                    "bay_count": r.bay_count,
                    "level_count": r.level_count,
                    "bay_width_m": r.bay_width_m,
                    "depth_m": r.depth_m,
                    "level_height_m": r.level_height_m,
                },
            )
        for b in self.layout.bins:
            self.emit(
                at,
                "BIN",
                {
                    "code": b.code,
                    "zone": b.zone,
                    "rack": b.rack,
                    "bay": b.bay,
                    "level": b.level,
                    "bin_role": b.bin_role,
                    "x": b.x,
                    "y": b.y,
                    "z": b.z,
                    "width_m": b.width_m,
                    "depth_m": b.depth_m,
                    "height_m": b.height_m,
                    "capacity_volume_m3": b.capacity_volume_m3,
                    "capacity_weight_kg": b.capacity_weight_kg,
                    "single_sku": b.single_sku,
                },
            )
        for f in self.layout.features:
            self.emit(
                at,
                "SITE_FEATURE",
                {"code": f.code, "feature_type": f.feature_type, "label": f.label, "outline": f.outline},
            )

        # Slotting (simulator spec 3.4): fastest ambient products to zone A.
        pick_a = sorted(b.code for b in self.layout.bins if b.zone == "A")
        pick_b = sorted(b.code for b in self.layout.bins if b.zone == "B" and b.bin_role == "PICK")
        pick_d = sorted(b.code for b in self.layout.bins if b.zone == "D" and b.bin_role == "PICK")
        ambient = sorted(
            (pr for pr in self.products if pr.temperature_class == "AMBIENT"),
            key=lambda pr: (-pr.base_demand, pr.sku),
        )
        chilled = sorted(
            (pr for pr in self.products if pr.temperature_class == "CHILLED"),
            key=lambda pr: (-pr.base_demand, pr.sku),
        )
        assignment = list(zip(ambient, pick_a + pick_b, strict=True))
        assignment += list(zip(chilled, pick_d, strict=True))
        # Six mid-velocity zone A products whose demand collapses (dead stock in prime).
        zone_a_products = [pr.sku for pr, b in assignment if b.startswith("A")]
        self.dead_skus = set(zone_a_products[150 : 150 + DEAD_STOCK_COUNT])

        for pr, code in assignment:
            daily = pr.base_demand
            cap = self._unit_cap(self.bins[code], pr)
            max_qty = max(10, min(cap, math.ceil(4 * daily) + 5))
            min_qty = max(2, min(max_qty - 1, math.ceil(1.5 * daily)))
            self.policies[pr.sku] = Policy(code, min_qty, max_qty)
            self.emit(
                at,
                "REPLENISHMENT_POLICY",
                {"sku": pr.sku, "pick_bin": code, "min_qty": min_qty, "max_qty": max_qty},
            )

        # Opening balances are ledger entries, so the history is complete (CWM 8.2).
        for pr in self.products:
            pol = self.policies[pr.sku]
            self.move(at, "ADJUSTMENT", pr.sku, pol.max_qty, None, pol.pick_bin, reason="OPENING_BALANCE")
            reserve_qty = max(20, math.ceil(10 * pr.base_demand))
            self.move(
                at,
                "ADJUSTMENT",
                pr.sku,
                reserve_qty,
                None,
                self.putaway_bin(pr, reserve_qty),
                reason="OPENING_BALANCE",
            )

    # ------------------------------------------------------------------ demand

    def next_operating_day(self, d: date) -> date | None:
        nxt = d + timedelta(days=1)
        while nxt in self.calendar:
            if self.calendar[nxt].is_operating:
                return nxt
            nxt += timedelta(days=1)
        return None

    def expected_demand(self, pr: Product, delivery: date) -> float:
        day = self.calendar[delivery]
        if not day.is_operating or (pr.sku in self.dead_skus and delivery >= self.dead_from):
            return 0.0
        factor = WEEKDAY[day.dow]
        if pr.brand == "Fresh" and (
            day.is_payday or self.calendar.get(delivery - timedelta(days=1), day).is_payday
        ):
            factor *= 1.15
        factor *= 1 + true_uplift(pr.category) * day.festival_ramp
        if pr.brand == "Style" and day.is_monsoon:
            factor *= 0.9
        for first, last, skus, multiplier in self.shocks:
            if first <= delivery <= last and pr.sku in skus:
                factor *= multiplier
        return pr.base_demand * factor

    # ------------------------------------------------------------------ daily phases

    def day_start(self, d: date) -> None:
        day = self.calendar.get(d)
        if day is None or not day.is_operating:
            return
        self.schedule(self.local(d, time(8, 0)), lambda: self.create_orders(d), order=10)
        self.schedule(self.local(d, time(10, 0)), lambda: self.receive(d), order=20)
        self.schedule(self.local(d, time(11, 0)), lambda: self.purchase(d), order=30)

    def create_orders(self, d: date) -> None:
        delivery = self.next_operating_day(d)
        if delivery is None:
            return
        ordinal = d.toordinal()
        rng_order = np.random.default_rng([self.p.seed, STREAM_ORDERING, ordinal])
        rng_demand = np.random.default_rng([self.p.seed, STREAM_DEMAND, ordinal])
        orders: dict[str, _Order] = {}
        by_brand: dict[str, list[Outlet]] = defaultdict(list)
        for o in self.outlets:
            if o.depot == SITE_DEPOT and rng_order.random() < ORDER_PROBABILITY[o.brand]:
                by_brand[o.brand].append(o)

        for pr in self.products:
            ordering = by_brand.get(pr.brand, [])
            units = int(rng_demand.poisson(self.expected_demand(pr, delivery)))
            if units == 0 or not ordering:
                continue
            n_lines = min(len(ordering), max(1, round(units / AVG_LINE_QTY)))
            n_lines = min(n_lines, units)
            chosen = rng_order.choice(len(ordering), size=n_lines, replace=False)
            split = rng_order.multinomial(units - n_lines, [1.0 / n_lines] * n_lines) + 1
            for idx, qty in zip(sorted(int(c) for c in chosen), split, strict=True):
                outlet = ordering[idx]
                order = orders.get(outlet.code)
                if order is None:
                    deadline = self.local(delivery, outlet.window_open) - DISPATCH_LEAD
                    ship = deadline - timedelta(minutes=30)
                    if rng_order.random() < LATE_SHARE:
                        ship = deadline + timedelta(minutes=int(rng_order.integers(15, 91)))
                    lane = f"S-L{(int(outlet.code[3:]) % 20) + 1:02d}"
                    order = _Order(
                        f"SO-{delivery:%Y%m%d}-{outlet.code}", outlet.code, delivery, deadline, ship, lane
                    )
                    orders[outlet.code] = order
                line_no = len(order.lines) + 1
                order.lines.append(_Line(f"{order.order_no}-{line_no:02d}", line_no, pr.sku, int(qty)))

        for code in sorted(orders):
            order = orders[code]
            created = self.local(d, time(8, 0)) + timedelta(minutes=int(rng_order.integers(0, 8 * 60)))
            self.emit(
                created,
                "ORDER",
                {
                    "order_no": order.order_no,
                    "customer": order.customer,
                    "delivery_date": order.delivery.isoformat(),
                    "dispatch_deadline": order.deadline.isoformat(),
                },
            )
            for line in order.lines:
                self.emit(
                    created,
                    "ORDER_LINE",
                    {
                        "external_id": line.external_id,
                        "order_no": order.order_no,
                        "line_no": line.line_no,
                        "sku": line.sku,
                        "qty_ordered": line.qty,
                    },
                )
        todays = [orders[c] for c in sorted(orders)]
        self.schedule(self.local(d, time(17, 0)), lambda: self.wave_replenish(todays), order=40)
        self.schedule(self.local(d, time(18, 0)), lambda: self.allocate(d, todays), order=50)

    def replenish(self, at: datetime, sku: str, wanted: int) -> None:
        pol = self.policies[sku]
        cap = self._unit_cap(self.bins[pol.pick_bin], self.by_sku[sku])
        room = cap - self.on_hand[(pol.pick_bin, sku)]
        remaining = min(wanted, room)
        for src in self.reserve_bins_with(sku):
            if remaining <= 0:
                break
            qty = min(remaining, self.on_hand[(src, sku)])
            self.move(at, "REPLENISH", sku, qty, src, pol.pick_bin)
            remaining -= qty

    def wave_replenish(self, orders: list[_Order]) -> None:
        """17:00: make sure tomorrow's demand can be allocated from the pick faces."""
        required: dict[str, int] = defaultdict(int)
        for o in orders:
            for line in o.lines:
                required[line.sku] += line.qty
        for i, sku in enumerate(sorted(required)):
            pol = self.policies[sku]
            key = (pol.pick_bin, sku)
            available = self.on_hand[key] - self.allocated[key]
            if available < required[sku] or available < pol.min_qty:
                target = max(pol.max_qty, required[sku])
                self.replenish(self._now + timedelta(seconds=i), sku, target - available)

    def allocate(self, d: date, orders: list[_Order], at: datetime | None = None) -> None:
        at = at or self.local(d, time(18, 0))
        picks_from = (
            self.local(d, time(19, 0)) if at == self.local(d, time(18, 0)) else at + timedelta(minutes=15)
        )
        for order in sorted(orders, key=lambda o: (o.deadline, o.order_no)):
            for line in order.lines:
                pol = self.policies[line.sku]
                key = (pol.pick_bin, line.sku)
                qty = min(line.qty, self.on_hand[key] - self.allocated[key])
                if qty <= 0:
                    continue
                self.allocated[key] += qty
                line.allocated = qty
                self.emit(
                    at,
                    "ALLOCATION",
                    {
                        "external_id": f"AL-{line.external_id}",
                        "order_line": line.external_id,
                        "sku": line.sku,
                        "bin": pol.pick_bin,
                        "qty": qty,
                    },
                )
            self.emit(
                at, "ORDER_STATUS", {"order_no": order.order_no, "status": "ALLOCATED", "shipped_at": None}
            )
            pick_end = order.ship_time - timedelta(minutes=30)
            pick_start = max(picks_from, pick_end - timedelta(hours=4))
            picks = [ln for ln in order.lines if ln.allocated > 0]
            step = (pick_end - pick_start) / max(1, len(picks))
            for i, line in enumerate(picks):
                t = pick_start + step * i
                self.schedule(t, lambda o=order, ln=line, t=t: self.pick(o, ln, t), order=60)
            if picks:
                picked_at = pick_start + step * len(picks)
                self.schedule(
                    picked_at,
                    lambda o=order, t=picked_at: self.emit(
                        t, "ORDER_STATUS", {"order_no": o.order_no, "status": "PICKED", "shipped_at": None}
                    ),
                    order=61,
                )
            self.schedule(order.ship_time, lambda o=order: self.ship(o), order=70)

    def pick(self, order: _Order, line: _Line, at: datetime) -> None:
        pol = self.policies[line.sku]
        key = (pol.pick_bin, line.sku)
        self.allocated[key] -= line.allocated
        self.move(at, "PICK", line.sku, line.allocated, pol.pick_bin, order.lane, order_line=line.external_id)
        line.picked = line.allocated
        if self.on_hand[key] - self.allocated[key] < pol.min_qty and pol.pick_bin not in self.pending_replen:
            self.pending_replen.add(pol.pick_bin)
            due = at + self.replen_lag
            self.schedule(due, lambda s=line.sku, t=due: self.auto_replenish(s, t), order=65)

    def auto_replenish(self, sku: str, at: datetime) -> None:
        pol = self.policies[sku]
        self.pending_replen.discard(pol.pick_bin)
        self.replenish(at, sku, pol.max_qty - self.on_hand[(pol.pick_bin, sku)])

    def ship(self, order: _Order) -> None:
        at = order.ship_time
        for line in order.lines:
            self.move(at, "SHIP", line.sku, line.picked, order.lane, None, order_line=line.external_id)
        self.emit(
            at,
            "ORDER_STATUS",
            {"order_no": order.order_no, "status": "SHIPPED", "shipped_at": at.isoformat()},
        )

    def receive(self, d: date) -> None:
        arrivals = self._arrivals.pop(d, [])
        if self.receiving_capacity < 1.0 and arrivals:
            # Less receiving capacity: the rest waits for the next operating day.
            keep = max(1, math.ceil(len(arrivals) * self.receiving_capacity))
            later = self.next_operating_day(d)
            if later is not None:
                self._arrivals[later] = arrivals[keep:] + self._arrivals[later]
                arrivals = arrivals[:keep]
        rng = np.random.default_rng([self.p.seed, STREAM_OPERATIONS, d.toordinal()])
        for i, (sku, qty) in enumerate(arrivals):
            at = self.local(d, time(10, 0)) + timedelta(minutes=i)
            dock = self.receiving_bins[self._receiving_rr % len(self.receiving_bins)]
            self._receiving_rr += 1
            self.on_order[sku] -= qty
            self.move(at, "RECEIPT", sku, qty, None, dock)
            due = at + timedelta(minutes=int(rng.integers(60, 241))) + self.putaway_delay
            self.schedule(due, lambda s=sku, q=qty, src=dock, t=due: self.putaway(s, q, src, t), order=25)

    def putaway(self, sku: str, qty: int, src: str, at: datetime) -> None:
        self.move(at, "PUTAWAY", sku, qty, src, self.putaway_bin(self.by_sku[sku], qty))

    def purchase(self, d: date) -> None:
        for pr in self.products:
            daily = pr.base_demand
            rop = math.ceil((pr.lead_days + 3) * daily)
            if self.storable_on_hand(pr.sku) + self.on_order[pr.sku] >= rop or daily <= 0:
                continue
            qty = max(10, math.ceil(14 * daily))
            arrival = d + timedelta(days=math.ceil(pr.lead_days * self.lead_time_multiplier))
            while arrival in self.calendar and not self.calendar[arrival].is_operating:
                arrival += timedelta(days=1)
            self.on_order[pr.sku] += qty
            self._arrivals[arrival].append((pr.sku, qty))

    # ------------------------------------------------------------------ run

    # ------------------------------------------------------------------ the input log (spec 6)

    def apply_input(self, inp: Input, n: int) -> None:
        a = inp.args
        if inp.type == "set_param":
            value = float(a["value"])
            match a["param"]:
                case "replenishment_lag_minutes":
                    self.replen_lag = timedelta(minutes=value)
                case "lead_time_multiplier":
                    self.lead_time_multiplier = value
                case "receiving_capacity":
                    self.receiving_capacity = value
                case "putaway_delay_minutes":
                    self.putaway_delay = timedelta(minutes=value)
                case _:
                    raise ValueError(f"unknown parameter {a['param']}")
        elif inp.type == "create_order":
            self.stage_order(inp.at, a, n)
        elif inp.type == "delay_inbound":
            skus, days = set(a["skus"]), int(a["days"])
            for arrival in sorted(d for d in self._arrivals if d >= inp.at.date()):
                moved = [x for x in self._arrivals[arrival] if x[0] in skus]
                if not moved:
                    continue
                self._arrivals[arrival] = [x for x in self._arrivals[arrival] if x[0] not in skus]
                later = arrival + timedelta(days=days)
                while later in self.calendar and not self.calendar[later].is_operating:
                    later += timedelta(days=1)
                self._arrivals[later].extend(moved)
        elif inp.type == "demand_shock":
            skus = set(a.get("skus") or [p.sku for p in self.products if p.category == a.get("category")])
            first = inp.at.astimezone(self.tz).date()
            self.shocks.append((first, first + timedelta(days=int(a["days"])), skus, float(a["multiplier"])))

    def stage_order(self, at: datetime, a: dict[str, Any], n: int) -> None:
        """An outlet order placed now (spec 6 create_order), due `due_in_hours` from now; it is
        allocated, picked and shipped by the normal flow, from 30 minutes after it arrives."""
        outlet = next(o for o in self.outlets if o.code == a["customer"])
        deadline = at + timedelta(hours=float(a["due_in_hours"]))
        delivery = deadline.astimezone(self.tz).date()
        lane = f"S-L{(int(outlet.code[3:]) % 20) + 1:02d}"
        order = _Order(f"SO-{delivery:%Y%m%d}-{outlet.code}-S{n + 1}", outlet.code, delivery, deadline,
                       deadline - timedelta(minutes=30), lane)  # fmt: skip
        self.emit(
            at,
            "ORDER",
            {
                "order_no": order.order_no,
                "customer": order.customer,
                "delivery_date": delivery.isoformat(),
                "dispatch_deadline": deadline.isoformat(),
            },
        )
        for i, line in enumerate(a["lines"], start=1):
            ln = _Line(f"{order.order_no}-{i:02d}", i, str(line["sku"]), int(line["qty"]))
            order.lines.append(ln)
            self.emit(at, "ORDER_LINE", {"external_id": ln.external_id, "order_no": order.order_no,
                                         "line_no": i, "sku": ln.sku, "qty_ordered": ln.qty})  # fmt: skip
        allocate_at = at + timedelta(minutes=30)
        self.schedule(allocate_at, lambda: self.allocate(delivery, [order], allocate_at), order=50)

    def run(self) -> list[OutRecord]:
        """The history up to the clock start, its tick markers and the load-complete marker."""
        self._simulate(self.p.clock_start)
        out = [
            OutRecord(i, t, at, payload)
            for i, (at, t, payload) in enumerate(self._timeline(self.p.clock_start), 1)
        ]
        out.append(OutRecord(len(out) + 1, "LOAD_COMPLETE", self.p.clock_start, {"records": len(out)}))
        return out

    def _simulate(self, until: datetime) -> None:
        """Every event up to `until`, in time order. Each simulated day's schedule is generated at
        that day's start from the seed and the state then, so the events up to any time T are the
        same whatever later time the simulation runs to (jump equals play, simulator spec 5.2)."""
        self.slot_and_open()
        for i, inp in enumerate(self.p.inputs):
            self.schedule(inp.at, lambda x=inp, n=i: self.apply_input(x, n), order=5)
        d = self.history_start.date()
        while d <= until.date():
            self.schedule(self.local(d, time(0, 0)), lambda day=d: self.day_start(day), order=0)
            d += timedelta(days=1)
        while self._events:
            at, _, _, action = heapq.heappop(self._events)
            if at > until:
                break
            self._now = at
            action()

    def _timeline(self, until: datetime) -> list[tuple[datetime, str, dict[str, Any]]]:
        """Records up to `until` in emission order, with a tick marker every 15 simulated minutes
        after the records it covers (spec 2.1): a tick at T follows every record at or before T."""
        ordered = sorted((r for r in self.records if r[0] <= until), key=lambda r: (r[0], r[1], r[2]))
        out: list[tuple[datetime, str, dict[str, Any]]] = []
        next_tick = self.history_start
        for at, _, _, rtype, payload in ordered:
            while at > next_tick:
                out.append((next_tick, "TICK", {}))
                next_tick += TICK
            out.append((at, rtype, payload))
        while next_tick <= until:
            out.append((next_tick, "TICK", {}))
            next_tick += TICK
        return out


class ShiftRefused(ValueError):
    """A time shift outside the rules (simulator spec 5.2, FR-SIM-05): said to the user as is."""


def extend(params: RunParams, after: datetime, until: datetime, first_seq: int) -> list[OutRecord]:
    """The records of the period (after, until], numbered from `first_seq` (Slice 4 time shift).

    The run is regenerated from its seed up to `until` (180 days take about a second) and only
    the new period is returned, so a shift in one step and a shift in several steps give the same
    records, and a restart of the simulator loses nothing.
    """
    sim = Simulation(params)
    if until <= after:
        raise ShiftRefused("Time only moves forward.")
    if (until - sim.history_start) % TICK:
        raise ShiftRefused("Time moves in steps of 15 minutes.")
    if until.astimezone(sim.tz).date() > max(sim.calendar):
        raise ShiftRefused(f"The business calendar ends on {max(sim.calendar):%d %B %Y}.")
    sim._simulate(until)  # pyright: ignore[reportPrivateUsage]
    period = [r for r in sim._timeline(until) if r[0] > after]  # pyright: ignore[reportPrivateUsage]
    return [OutRecord(first_seq + i, t, at, payload) for i, (at, t, payload) in enumerate(period)]


def generate(params: RunParams) -> list[OutRecord]:
    return Simulation(params).run()
