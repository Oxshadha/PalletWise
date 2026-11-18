"""Reference inputs of the simulator: the business calendar and the outlets (simulator spec 3, 4).

Only `calendar.csv` and `outlets.csv` are used (vision 10, approved data restriction).
"""

import csv
import os
from dataclasses import dataclass
from datetime import date, time
from pathlib import Path

DATA_DIR = Path(os.environ.get("SIM_DATA_DIR", Path(__file__).resolve().parents[4] / "data" / "reference"))


@dataclass(frozen=True, slots=True)
class Day:
    date: date
    is_operating: bool
    is_payday: bool
    is_monsoon: bool
    festival: str | None
    festival_ramp: float
    dow: int


@dataclass(frozen=True, slots=True)
class Outlet:
    code: str
    brand: str
    district: str
    depot: str
    dock_type: str
    parking_constraint: str | None
    window_open: time
    window_close: time


def load_calendar(path: Path | None = None) -> dict[date, Day]:
    path = path or DATA_DIR / "calendar.csv"
    with path.open(newline="", encoding="utf-8") as fh:
        return {
            d.date: d
            for d in (
                Day(
                    date=date.fromisoformat(r["date"]),
                    is_operating=r["is_operating"] == "1",
                    is_payday=r["is_payday"] == "1",
                    is_monsoon=r["monsoon"] == "1",
                    festival=r["festival"] or None,
                    festival_ramp=float(r["festival_ramp"]),
                    dow=int(r["dow"]),
                )
                for r in csv.DictReader(fh)
            )
        }


def load_outlets(path: Path | None = None) -> list[Outlet]:
    path = path or DATA_DIR / "outlets.csv"
    with path.open(newline="", encoding="utf-8") as fh:
        return [
            Outlet(
                code=r["outlet_id"],
                brand=r["brand"],
                district=r["district"],
                depot=r["depot"],
                dock_type=r["dock_type"],
                parking_constraint=r["parking_constraint"] or None,
                window_open=time.fromisoformat(r["window_open_time"]),
                window_close=time.fromisoformat(r["window_close_time"]),
            )
            for r in csv.DictReader(fh)
        ]
