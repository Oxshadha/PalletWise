"""Tier 2: the semantic router (ADR 0001 amendment 2).

Recognises a question that means the same as a curated route and routes it without a model call.
It abstains whenever it is not sure, and the question goes to the model (tier 3):

1. Conditions by unexplained words: every content word of the question must belong to the route's
   own vocabulary (its examples plus listed synonyms). Any other word (a zone, SKU, number, date,
   category, brand, outlet, "those") makes it abstain, so a condition can never be dropped.
2. Similarity: word and word-pair overlap with the route's examples, local and deterministic.
3. Calibrated confidence: the best route must reach the threshold and beat the second best by the
   margin; both are fitted on a calibration set separate from the evaluation sets.
4. No arguments are extracted: a route carries only its own fixed arguments.
"""

import itertools
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import yaml

ROUTES_FILE = Path(__file__).with_name("semantic_routes.yaml")
_WORD = re.compile(r"[a-z0-9]+(?:[-'][a-z0-9]+)*")


def stem(word: str) -> str:
    """A light, predictable stem: plural and simple verb endings only ("items" and "item" match;
    "lowest" stays apart from "low", which is a different question)."""
    if word.endswith("'s"):
        word = word[:-2]
    for suffix in ("ies", "es", "s"):
        if len(word) > 4 and word.endswith(suffix) and not word.endswith("ss"):
            return word[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return word


def words(text: str) -> list[str]:
    return [stem(w) for w in _WORD.findall(text.lower().replace("\u2019", "'"))]


def _features(tokens: list[str]) -> Counter[str]:
    return Counter(tokens + [f"{a} {b}" for a, b in itertools.pairwise(tokens)])


def _cosine(a: Counter[str], b: Counter[str]) -> float:
    dot = sum(a[k] * b[k] for k in a.keys() & b.keys())
    norm = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
    return dot / norm if norm else 0.0


@dataclass(frozen=True)
class SemanticRoute:
    name: str
    tool: str
    arguments: dict[str, Any]
    examples: tuple[str, ...]
    vocabulary: frozenset[str]
    features: tuple[Counter[str], ...] = field(default=(), compare=False, repr=False)


@dataclass(frozen=True)
class Decision:
    """The router's verdict with its evidence, kept for traces and calibration."""

    route: SemanticRoute | None
    score: float
    margin: float
    reason: str  # "routed", or why it abstained


@dataclass(frozen=True)
class SemanticRouter:
    routes: tuple[SemanticRoute, ...]
    stopwords: frozenset[str]
    threshold: float
    margin: float

    def content_words(self, question: str) -> list[str]:
        return [w for w in words(question) if w not in self.stopwords]

    def decide(self, question: str) -> Decision:
        content = self.content_words(question)
        if not content:
            return Decision(None, 0.0, 0.0, "no content words")
        features = _features(words(question))
        scored = sorted(
            (
                (max((_cosine(features, f) for f in route.features), default=0.0), route)
                for route in self.routes
            ),
            key=lambda pair: pair[0],
            reverse=True,
        )
        (best_score, best), second = scored[0], (scored[1][0] if len(scored) > 1 else 0.0)
        unexplained = [w for w in content if w not in best.vocabulary]
        if unexplained:
            # A word the route does not explain may be a condition: never drop it.
            return Decision(None, best_score, best_score - second, f"unexplained: {' '.join(unexplained)}")
        if best_score < self.threshold:
            return Decision(None, best_score, best_score - second, "below threshold")
        if best_score - second < self.margin:
            return Decision(None, best_score, best_score - second, "ambiguous between routes")
        return Decision(best, best_score, best_score - second, "routed")


def build(raw: dict[str, Any]) -> SemanticRouter:
    stopwords = frozenset(stem(w) for w in raw["stopwords"])
    routes: list[SemanticRoute] = []
    for r in raw["routes"]:
        examples = tuple(str(e) for e in r["examples"])
        tokens = [words(e) for e in examples]
        vocabulary = frozenset(
            {w for t in tokens for w in t if w not in stopwords} | {stem(w) for w in r.get("synonyms", [])}
        )
        routes.append(
            SemanticRoute(
                name=str(r["name"]),
                tool=str(r["tool"]),
                arguments=dict(r.get("arguments") or {}),
                examples=examples,
                vocabulary=vocabulary,
                features=tuple(_features(t) for t in tokens),
            )
        )
    return SemanticRouter(
        routes=tuple(routes),
        stopwords=stopwords,
        threshold=float(raw["threshold"]),
        margin=float(raw["margin"]),
    )


@cache
def router() -> SemanticRouter:
    return build(yaml.safe_load(ROUTES_FILE.read_text(encoding="utf-8")))
