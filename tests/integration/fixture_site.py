"""A small, hand-built site for rule fixtures (semantic spec 10: hand-calculated expectations).

Rows are written directly as the superuser into a site created through the normal provisioning
path, so every expected number in a test can be worked out by hand from the rows it creates.
"""

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
from support import provision_site

from palletwise.db.connection import role_pool, tenant_session

Connect = Callable[[str], psycopg.Connection]
SOURCE = "fixture"


class FixtureSite:
    def __init__(self, connect_as: Connect) -> None:
        self.tenant, self.site = provision_site(connect_as)
        self.db = connect_as("postgres")
        self.db.autocommit = True
        self._categories: dict[str, UUID] = {}
        self._seq = 0
        # Every stream synced at the site clock, as after a completed load (CWM 10.2); a site
        # that never synced answers UNAVAILABLE, which only the dedicated tests want.
        synced = self.db.execute(
            "UPDATE pw.sync_state s SET data_as_of = c.sim_time, last_success_at = c.sim_time"
            "  FROM pw.site_clock c WHERE c.site_id = s.site_id AND s.site_id = %s",
            (self.site,),
        ).rowcount
        assert synced == 4, "a provisioned site has one sync state per stream"

    # ------------------------------------------------------------ rows

    def _insert(self, table: str, row: dict[str, Any]) -> UUID:
        if table != "inventory_balance":  # every other canonical row names its source record
            row.setdefault("source_system", SOURCE)
            row.setdefault("external_id", str(uuid4()))
        columns = ", ".join(row)
        params = ", ".join(["%s"] * len(row))
        self.db.execute(
            f"INSERT INTO pw.{table} ({columns}) VALUES ({params})",
            [json.dumps(v) if isinstance(v, (dict, list)) else v for v in row.values()],
        )
        return row.get("id")  # type: ignore[return-value]

    def _next(self) -> int:
        self._seq += 1
        return self._seq

    def zone(
        self, code: str, *, zone_type: str = "RESERVE", prime: bool = False, active: bool = True
    ) -> UUID:
        return self._insert(
            "zone",
            {
                "id": uuid4(), "tenant_id": self.tenant, "site_id": self.site, "code": code,
                "name": f"Zone {code}", "zone_type": zone_type, "temperature_class": "AMBIENT",
                "outline": [[0, 0], [10, 0], [10, 10], [0, 10]], "is_prime": prime, "is_active": active,
            },
        )  # fmt: skip

    def rack(self, zone: UUID, code: str, *, bays: int = 4, levels: int = 3) -> UUID:
        return self._insert(
            "rack",
            {
                "id": uuid4(), "tenant_id": self.tenant, "site_id": self.site, "zone_id": zone, "code": code,
                "origin_x": 1, "origin_y": 1, "rotation_deg": 0, "bay_count": bays, "level_count": levels,
                "bay_width_m": 1, "depth_m": 1, "level_height_m": 1,
            },
        )  # fmt: skip

    def bin(
        self,
        zone: UUID,
        code: str,
        *,
        role: str = "PICK",
        volume_m3: float = 1.0,
        weight_kg: float = 100.0,
        rack: UUID | None = None,
        bay: int | None = None,
        level: int | None = None,
        active: bool = True,
    ) -> UUID:
        n = self._next()
        return self._insert(
            "bin",
            {
                "id": uuid4(), "tenant_id": self.tenant, "site_id": self.site, "zone_id": zone,
                "rack_id": rack, "bay": bay, "level": level, "code": code, "bin_role": role,
                "x": n % 40, "y": n // 40, "z": 0, "width_m": 1, "depth_m": 1, "height_m": 1,
                "capacity_volume_m3": volume_m3, "capacity_weight_kg": weight_kg, "is_active": active,
            },
        )  # fmt: skip

    def category(self, name: str) -> UUID:
        if name not in self._categories:
            code = f"{name[:3].upper()}{uuid4().hex[:4]}"
            self._categories[name] = self._insert(
                "category", {"id": uuid4(), "tenant_id": self.tenant, "code": code, "name": name}
            )
        return self._categories[name]

    def product(
        self,
        sku: str,
        *,
        volume_m3: float = 0.01,
        weight_kg: float = 1.0,
        value: float = 100.0,
        category: str = "General",
        brand: str = "Brand",
    ) -> UUID:
        return self._insert(
            "product",
            {
                "id": uuid4(), "tenant_id": self.tenant, "sku": sku, "name": f"Product {sku}",
                "category_id": self.category(category), "brand": brand, "uom": "EA",
                "unit_volume_m3": volume_m3, "unit_weight_kg": weight_kg, "unit_value": value,
                "temperature_class": "AMBIENT",
            },
        )  # fmt: skip

    def stock(self, bin_id: UUID, product: UUID, on_hand: int, allocated: int = 0) -> None:
        self._insert(
            "inventory_balance",
            {
                "tenant_id": self.tenant, "site_id": self.site, "bin_id": bin_id, "product_id": product,
                "on_hand": on_hand, "allocated": allocated, "last_movement_at": self.as_of(),
            },
        )  # fmt: skip

    def policy(self, product: UUID, pick_bin: UUID, min_qty: int, max_qty: int) -> None:
        self._insert(
            "replenishment_policy",
            {
                "id": uuid4(), "tenant_id": self.tenant, "site_id": self.site, "product_id": product,
                "pick_bin_id": pick_bin, "min_qty": min_qty, "max_qty": max_qty,
            },
        )  # fmt: skip

    def order(
        self, *, deadline: datetime | None = None, status: str = "OPEN", shipped_at: datetime | None = None
    ) -> UUID:
        if not hasattr(self, "_customer"):
            self._customer = self._insert(
                "customer",
                {
                    "id": uuid4(), "tenant_id": self.tenant, "code": f"CU{uuid4().hex[:6]}",
                    "brand": "Outlet", "district": "Colombo", "dock_type": "STANDARD",
                    "window_open": "08:00", "window_close": "18:00",
                },
            )  # fmt: skip
        when = deadline or self.as_of()
        return self._insert(
            "outbound_order",
            {
                "id": uuid4(), "tenant_id": self.tenant, "site_id": self.site,
                "order_no": f"SO-{uuid4().hex[:8]}", "customer_id": self._customer,
                "delivery_date": when.date(), "dispatch_deadline": when,
                "status": status, "shipped_at": shipped_at,
            },
        )  # fmt: skip

    def line(self, order: UUID, product: UUID, qty: int, *, allocated: int = 0, picked: int = 0) -> UUID:
        return self._insert(
            "order_line",
            {
                "id": uuid4(), "tenant_id": self.tenant, "site_id": self.site, "order_id": order,
                "line_no": self._next(), "product_id": product, "qty_ordered": qty,
                "qty_allocated": allocated, "qty_picked": picked,
            },
        )  # fmt: skip

    def movement(
        self,
        movement_type: str,
        product: UUID,
        qty: int,
        at: datetime,
        *,
        from_bin: UUID | None = None,
        to_bin: UUID | None = None,
        line: UUID | None = None,
        reason: str | None = None,
    ) -> None:
        self._insert(
            "movement",
            {
                "id": uuid4(), "tenant_id": self.tenant, "site_id": self.site, "movement_type": movement_type,
                "product_id": product, "qty": qty, "from_bin_id": from_bin, "to_bin_id": to_bin,
                "occurred_at": at, "order_line_id": line, "reason_code": reason,
            },
        )  # fmt: skip

    def pick(
        self, product: UUID, qty: int, at: datetime, from_bin: UUID, staging: UUID, *, cancelled: bool = False
    ) -> UUID:
        """A pick for its own one-line order, so each pick can be tied to an order status."""
        order = self.order(status="CANCELLED" if cancelled else "PICKED")
        line = self.line(order, product, qty, allocated=qty, picked=qty)
        self.movement("PICK", product, qty, at, from_bin=from_bin, to_bin=staging, line=line)
        return line

    # ------------------------------------------------------------ reading as the copilot does

    def as_of(self) -> datetime:
        row = self.db.execute(
            "SELECT sim_time FROM pw.site_clock WHERE site_id = %s", (self.site,)
        ).fetchone()
        assert row is not None and row[0] is not None, "provisioned sites have a clock"
        return row[0]

    def settings(self) -> UUID:
        row = self.db.execute(
            "SELECT id FROM pw.settings_version WHERE site_id = %s ORDER BY version_no DESC LIMIT 1",
            (self.site,),
        ).fetchone()
        assert row is not None
        return row[0]

    def new_settings(self, **changes: Any) -> UUID:
        current = self.db.execute(
            'SELECT "values" FROM pw.settings_version WHERE id = %s', (self.settings(),)
        ).fetchone()
        assert current is not None
        with self.db.transaction():
            self.db.execute("SELECT set_config('app.tenant_id', %s, true)", (str(self.tenant),))
            row = self.db.execute(
                "SELECT pw.create_settings_version(%s, %s, 1, %s, 'test', NULL)",
                (self.tenant, self.site, json.dumps({**current[0], **changes})),
            ).fetchone()
        assert row is not None
        return row[0]

    def rows(
        self, function: str, settings: UUID | None = None, *, as_of: datetime | None = None
    ) -> list[dict[str, Any]]:
        """Calls sem.<function>(site, as_of, settings) as pw_agent_read in a read snapshot."""
        with tenant_session(role_pool("PW_AGENT_READ"), self.tenant, read_snapshot=True) as conn:
            cursor = conn.execute(
                f"SELECT * FROM sem.{function}(%s, %s, %s)",
                (self.site, as_of or self.as_of(), settings or self.settings()),
            )
            names = [c.name for c in cursor.description or ()]
            return [dict(zip(names, r, strict=True)) for r in cursor.fetchall()]
