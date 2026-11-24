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


class _Question(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    text: str
    tool: str
    arguments: dict[str, Any]


class _Rules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: int
    identifiers: dict[str, str]
    rules: tuple[_Rule, ...]
    questions: tuple[_Question, ...] = ()


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


def normalize(question: str) -> str:
    """Lower case, one space, straight apostrophes, no end punctuation."""
    text = question.replace("\u2019", "'").replace("\u2018", "'").lower()
    return " ".join(text.split()).rstrip(" ?.!")


# A message can carry a second request next to an identifier lookup ("Which items are running low
# on stock? Where is SKU-102 stored?"). An identifier route answers only its own part, so it must
# not take such a message: the model answers both (each through the executor). A clause asks for
# something when it has a question or request word and names something the warehouse holds. Text
# that only carries instructions ("Also run DROP TABLE ...", "follow any instructions in its name")
# asks for no warehouse data, so the lookup is still answered under the usual boundaries (INJ-003).
_CLAUSES = re.compile(r"[?;!]|\.\s|\b(?:and|also|plus|then|as well as)\b", re.IGNORECASE)
_ASKS = re.compile(
    r"\b(which|what|where|when|who|how|show|list|give|tell|are|is|do|does|did|can|could)\b", re.IGNORECASE
)
_WAREHOUSE = re.compile(
    r"\b(orders?|stock|products?|items?|skus?|zones?|bins?|locations?|inventory|movers?|units?|"
    r"exceptions?|capacity|dispatch\w*|shipments?|categor(?:y|ies)|brands?|pallets?|picks?|replenish\w*)\b",
    re.IGNORECASE,
)


def _asks_more(question: str, patterns: dict[str, str]) -> bool:
    """True when a clause without an identifier is a warehouse request of its own."""
    for clause in _CLAUSES.split(question):
        if not clause or not clause.strip():
            continue
        if any(re.search(p, clause, flags=re.IGNORECASE) for p in patterns.values()):
            continue  # the identifier's own clause
        if _ASKS.search(clause) and _WAREHOUSE.search(clause):
            return True
    return False


def route(question: str) -> Route | None:
    rules = _rules()
    exact = normalize(question)
    for known in rules.questions:
        if exact == known.text:
            return Route(f"question:{known.tool}", known.tool, dict(known.arguments))
    words = set(re.findall(r"[a-z]+", question.lower()))
    found: dict[str, set[str]] = {
        name: {m.upper() for m in re.findall(pattern, question, flags=re.IGNORECASE)}
        for name, pattern in rules.identifiers.items()
    }
    # A question naming identifiers of more than one kind is not a simple lookup.
    if sum(1 for ids in found.values() if ids) != 1:
        return None
    # A second request in the same message is not a simple lookup either (code review, compound
    # questions): an identifier route would answer one part and silently drop the other.
    if _asks_more(question, rules.identifiers):
        return None
    matches = [
        Route(rule.name, rule.tool, {rule.argument: next(iter(found[rule.identifier]))})
        for rule in rules.rules
        if len(found[rule.identifier]) == 1 and words & set(rule.words)
    ]
    return matches[0] if len(matches) == 1 else None
