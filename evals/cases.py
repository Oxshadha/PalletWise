"""Evaluation cases (evals spec 2): one JSON object per line in evals/sets/<set>.jsonl."""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

SETS_DIR = Path(__file__).with_name("sets")

Category = Literal[
    "DATA",
    "FOLLOW_UP",
    "PAGE_CONTEXT",
    "NO_RESULTS",
    "UNSUPPORTED",
    "OUT_OF_SCOPE",
    "INJECTION",
    "INDIRECT_INJECTION",
    "SCENARIO",
    "PERMISSION",
    "GOVERNED",  # only the governed query tier can answer (ADR 0002): rows checked against reference SQL
    "GOVERNED_INVALID",  # a governed query would compute a wrong number: must not be answered by one
]
CATEGORIES = set(Category.__args__)  # type: ignore[attr-defined]
OUTCOMES = {"ANSWER", "NO_RESULTS", "DECLINE", "STALE", "DENIED"}


@dataclass(frozen=True)
class Gold:
    path: str | None
    tool: str | None
    params: dict[str, Any]  # the minimum the chosen call must match (evals spec 6)
    outcome: str
    highlights_ref: str | None
    facts: tuple[str, ...]
    forbidden: tuple[str, ...]
    # The complete request the deterministic gate executes, when `params` is only the minimum a
    # query must match (for example its filters and scope, whatever measure the model picks).
    run_params: dict[str, Any] | None = None
    # The intended tier (ADR 0001 amendment 2): 1 exact route, 2 semantic router, 3 the model.
    tier: int | None = None
    # Governed cases: the rows an independently written query returns (ADR 0002 section 7).
    reference_sql: str | None = None

    @property
    def call(self) -> dict[str, Any]:
        return dict(self.run_params if self.run_params is not None else self.params)


@dataclass(frozen=True)
class Case:
    id: str
    set: str
    question: str
    category: str
    role: str
    gold: Gold
    page_context: dict[str, Any] | None = None
    follows: str | None = None
    scenario: str | None = None
    tags: tuple[str, ...] = field(default=())
    # "deterministic": a routing check only (a near-miss with no single correct tool); the model
    # gate skips it. None: both gates.
    level: str | None = None


class CaseError(ValueError):
    pass


def _case(raw: dict[str, Any], set_name: str, line: int) -> Case:
    where = f"{set_name}.jsonl line {line}"
    allowed = {"id", "question", "category", "role", "page_context", "follows", "scenario", "gold", "level"}
    if unknown := set(raw) - allowed:
        raise CaseError(f"{where}: unknown fields {sorted(unknown)}")
    gold = raw.get("gold") or {}
    gold_allowed = {
        "path",
        "tool",
        "params",
        "run_params",
        "outcome",
        "highlights_ref",
        "facts",
        "forbidden",
        "tier",
        "reference_sql",
    }
    if unknown := set(gold) - gold_allowed:
        raise CaseError(f"{where}: unknown gold fields {sorted(unknown)}")
    if raw.get("category") not in CATEGORIES:
        raise CaseError(f"{where}: category {raw.get('category')!r}")
    if raw.get("role") not in {"warehouse_manager", "admin"}:
        raise CaseError(f"{where}: role {raw.get('role')!r}")
    if gold.get("outcome") not in OUTCOMES:
        raise CaseError(f"{where}: outcome {gold.get('outcome')!r}")
    if gold.get("path") not in {None, "A", "B", "C"}:
        raise CaseError(f"{where}: path {gold.get('path')!r}")
    if gold.get("tier") not in {None, 1, 2, 3}:
        raise CaseError(f"{where}: tier {gold.get('tier')!r}")
    if raw.get("level") not in {None, "deterministic"}:
        raise CaseError(f"{where}: level {raw.get('level')!r}")
    return Case(
        id=str(raw["id"]),
        set=set_name,
        question=str(raw["question"]),
        category=str(raw["category"]),
        role=str(raw["role"]),
        page_context=raw.get("page_context"),
        follows=raw.get("follows"),
        scenario=raw.get("scenario"),
        level=raw.get("level"),
        gold=Gold(
            path=gold.get("path"),
            tool=gold.get("tool"),
            params=dict(gold.get("params") or {}),
            outcome=str(gold["outcome"]),
            highlights_ref=gold.get("highlights_ref"),
            facts=tuple(str(f) for f in gold.get("facts") or ()),
            forbidden=tuple(str(f) for f in gold.get("forbidden") or ()),
            run_params=dict(gold["run_params"]) if gold.get("run_params") is not None else None,
            tier=gold.get("tier"),
            reference_sql=gold.get("reference_sql"),
        ),
    )


def load_set(name: str) -> tuple[list[Case], str]:
    """The cases of one set and the SHA-256 of its file (the freeze rule, evals spec 4)."""
    path = SETS_DIR / f"{name}.jsonl"
    data = path.read_bytes()
    cases: list[Case] = []
    for number, line in enumerate(data.decode("utf-8").splitlines(), start=1):
        if line.strip():
            cases.append(_case(json.loads(line), name, number))
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise CaseError(f"{name}: duplicate case ids")
    by_id = {c.id for c in cases}
    for case in cases:
        if case.follows and case.follows not in by_id:
            raise CaseError(f"{case.id}: follows unknown case {case.follows}")
    return cases, hashlib.sha256(data).hexdigest()
