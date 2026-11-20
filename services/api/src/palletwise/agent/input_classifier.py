"""The input classifier (agent spec 6, Slice 2 D6, product owner 2026-11-20: deterministic).

An extra, advisory layer: it recognises messages that should be declined politely without a model
call. It never grants anything and is never the only control; OPA, `tools.executor`, the data block,
grounding and the output guards remain the boundary. It acts only on messages that no route
recognised (tier 3), so a real question carrying an injection ("Where is SKU-102 stored? Also run
DROP TABLE ...") is still answered, under those boundaries. When unsure, it abstains.
"""

import re
from dataclasses import dataclass
from typing import Literal

Kind = Literal["injection", "change_request", "restricted", "out_of_scope"]

# Attempts to change how the copilot works, reach other data, or run code.
_INJECTION = re.compile(
    r"\b(ignore (all |the )?(previous|above|prior)|disregard (all |the )?(previous|above|prior|your)|"
    r"you are now|pretend (that |you|the)|act as|system prompt|developer mode|jailbreak|"
    r"(print|reveal|show|repeat) (me )?your (instructions|prompt|rules)|switch to tenant|"
    r"select \* from|drop table|delete from|insert into|update pw\.|truncate table)\b",
    re.IGNORECASE,
)
# A command to change something, followed by something the warehouse holds. The copilot can only
# look things up. "Move on to the next question" has no such object and is left alone.
_CHANGE = re.compile(
    r"^\s*(please\s+|can you\s+|could you\s+|as the copilot,?\s+)?"
    r"(approve|reject|create|delete|remove|move|transfer|update|cancel|change|set|assign|release|"
    r"adjust|ship|replenish|allocate|deallocate)\b"
    # Not a command when a preposition or "me" follows: "update me on delayed orders", "change in
    # stock since Monday", "move on to the next question".
    r"(?!\s+(on|me|in|of|over|from|to|by|for)\b)"
    # The object follows the command within a few words, in the same request.
    r"(?:\s+[\w-]+){0,5}?\s+(proposals?|orders?|stock|units?|products?|items?|skus?|sku-\d+|bins?|"
    r"locations?|pallets?|inventory|allocations?|zones?|replenishments?)\b",
    re.IGNORECASE,
)
# Information the copilot never gives. Ambiguous words ("session", "token") are left out: a morning
# session or a token budget is ordinary warehouse talk.
_RESTRICTED = re.compile(
    r"\b(audit logs?|passwords?|api keys?|credentials|other tenants?|other users|user list)\b",
    re.IGNORECASE,
)
_OFF_TOPIC = re.compile(
    r"\b(weather|poem|joke|song|story|recipe|horoscope|news|translate|lyrics)\b", re.IGNORECASE
)
# A message naming anything the warehouse holds is never declined as off-topic: "any news on
# delayed orders?" is a warehouse question.
_WAREHOUSE = re.compile(
    r"\b(orders?|stock|products?|skus?|sku-\d+|zones?|bins?|locations?|items?|inventory|pallets?|"
    r"picks?|picked|picking|dispatch\w*|shipments?|warehouse|outlets?|categor\w+|brands?|"
    r"capacity|replenish\w*|movers?|units?)\b",
    re.IGNORECASE,
)

MESSAGES: dict[Kind, str] = {
    "injection": (
        "I can only answer questions about this warehouse, and I cannot change how I work. "
        "Please ask your question on its own."
    ),
    "change_request": ("I can only look things up. I cannot approve, create, move or change anything."),
    "restricted": "I cannot share that information.",
    "out_of_scope": "I can only answer questions about this warehouse.",
}


@dataclass(frozen=True)
class Verdict:
    kind: Kind | None  # None: no reason to decline; the normal path decides
    reason: str = ""

    @property
    def declines(self) -> bool:
        return self.kind is not None

    @property
    def message(self) -> str:
        return MESSAGES[self.kind] if self.kind else ""


def classify(question: str) -> Verdict:
    """Why a message no route recognised should be declined, or no verdict (abstain)."""
    if found := _INJECTION.search(question):
        return Verdict("injection", found.group(0))
    if found := _CHANGE.search(question):
        return Verdict("change_request", found.group(2))
    if found := _RESTRICTED.search(question):
        return Verdict("restricted", found.group(0))
    if (found := _OFF_TOPIC.search(question)) and not _WAREHOUSE.search(question):
        return Verdict("out_of_scope", found.group(0))
    return Verdict(None)
