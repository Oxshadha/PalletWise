"""Deterministic guardrails (agent spec 6): input normalization and output grounding.

These are boundaries, not judgments: they never grant a permission, and the output checks
only decide whether model text may be shown or must be replaced by the template answer.
"""

import contextlib
import re
import unicodedata
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from palletwise.contracts.results import ResultEnvelope

MAX_QUESTION = 1_000
_CONTROL = re.compile(
    "[\\x00-\\x08\\x0b-\\x1f\\x7f-\\x9f"  # control characters
    "\\u200b-\\u200f\\u202a-\\u202e\\u2060-\\u206f\\ufeff]"  # zero-width and bidi
)
# En dash, figure dash and minus sign become a hyphen; em dash and horizontal bar a comma.
_DASHES = {chr(0x2013): "-", chr(0x2012): "-", chr(0x2212): "-", chr(0x2014): ", ", chr(0x2015): ", "}


class QuestionRejected(ValueError):
    """The question cannot be processed; the message is shown to the user."""


def normalize_question(text: str) -> str:
    """NFKC, invisible and control characters removed, whitespace collapsed, length checked."""
    cleaned = _CONTROL.sub("", unicodedata.normalize("NFKC", text))
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        raise QuestionRejected("Please type a question.")
    if len(cleaned) > MAX_QUESTION:
        raise QuestionRejected(f"Questions can be at most {MAX_QUESTION} characters.")
    return cleaned


def normalize_dashes(text: str) -> str:
    """FR-COP-13: em and en dashes are replaced, never a reason to reject an answer."""
    for dash, replacement in _DASHES.items():
        text = text.replace(dash, replacement)
    return re.sub(r"\s+,", ",", text).replace(",,", ",")


# ---------------------------------------------------------------- grounding (agent spec 6.1)

# Identifiers such as SKU-102, A8-01-02 or C-F01: letters and digits joined by hyphens, with
# at least one digit, so hyphenated words ("out-of-stock") are not identifiers.
IDENTIFIER = re.compile(r"\b(?=[A-Za-z0-9-]*\d)[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+\b")
_NUMBER = re.compile(r"(?<![\w.])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?%?|(?<![\w.])-?\d+(?:\.\d+)?%?")
_TIME = re.compile(r"\b\d{1,2}:\d{2}\b")


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float | Decimal):
        return Decimal(str(value))
    return None


def _numbers_in(text: str) -> list[str]:
    without_ids = IDENTIFIER.sub(" ", text)
    return _NUMBER.findall(without_ids)


def _time_parts(moment: datetime, time_zone: str) -> set[Decimal]:
    local = moment.astimezone(ZoneInfo(time_zone))
    return {Decimal(v) for v in (local.year, local.month, local.day, local.hour, local.minute)}


def _allowed_numbers(results: Iterable[ResultEnvelope], question: str) -> set[Decimal]:
    allowed: set[Decimal] = set()
    for result in results:
        allowed.add(Decimal(result.row_count))
        for row in result.rows:
            for value in row.values():
                if (d := _decimal(value)) is not None:
                    allowed.add(d)
                elif isinstance(value, datetime):
                    allowed |= _time_parts(value, result.evidence.time_zone)
                elif isinstance(value, str):
                    # A number inside a name or code ("Soda Water 1.5L Value Pack") is quoted from
                    # the data, not stated as a quantity; without this, quoting the name fails.
                    for token in _numbers_in(value):
                        with contextlib.suppress(InvalidOperation):
                            allowed.add(_parse(token))
        # The parameters the tool ran with ("the last 30 days", "top 5") are facts of the answer.
        for value in result.normalized_request.values():
            if (d := _decimal(value)) is not None:
                allowed.add(d)
        for moment in (result.evidence.as_of, result.evidence.data_as_of):
            if moment is not None:
                allowed |= _time_parts(moment, result.evidence.time_zone)
    for token in _numbers_in(question):
        allowed.add(_parse(token))
    return allowed


def _parse(token: str) -> Decimal:
    return Decimal(token.rstrip("%").replace(",", ""))


def _grounded(token: str, allowed: set[Decimal]) -> bool:
    try:
        value = _parse(token)
    except InvalidOperation:
        return False
    exponent = value.as_tuple().exponent
    places = -exponent if isinstance(exponent, int) and exponent < 0 else 0
    quantum = Decimal(1).scaleb(-places)
    for candidate in allowed:
        if (candidate < 0) != (value < 0):  # signs must match (rule 4)
            continue
        if candidate.quantize(quantum) == value:
            return True
    return False


def _identifiers_allowed(results: Iterable[ResultEnvelope], question: str) -> set[str]:
    known: set[str] = {m.upper() for m in IDENTIFIER.findall(question)}
    for result in results:
        known |= {e.label.upper() for e in result.entities}
        for row in result.rows:
            for value in row.values():
                if isinstance(value, str):
                    known |= {m.upper() for m in IDENTIFIER.findall(value)}
    return known


def grounding_problems(text: str, results: list[ResultEnvelope], question: str) -> list[str]:
    """Numbers and identifiers in the text that the results or the question do not support."""
    problems: list[str] = []
    allowed = _allowed_numbers(results, question)
    for token in _numbers_in(_TIME.sub(lambda m: m.group(0).replace(":", " "), text)):
        if not _grounded(token, allowed):
            problems.append(f"number {token}")
    known = _identifiers_allowed(results, question)
    for identifier in IDENTIFIER.findall(text):
        if identifier.upper() not in known:
            problems.append(f"identifier {identifier}")
    return problems


# ---------------------------------------------------------------- required facts (ADR 0001 section 4)

_GROUPED = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")
_DECIMAL = re.compile(r"\d+\.\d+")


def _plain_number(match: re.Match[str]) -> str:
    value = match.group(0).replace(",", "")
    if "." in value:
        value = value.rstrip("0").rstrip(".")
    return value


def normalize_fact_text(text: str) -> str:
    """Case, spacing and number formatting normalized: "1,419,241.20", "1419241.2" and
    "1419241.20" read the same. Identifiers such as SKU-102 and units are left as written."""
    lowered = " ".join(str(text).lower().split())
    return _DECIMAL.sub(_plain_number, _GROUPED.sub(_plain_number, lowered))


def facts_missing(facts: Iterable[str], text: str) -> list[str]:
    """The required facts the text does not state (shared by the runtime and the evaluation)."""
    body = normalize_fact_text(text)
    return [f for f in facts if normalize_fact_text(f) not in body]
