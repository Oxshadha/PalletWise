"""The REST connector (ADR 0003): any WMS with a REST API, configured by a mapping file.

Each stream (one endpoint, one canonical record type) keeps its own position in the cursor: the
last change time for incremental endpoints, or the paging cursor for feed-style endpoints.
Records come back oldest first across streams. Rate limits and server errors are retried with
backoff, honouring Retry-After.
"""

import json
import os
import time
from collections.abc import Callable
from typing import Any, cast

import httpx

from palletwise.connectors.base import Batch, CanonicalRecord, Health
from palletwise.connectors.mapping import Mapping, Stream, convert, dig, timestamp

RETRIES = 4
RETRY_STATUS = {429, 500, 502, 503, 504}


class RestConnector:
    def __init__(
        self,
        mapping: Mapping,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.mapping = mapping
        self.source_system = mapping.source_system
        self._sleep = sleep
        headers: dict[str, str] = {"Accept": "application/json"}
        auth = mapping.auth
        if auth.type != "none" and auth.env:
            secret = os.environ.get(auth.env, "")
            headers[auth.header] = f"Bearer {secret}" if auth.type == "bearer" else secret
        self._client = httpx.Client(
            base_url=mapping.base_url, headers=headers, timeout=mapping.timeout_seconds, transport=transport
        )

    # ---------------------------------------------------------------- HTTP with retries

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        for attempt in range(RETRIES + 1):
            try:
                response = self._client.get(path, params=params)
            except httpx.TransportError:
                if attempt == RETRIES:
                    raise
                self._sleep(0.5 * 2**attempt)
                continue
            if response.status_code in RETRY_STATUS and attempt < RETRIES:
                wait = response.headers.get("Retry-After")
                self._sleep(float(wait) if wait and wait.replace(".", "", 1).isdigit() else 0.5 * 2**attempt)
                continue
            response.raise_for_status()
            return response.json()
        raise RuntimeError("unreachable")

    # ---------------------------------------------------------------- reading one stream

    def _read(
        self, stream: Stream, position: dict[str, Any], limit: int
    ) -> tuple[list[CanonicalRecord], dict[str, Any]]:
        paging = self.mapping.paging
        records: list[CanonicalRecord] = []
        page: Any = position.get("page", 1 if paging.style == "page" else None)
        since = position.get("since")
        while len(records) < limit:
            params: dict[str, Any] = {paging.size_param: paging.size}
            if page is not None:
                params[paging.param] = page
            if stream.since_param and since:
                params[stream.since_param] = since
            body = self._get(stream.path, params)
            items = dig(body, stream.items) if stream.items else body
            if not isinstance(items, list) or not items:
                break
            for item in items:  # pyright: ignore[reportUnknownVariableType]
                records.append(self._record(stream, item))
            if paging.style == "cursor":
                page = dig(body, paging.next)
                if not page:
                    break
            else:
                if len(items) < paging.size:  # pyright: ignore[reportUnknownArgumentType]
                    page = int(cast(int, page)) + 1
                    break
                page = int(cast(int, page)) + 1
        new_position = dict(position)
        if records:
            if stream.since_param:
                new_position["since"] = max(r.occurred_at for r in records).isoformat()
                new_position.pop("page", None)
            else:
                new_position["page"] = page
        return records, new_position

    def _record(self, stream: Stream, item: Any) -> CanonicalRecord:
        return CanonicalRecord(
            record_type=stream.record_type,
            occurred_at=timestamp(dig(item, stream.occurred_at)),
            payload={name: convert(rule, item) for name, rule in stream.fields.items()},
            source_system=self.source_system,
            external_id=str(dig(item, stream.id)),
        )

    # ---------------------------------------------------------------- the connector contract

    def pull(self, cursor: str | None, limit: int) -> Batch:
        positions: dict[str, dict[str, Any]] = json.loads(cursor) if cursor else {}
        records: list[CanonicalRecord] = []
        for stream in self.mapping.streams:
            got, positions[stream.record_type] = self._read(
                stream, positions.get(stream.record_type, {}), max(1, limit - len(records))
            )
            records.extend(got)
            if len(records) >= limit:
                break
        records.sort(key=lambda r: r.occurred_at)
        return Batch(records=records, cursor=json.dumps(positions, sort_keys=True))

    def health(self) -> Health:
        streams: dict[str, str] = {}
        for stream in self.mapping.streams:
            try:
                self._get(stream.path, {self.mapping.paging.size_param: 1})
                streams[stream.record_type] = "ok"
            except (httpx.HTTPError, ValueError) as exc:
                streams[stream.record_type] = f"error: {type(exc).__name__}"
        return Health(ok=all(v == "ok" for v in streams.values()), streams=streams)
