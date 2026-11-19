"""Deterministic routing for paths A and B (agent spec 3.2)."""

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

RULES_FILE = Path(__file__).with_name("fast_path.yaml")


class _Rule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str
    tool: str
    identifier: str
    argument: str
    words: tuple[str, ...]


class _Rules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: int
    identifiers: dict[str, str]
    rules: tuple[_Rule, ...]


@dataclass(frozen=True, slots=True)
class Route:
    rule: str
    tool: str
    arguments: dict[str, Any]


@cache
def _rules() -> _Rules:
    rules = _Rules.model_validate(yaml.safe_load(RULES_FILE.read_text(encoding="utf-8")))
    for rule in rules.rules:
        if rule.identifier not in rules.identifiers:
            raise ValueError(f"fast path rule {rule.name} uses unknown identifier {rule.identifier}")
    return rules


def route(question: str) -> Route | None:
    rules = _rules()
    words = set(re.findall(r"[a-z]+", question.lower()))
    found: dict[str, set[str]] = {
        name: {m.upper() for m in re.findall(pattern, question, flags=re.IGNORECASE)}
        for name, pattern in rules.identifiers.items()
    }
    # A question naming identifiers of more than one kind is not a simple lookup.
    if sum(1 for ids in found.values() if ids) != 1:
        return None
    matches = [
        Route(rule.name, rule.tool, {rule.argument: next(iter(found[rule.identifier]))})
        for rule in rules.rules
        if len(found[rule.identifier]) == 1 and words & set(rule.words)
    ]
    return matches[0] if len(matches) == 1 else None
