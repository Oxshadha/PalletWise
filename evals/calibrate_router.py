"""Fits the tier-2 router's threshold and margin (ADR 0001 amendment 2, safeguard 1).

Usage: python -m evals.calibrate_router [--write]

On the calibration set (evals/calibration/semantic_routes.jsonl, separate from the evaluation sets),
every setting is scored. A setting is allowed only with zero misroutes: no question routed to a
wrong route and no near-miss routed at all. Among allowed settings the one with the most correct
routes wins, and among those the most conservative (highest threshold, then highest margin).
With --write, the fitted values and the set's hash go into semantic_routes.yaml.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import yaml

from palletwise.agent import semantic_router

CALIBRATION = Path(__file__).with_name("calibration") / "semantic_routes.jsonl"


def _say(line: str) -> None:
    """Command-line output."""
    sys.stdout.write(line + "\n")


def score(
    router: semantic_router.SemanticRouter, rows: list[dict[str, str | None]]
) -> tuple[int, int, list[str]]:
    """Correct routes, misroutes, and a line per misroute."""
    correct, wrong, notes = 0, 0, []
    for row in rows:
        decision = router.decide(str(row["question"]))
        got = decision.route.name if decision.route else None
        if got is None:
            continue
        if got == row["route"]:
            correct += 1
        else:
            wrong += 1
            notes.append(f"{row['question']!r}: routed to {got}, expected {row['route']}")
    return correct, wrong, notes


def fit(raw: dict, rows: list[dict[str, str | None]]) -> tuple[float, float, int]:  # type: ignore[type-arg]
    best: tuple[int, float, float] | None = None
    for t in [round(0.20 + 0.02 * i, 2) for i in range(36)]:
        for m in [round(0.02 * i, 2) for i in range(16)]:
            correct, wrong, _ = score(semantic_router.build({**raw, "threshold": t, "margin": m}), rows)
            if wrong == 0 and (best is None or (correct, t, m) > best):
                best = (correct, t, m)
    if best is None:
        raise SystemExit("no setting routes without misroutes: fix the routes before calibrating")
    return best[1], best[2], best[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evals.calibrate_router")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    data = CALIBRATION.read_bytes()
    rows = [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]
    text = semantic_router.ROUTES_FILE.read_text(encoding="utf-8")
    raw = yaml.safe_load(text)
    threshold, margin, correct = fit(raw, rows)
    positives = sum(r["route"] is not None for r in rows)
    fitted = semantic_router.build({**raw, "threshold": threshold, "margin": margin})
    _, wrong, notes = score(fitted, rows)
    calibration = {
        "set": "evals/calibration/semantic_routes.jsonl",
        "sha256": hashlib.sha256(data).hexdigest(),
        "questions": len(rows),
        "positives": positives,
        "misroutes": wrong,
        "coverage": round(correct / positives, 4) if positives else 0.0,
    }
    _say(f"threshold {threshold}, margin {margin}: {correct} of {positives} routed, {wrong} misroutes")
    for row in rows:
        decision = fitted.decide(str(row["question"]))
        if row["route"] is not None and decision.route is None:
            _say(f"  abstained on {row['question']!r} ({decision.reason}, score {decision.score:.2f})")
    for note in notes:
        _say(f"  MISROUTE {note}")
    if args.write:
        text = re.sub(r"(?m)^threshold: .*$", f"threshold: {threshold}  # fitted by calibration", text)
        text = re.sub(r"(?m)^margin: .*$", f"margin: {margin}  # fitted by calibration", text)
        text = re.sub(r"(?m)^calibration: .*$", f"calibration: {json.dumps(calibration)}", text)
        semantic_router.ROUTES_FILE.write_text(text, encoding="utf-8")
        _say(f"written to {semantic_router.ROUTES_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
