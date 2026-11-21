"""The guided demo's storyline (slice-4-acceptance 2.1, D6) only names what the product has.

The browser runs each step's setup through the public API, so a scenario or a shift step the API
does not know would fail in front of an audience; this catches it at test time instead.
"""

import json
import typing
from pathlib import Path

from palletwise.api.simulation import ShiftIn
from pw_simulator import scenarios

STORYLINE = Path(__file__).resolve().parents[2] / "apps/web/src/features/simulation/storyline.json"


def test_every_step_names_a_real_scenario_or_shift_and_has_words() -> None:
    steps = json.loads(STORYLINE.read_text(encoding="utf-8"))["steps"]
    assert len(steps) == 8 and len({s["id"] for s in steps}) == 8
    known = {s.id for s in scenarios.available()}
    shift_steps = set(typing.get_args(typing.get_args(ShiftIn.model_fields["by"].annotation)[0]))
    for step in steps:
        assert step["title"] and step["show"]
        setup = step.get("setup")
        if setup is None:
            continue
        assert setup["label"]
        if setup["type"] == "scenario":
            assert setup["scenario"] in known
        else:
            assert setup["type"] == "shift" and setup["by"] in shift_steps
