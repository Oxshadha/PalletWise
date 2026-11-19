"""Telemetry privacy (evals spec 11): applied to every span before any exporter sees it.

1. Allowlist: an attribute key not listed here is dropped, so a new attribute never leaks by default.
2. Masking: email addresses and phone numbers in allowed string values become a salted hash.
3. Never exported: bearer and JWT tokens are redacted; exception messages and stack traces are
   dropped (they can carry SQL values), keeping only the exception type.
4. Content events (prompts and responses) pass only when content capture is on.

Minimize first, mask second: code does not put personal data into attributes; this catches mistakes.
"""

import hashlib
import hmac
import re
from collections.abc import Sequence
from typing import Any, cast

from opentelemetry.sdk.trace import Event, ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import Status

ALLOWED_ATTRIBUTES = frozenset(
    {
        # http.server
        "http.request.method",
        "http.route",
        "http.response.status_code",
        "pw.trace.request_id",
        # copilot.run
        "pw.run.id",
        "pw.conversation.id",
        "pw.run.path",
        "pw.run.outcome",
        "pw.agent.id",
        "pw.agent.version",
        "pw.prompt.version",
        "pw.user.hash",
        # gen_ai.chat (GenAI semantic conventions, pinned with the SDK version)
        "gen_ai.system",
        "gen_ai.operation.name",
        "gen_ai.request.model",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "gen_ai.response.finish_reasons",
        "pw.model.purpose",
        "pw.model.outcome",
        "pw.cost.estimated",
        # policy.decision
        "pw.policy.action",
        "pw.policy.scope",
        "pw.policy.decision",
        "pw.policy.deny_reason",
        # tool.execute
        "pw.tool.name",
        "pw.tool.layer",
        "pw.tool.outcome",
        "pw.tool.row_count",
        "pw.tool.highlight_count",
        "pw.data_as_of",
        # db.query
        "pw.db.statement",
        "db.response.returned_rows",
        # ingest.batch
        "pw.ingest.records",
        "pw.ingest.skipped",
        "pw.ingest.cursor",
        # errors
        "error.type",
        "exception.type",
    }
)

# Identifiers and timestamps: never masked (a UUID or a date can look like a phone number).
UNMASKED = frozenset(
    {
        "pw.run.id",
        "pw.conversation.id",
        "pw.user.hash",
        "pw.trace.request_id",
        "pw.data_as_of",
        "pw.ingest.cursor",
    }
)

CONTENT_EVENTS = frozenset({"gen_ai.content.prompt", "gen_ai.content.completion"})
CONTENT_ATTRIBUTES = frozenset({"gen_ai.prompt", "gen_ai.completion"})

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
TOKEN = re.compile(r"(?i)bearer\s+[\w.~+/=-]+|eyJ[\w-]{4,}\.[\w-]{4,}\.[\w-]*")


class Masker:
    """Salted hashing of personal data; the salt is a per-environment secret."""

    def __init__(self, salt: bytes) -> None:
        self._salt = salt

    def hash(self, value: str) -> str:
        digest = hmac.new(self._salt, value.encode(), hashlib.sha256).hexdigest()
        return f"h:{digest[:16]}"

    def text(self, value: str, *, phones: bool = True) -> str:
        value = TOKEN.sub("[redacted]", value)
        value = EMAIL.sub(lambda m: self.hash(m.group(0).lower()), value)
        if phones:
            value = PHONE.sub(lambda m: self.hash(re.sub(r"\D", "", m.group(0))), value)
        return value

    def _item(self, value: object) -> object:
        return self.text(value) if isinstance(value, str) else value

    def value(self, key: str, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value, phones=key not in UNMASKED)
        if isinstance(value, (list, tuple)):
            return tuple(self._item(v) for v in cast("Sequence[object]", value))
        return value


def sanitize(span: ReadableSpan, masker: Masker, *, content: bool) -> ReadableSpan:
    """A copy of the span with only allowed, masked attributes and events."""
    attributes = {
        k: masker.value(k, v) for k, v in (span.attributes or {}).items() if k in ALLOWED_ATTRIBUTES
    }
    events: list[Event] = []
    for event in span.events:
        given = event.attributes or {}
        if event.name == "exception":
            kept = {"exception.type": given.get("exception.type", "")}
        elif event.name in CONTENT_EVENTS:
            if not content:
                continue
            kept = {
                k: masker.value(k, v) for k, v in (event.attributes or {}).items() if k in CONTENT_ATTRIBUTES
            }
        else:
            kept = {
                k: masker.value(k, v) for k, v in (event.attributes or {}).items() if k in ALLOWED_ATTRIBUTES
            }
        events.append(Event(event.name, kept, event.timestamp))
    # A status description is "Type: message"; the message can carry data, so only the type stays.
    description = span.status.description.split(":", 1)[0] if span.status.description else None
    return ReadableSpan(
        name=span.name,
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=attributes,
        events=events,
        links=span.links,
        kind=span.kind,
        status=Status(span.status.status_code, description),
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


class SanitizingExporter(SpanExporter):
    """Wraps the real exporter; nothing reaches it unsanitized."""

    def __init__(self, inner: SpanExporter, masker: Masker, *, content: bool) -> None:
        self._inner = inner
        self._masker = masker
        self._content = content

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return self._inner.export([sanitize(s, self._masker, content=self._content) for s in spans])

    def shutdown(self) -> None:
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._inner.force_flush(timeout_millis)
