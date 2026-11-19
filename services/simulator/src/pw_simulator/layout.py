"""The simulated site layout (simulator spec 3.2): 6 zones, 26 racks, 676 bins.

Coordinates in metres: origin south-west corner, x east, y north, z up. Floor 80 m by 50 m.
Zone A (prime) sits next to the shipping docks on the east wall.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Zone:
    code: str
    name: str
    zone_type: str
    temperature_class: str
    is_prime: bool
    outline: list[list[float]]


@dataclass(frozen=True, slots=True)
class Rack:
    code: str
    zone: str
    origin_x: float
    origin_y: float
    bay_count: int
    level_count: int
    bay_width_m: float = 1.2
    depth_m: float = 1.0
    level_height_m: float = 1.5
    rotation_deg: float = 0.0


@dataclass(frozen=True, slots=True)
class Bin:
    code: str
    zone: str
    rack: str | None
    bay: int | None
    level: int | None
    bin_role: str
    x: float
    y: float
    z: float
    width_m: float
    depth_m: float
    height_m: float
    capacity_volume_m3: float
    capacity_weight_kg: float
    single_sku: bool
    temperature_class: str = "AMBIENT"


@dataclass(frozen=True, slots=True)
class Feature:
    code: str
    feature_type: str
    label: str
    outline: list[list[float]]


@dataclass(slots=True)
class Layout:
    zones: list[Zone] = field(default_factory=list[Zone])
    racks: list[Rack] = field(default_factory=list[Rack])
    bins: list[Bin] = field(default_factory=list[Bin])
    features: list[Feature] = field(default_factory=list[Feature])


def _rect(x0: float, y0: float, x1: float, y1: float) -> list[list[float]]:
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _rack_bins(rack: Rack, role_for_level: dict[int, str], temp: str) -> list[Bin]:
    bins: list[Bin] = []
    for bay in range(1, rack.bay_count + 1):
        for level in range(1, rack.level_count + 1):
            role = role_for_level[level]
            bins.append(
                Bin(
                    code=f"{rack.code}-{bay:02d}-{level:02d}",
                    zone=rack.zone,
                    rack=rack.code,
                    bay=bay,
                    level=level,
                    bin_role=role,
                    x=round(rack.origin_x + (bay - 0.5) * rack.bay_width_m, 2),
                    y=round(rack.origin_y + rack.depth_m / 2, 2),
                    z=round((level - 0.5) * rack.level_height_m, 2),
                    width_m=rack.bay_width_m,
                    depth_m=rack.depth_m,
                    height_m=1.2,
                    capacity_volume_m3=1.44,
                    capacity_weight_kg=500.0 if role == "PICK" else 1000.0,
                    single_sku=role == "PICK",
                    temperature_class=temp,
                )
            )
    return bins


def _floor_bins(
    zone: str,
    prefix: str,
    role: str,
    count: int,
    x0: float,
    y0: float,
    per_row: int,
    w: float,
    d: float,
    h: float,
    vol: float,
    kg: float,
) -> list[Bin]:
    bins: list[Bin] = []
    for i in range(count):
        row, col = divmod(i, per_row)
        bins.append(
            Bin(
                code=f"{prefix}{i + 1:02d}",
                zone=zone,
                rack=None,
                bay=None,
                level=None,
                bin_role=role,
                x=round(x0 + (col + 0.5) * w, 2),
                y=round(y0 + (row + 0.5) * d, 2),
                z=round(h / 2, 2),
                width_m=w,
                depth_m=d,
                height_m=h,
                capacity_volume_m3=vol,
                capacity_weight_kg=kg,
                single_sku=False,
            )
        )
    return bins


def build_layout() -> Layout:
    lay = Layout()
    lay.zones = [
        Zone("A", "Fast pick", "FAST_PICK", "AMBIENT", True, _rect(52, 6, 70, 44)),
        Zone("B", "Reserve", "RESERVE", "AMBIENT", False, _rect(10, 6, 50, 44)),
        Zone("C", "Bulk floor", "BULK", "AMBIENT", False, _rect(10, 44, 50, 49)),
        Zone("D", "Chilled", "COLD", "CHILLED", False, _rect(1, 6, 9, 44)),
        Zone("R", "Returns", "RETURNS", "AMBIENT", False, _rect(1, 1, 9, 5)),
        Zone("S", "Docks and staging", "STAGING", "AMBIENT", False, _rect(71, 1, 79, 49)),
    ]

    # Racks run along x: 6 bays x 1.2 m = 7.2 m long, 1.0 m deep; aisles between rows.
    # Zone A: 8 racks, 6 bays, 4 levels, all PICK. Two columns of four rows.
    for i in range(8):
        col, row = divmod(i, 4)
        lay.racks.append(Rack(f"A{i + 1}", "A", 53.0 + col * 9.0, 8.0 + row * 9.0, 6, 4))
    # Zone B: 14 racks, 6 bays, 4 levels; level 1 PICK, levels 2 to 4 RESERVE.
    for i in range(14):
        col, row = divmod(i, 7)
        lay.racks.append(Rack(f"B{i + 1}", "B", 12.0 + col * 18.0, 8.0 + row * 5.0, 6, 4))
    # Zone D: 4 racks, 6 bays, 3 levels; level 1 PICK, levels 2 to 3 RESERVE.
    for i in range(4):
        lay.racks.append(Rack(f"D{i + 1}", "D", 1.4, 8.0 + i * 9.0, 6, 3))

    for rack in lay.racks:
        if rack.zone == "A":
            lay.bins += _rack_bins(rack, {1: "PICK", 2: "PICK", 3: "PICK", 4: "PICK"}, "AMBIENT")
        elif rack.zone == "B":
            lay.bins += _rack_bins(rack, {1: "PICK", 2: "RESERVE", 3: "RESERVE", 4: "RESERVE"}, "AMBIENT")
        else:
            lay.bins += _rack_bins(rack, {1: "PICK", 2: "RESERVE", 3: "RESERVE"}, "CHILLED")

    lay.bins += _floor_bins("C", "C-F", "BULK", 40, 10.0, 44.0, 20, 2.0, 2.5, 1.8, 2.16, 1500.0)
    lay.bins += _floor_bins("R", "R-F", "RETURNS", 10, 1.0, 1.0, 5, 1.6, 2.0, 1.8, 2.16, 1500.0)
    lay.bins += _floor_bins("S", "S-L", "STAGING", 20, 71.0, 10.0, 2, 4.0, 3.9, 1.8, 17.0, 5000.0)
    lay.bins += _floor_bins("S", "S-R", "RECEIVING", 6, 71.0, 1.0, 6, 1.33, 2.5, 1.8, 6.0, 3000.0)

    lay.features = [
        Feature("DOCK-SHIP-1", "SHIPPING_DOCK", "Shipping dock 1", _rect(79, 20, 80, 26)),
        Feature("DOCK-SHIP-2", "SHIPPING_DOCK", "Shipping dock 2", _rect(79, 30, 80, 36)),
        Feature("DOCK-RECV-1", "RECEIVING_DOCK", "Receiving dock 1", _rect(70, 0, 76, 1)),
        Feature("DOCK-RECV-2", "RECEIVING_DOCK", "Receiving dock 2", _rect(76, 0, 80, 1)),
        Feature("AISLE-CROSS", "AISLE", "Cross aisle", _rect(50, 6, 52, 44)),
        Feature("OFFICE", "OFFICE", "Office", _rect(1, 45, 9, 49)),
    ]
    return lay
