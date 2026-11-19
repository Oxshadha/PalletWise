"""Loads the business calendar reference data from calendar.csv (CWM 7.1, 7.2; FR-FES-01).

Global reference data: no tenant, loaded by the `migrate` step, never through a connector.
"""

import csv
from dataclasses import astuple, dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import psycopg

REGION_CODE = "LK"
REGION_NAME = "Sri Lanka"


@dataclass(frozen=True, slots=True)
class CalendarDay:
    date: date
    dow: int
    is_weekend: bool
    iso_year: int
    iso_week: int
    is_payday: bool
    festival: str | None
    festival_ramp: Decimal
    is_holiday: bool
    is_monsoon: bool
    is_operating: bool


def read_calendar(path: Path) -> list[CalendarDay]:
    """Parse calendar.csv exactly as delivered; no transformation beyond typing."""
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    return [
        CalendarDay(
            date=date.fromisoformat(r["date"]),
            dow=int(r["dow"]),
            is_weekend=r["is_weekend"] == "1",
            iso_year=int(r["iso_year"]),
            iso_week=int(r["iso_week"]),
            is_payday=r["is_payday"] == "1",
            festival=r["festival"] or None,
            festival_ramp=Decimal(r["festival_ramp"]),
            is_holiday=r["is_holiday"] == "1",
            is_monsoon=r["monsoon"] == "1",
            is_operating=r["is_operating"] == "1",
        )
        for r in rows
    ]


def load_calendar(conn: psycopg.Connection, path: Path) -> int:
    """Upsert the region and every calendar day. Returns the number of days loaded."""
    days = read_calendar(path)
    if not days:
        raise ValueError(f"{path} contains no calendar rows")
    start = min(d.date for d in days)
    end = max(d.date for d in days)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO pw.calendar_region (region_code, name, coverage_start, coverage_end)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (region_code) DO UPDATE
              SET coverage_start = EXCLUDED.coverage_start, coverage_end = EXCLUDED.coverage_end
            """,
            (REGION_CODE, REGION_NAME, start, end),
        )
        cur.executemany(
            """
            INSERT INTO pw.business_calendar
              (region_code, date, dow, is_weekend, iso_year, iso_week, is_payday, festival,
               festival_ramp, is_holiday, is_monsoon, is_operating)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (region_code, date) DO UPDATE SET
              dow = EXCLUDED.dow, is_weekend = EXCLUDED.is_weekend, iso_year = EXCLUDED.iso_year,
              iso_week = EXCLUDED.iso_week, is_payday = EXCLUDED.is_payday,
              festival = EXCLUDED.festival, festival_ramp = EXCLUDED.festival_ramp,
              is_holiday = EXCLUDED.is_holiday, is_monsoon = EXCLUDED.is_monsoon,
              is_operating = EXCLUDED.is_operating
            """,
            [(REGION_CODE, *astuple(d)) for d in days],
        )
    return len(days)
