"""Site live stream (agent spec 9.2, slice-3-acceptance 2.1): `GET /api/sites/{id}/events`.

Hints, never data. The truth is the site's change sequence in the database (migration
0022_site_changes): every change a screen shows advances it in the same transaction. A
notification only wakes the stream; the stream then reads the sequence and the kinds changed
since the last event it sent. So a lost notification costs at most one heartbeat (15 seconds),
never a stale screen, and a reconnecting client (`Last-Event-ID`) is told what it missed, or to
refetch everything when the history no longer reaches back.

Events: `hello` on connect (the client refetches its snapshots), `change` with the kinds,
`heartbeat` with the current sequence, `end` when the session is over. The event ID is always the
site's sequence, so the client ignores anything at or below what it has applied.
"""

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Request
from fastapi.responses import StreamingResponse

from palletwise.api.security import SESSION_COOKIE, CurrentPrincipal, auth_service
from palletwise.api.sites import Reads, authorize_site
from palletwise.auth.service import SessionEnded
from palletwise.db.site_changes import changes_since, notifications

log = logging.getLogger("palletwise.api.events")

router = APIRouter(prefix="/api/sites/{site_id}", tags=["map"])

HEARTBEAT_SECONDS = 15.0
COALESCE_SECONDS = 0.25  # a burst of changes becomes one event
ALL_KINDS = ("clock", "data_status", "exceptions", "inventory")


class SiteChangeHub:
    """One LISTEN connection per API process. It wakes the streams of a site; nothing more."""

    def __init__(self, role_env_prefix: str = "PW_APP") -> None:
        self._role = role_env_prefix
        self._waiting: dict[UUID, set[asyncio.Event]] = {}
        self._task: asyncio.Task[None] | None = None

    def subscribe(self, site: UUID) -> asyncio.Event:
        self.start()  # connects on first use, in the serving event loop
        event = asyncio.Event()
        self._waiting.setdefault(site, set()).add(event)
        return event

    def unsubscribe(self, site: UUID, event: asyncio.Event) -> None:
        waiting = self._waiting.get(site)
        if waiting is not None:
            waiting.discard(event)
            if not waiting:
                del self._waiting[site]

    def _wake(self, site: UUID | None = None) -> None:
        for s, events in list(self._waiting.items()):
            if site is None or s == site:
                for event in events:
                    event.set()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._listen())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _listen(self) -> None:
        backoff = 1.0
        while True:
            try:
                async for payload in notifications(self._role):
                    if not payload:  # listening again: anything missed meanwhile is reconciled
                        backoff = 1.0
                        self._wake()
                        continue
                    with contextlib.suppress(ValueError):
                        self._wake(UUID(payload))
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("site change listener lost its connection; reconnecting", exc_info=True)
                self._wake()  # streams fall back to their heartbeat reads meanwhile
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)


def _event(kind: str, seq: int, data: dict[str, object]) -> str:
    return f"event: {kind}\nid: {seq}\ndata: {json.dumps(data)}\n\n"


def _last_event_id(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return max(int(value), 0)
    except ValueError:
        return None


@router.get(
    "/events",
    responses={
        200: {"content": {"text/event-stream": {}}, "description": "Site change hints (agent spec 9.2)"}
    },
)
async def site_events(
    site_id: UUID,
    request: Request,
    principal: CurrentPrincipal,
    reads: Reads,
    last_event_id: Annotated[str | None, Header()] = None,
) -> StreamingResponse:
    if (refused := await authorize_site(principal, site_id, "site.events", reads)) is not None:
        return refused  # type: ignore[return-value]
    hub: SiteChangeHub = request.app.state.site_changes
    auth = auth_service(request)
    cookie = request.cookies.get(SESSION_COOKIE)
    tenant = principal.identity.tenant
    resumed = _last_event_id(last_event_id)

    async def still_allowed() -> bool:
        """The session is still valid and the policy still allows this site (2.1 rule 3)."""
        try:
            current = await asyncio.to_thread(auth.authenticate, cookie)
        except SessionEnded:
            return False
        return await authorize_site(current, site_id, "site.events", reads) is None

    async def stream() -> AsyncIterator[str]:
        wake = hub.subscribe(site_id)
        sent: int | None = resumed
        heartbeat = False
        try:
            while not await request.is_disconnected():
                if heartbeat and not await still_allowed():
                    yield _event("end", sent or 0, {"reason": "not_allowed"})
                    return
                now = await asyncio.to_thread(changes_since, reads.app_pool, tenant, site_id, sent or 0)
                if sent is None:
                    yield _event("hello", now.seq, {"seq": now.seq})
                elif now.seq != sent:
                    kinds = now.kinds if now.complete else ALL_KINDS
                    yield _event("change", now.seq, {"kinds": list(kinds), "complete": now.complete})
                elif heartbeat:
                    yield _event("heartbeat", now.seq, {"seq": now.seq})
                sent = now.seq
                wake.clear()
                try:
                    await asyncio.wait_for(wake.wait(), HEARTBEAT_SECONDS)
                    heartbeat = False
                    await asyncio.sleep(COALESCE_SECONDS)
                except TimeoutError:
                    # No wake-up for a whole interval: check the session and policy, read the
                    # sequence (a lost notification is found here), and send a heartbeat.
                    heartbeat = True
        finally:
            hub.unsubscribe(site_id, wake)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
