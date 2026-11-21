"""Scenarios (simulator spec 6): YAML files that stage a situation through the run's input log.

A scenario's events start relative to the moment it is run; its preconditions are checked first and
a scenario outside them is refused with the reason, never run at another time. Event types are a
closed list. Expectations and tour lines are data for the evaluation suite and the guided demo.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from pw_simulator.history import INPUT_TYPES, PARAMS, Input

SCENARIOS = Path(__file__).with_name("scenarios")
_DURATION = re.compile(r"^(\d+)([mhd])$")
_UNITS = {"m": "minutes", "h": "hours", "d": "days"}


class ScenarioRefused(ValueError):
    """Unknown scenario, or its preconditions do not hold now (said to the user as is)."""


def duration(text: str) -> timedelta:
    match = _DURATION.match(text)
    if match is None:
        raise ValueError(f"not a duration: {text!r}")
    return timedelta(**{_UNITS[match.group(2)]: int(match.group(1))})


@dataclass(frozen=True, slots=True)
class Event:
    after: timedelta
    type: str
    args: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Scenario:
    id: str
    title: str
    description: str
    requires: dict[str, Any]
    events: tuple[Event, ...]
    expect: tuple[dict[str, Any], ...]
    tour: tuple[str, ...]

    def inputs(self, start: datetime) -> list[Input]:
        """The input log entries, relative to `start` (strictly after the emitted period)."""
        return [Input(start + e.after, e.type, e.args) for e in self.events]

    def check(self, now: datetime) -> None:
        between = self.requires.get("sim_time_between")
        if between:
            first, last = (datetime.fromisoformat(str(x)) for x in between)
            if not first <= now <= last:
                raise ScenarioRefused(
                    f"{self.title} runs between {first:%d %B %H:%M} and {last:%d %B %H:%M} site time."
                )


def _event(raw: dict[str, Any], where: str) -> Event:
    kind = raw.get("type")
    if kind not in INPUT_TYPES:
        raise ValueError(f"{where}: event type {kind!r} is not one of {INPUT_TYPES}")
    args = {k: v for k, v in raw.items() if k not in ("after", "type")}
    if kind == "set_param" and args.get("param") not in PARAMS:
        raise ValueError(f"{where}: parameter {args.get('param')!r} is not one of {PARAMS}")
    return Event(duration(str(raw.get("after", "0m"))), str(kind), args)


@cache
def load(scenario_id: str) -> Scenario:
    path = SCENARIOS / f"{scenario_id}.yaml"
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", scenario_id) or not path.is_file():
        raise ScenarioRefused(f"There is no scenario {scenario_id}.")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw.get("id") != scenario_id:
        raise ValueError(f"{path.name}: id {raw.get('id')!r} does not match the file name")
    return Scenario(
        id=scenario_id,
        title=str(raw["title"]),
        description=str(raw.get("description", "")),
        requires=dict(raw.get("requires") or {}),
        events=tuple(
            _event(dict[str, Any](e), f"{path.name} event {i + 1}")
            for i, e in enumerate(list[Any](raw.get("events") or []))
        ),
        expect=tuple(dict[str, Any](e) for e in list[Any](raw.get("expect") or [])),
        tour=tuple(str(t) for t in list[Any](raw.get("tour") or [])),
    )


def available() -> list[Scenario]:
    return [load(p.stem) for p in sorted(SCENARIOS.glob("*.yaml"))]
