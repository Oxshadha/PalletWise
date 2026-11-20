"""Copilot endpoints (agent spec 3.1, 9.1, 12): runs as an event stream, cancel, history, feedback.

Pre-checks run in the spec's order before anything streams: session and rate limit, site
ready, agent active, then the copilot's delegated identity. Every run is stored and audited
when it ends, including cancelled and failed runs.
"""

import asyncio
import hashlib
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sse_starlette import EventSourceResponse

from palletwise import clock
from palletwise.agent.events import RunEvent
from palletwise.agent.guardrails import QuestionRejected, normalize_question
from palletwise.agent.runtime import (
    AgentProfile,
    CopilotRuntime,
    HistoryTurn,
    ModelCall,
    RunRequest,
    RunState,
)
from palletwise.agent.suggestions import Suggestion
from palletwise.api.problems import problem
from palletwise.api.security import CurrentPrincipal, auth_service
from palletwise.auth.oidc import IdentityProviderUnavailable
from palletwise.auth.principal import Principal
from palletwise.auth.service import AuthService, DelegationRefused, SessionEnded
from palletwise.db import status_repo
from palletwise.db.agent_repo import AgentRecord, AgentRegistry
from palletwise.db.audit_repo import AuditEntry, AuditLog
from palletwise.db.cache_repo import WordingCacheStore
from palletwise.db.connection import Pool
from palletwise.db.copilot_repo import (
    ConversationStore,
    Owner,
    RateLimiter,
    RunOutcome,
    UsageLedger,
    UsageRecord,
)
from palletwise.llm import estimated_cost
from palletwise.semantic.model import semantic_model
from palletwise.telemetry import metrics
from palletwise.telemetry.setup import user_hash
from palletwise.telemetry.tracing import current_trace_id, tracer
from palletwise.tools.catalog import BIN_CODE_PATTERN
from palletwise.tools.executor import RunContext

log = logging.getLogger(__name__)

RUNS_PER_MINUTE = 20
HISTORY_TURNS = 6
HISTORY_CHARS = 8_000  # about 2,000 tokens (agent spec 5)
DISCONNECT_GRACE_SECONDS = 10.0  # agent spec 9.1
CANCEL_POLL_SECONDS = 1.0

router = APIRouter(prefix="/api/copilot", tags=["copilot"])


# ---------------------------------------------------------------- request and response models


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SuggestionIn(Strict):
    label: str = Field(max_length=200)
    tool: str = Field(pattern=r"^[a-z][a-z_]{0,63}$")
    arguments: dict[str, str | int | bool | None] = Field(max_length=10)


class PageContext(Strict):
    """What the user is looking at. Context for the model only, never used to authorize."""

    selected_bin: str | None = Field(default=None, pattern=BIN_CODE_PATTERN)
    selected_zone: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9-]{0,31}$")


class RunIn(Strict):
    site_id: UUID
    question: str = Field(max_length=4_000)  # normalized and limited to 1,000 by the guardrails
    conversation_id: UUID | None = None
    suggestion: SuggestionIn | None = None
    page_context: PageContext | None = None


class FeedbackIn(Strict):
    rating: Literal["HELPFUL", "NOT_HELPFUL"]
    comment: str | None = Field(default=None, max_length=1_000)


class ConversationOut(BaseModel):
    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


class RunOut(BaseModel):
    id: UUID
    seq: int
    question: str
    path: str | None
    status: str
    outcome: str | None
    answer_text: str | None
    results: list[Any]
    suggestions: list[Any]
    trace_id: str
    created_at: datetime
    finished_at: datetime | None


class ConversationDetailOut(ConversationOut):
    runs: list[RunOut]


# ---------------------------------------------------------------- services


@dataclass
class CopilotServices:
    runtime: CopilotRuntime
    agents: AgentRegistry
    conversations: ConversationStore
    ledger: UsageLedger
    limiter: RateLimiter
    audit: AuditLog
    app_pool: Pool
    active: dict[UUID, "asyncio.Task[None]"] = field(default_factory=lambda: {})
    # Run records being written; shutdown waits for them before closing the pools.
    finishing: set["asyncio.Future[None]"] = field(default_factory=lambda: set())

    async def drain(self, timeout: float) -> None:
        """Stops active runs and waits for every run record to be written (graceful shutdown)."""
        for task in list(self.active.values()):
            task.cancel()
        pending = {*self.active.values(), *self.finishing}
        if pending:
            await asyncio.wait(pending, timeout=timeout)


def services(request: Request) -> CopilotServices:
    found: CopilotServices = request.app.state.copilot
    return found


Services = Annotated[CopilotServices, Depends(services)]
Auth = Annotated[AuthService, Depends(auth_service)]


@dataclass
class WordingCacheAdapter:
    """The runtime's WordingCache port on the PostgreSQL store (agent spec 14b, Slice 2 D4).

    Adds the semantic model's version to the runtime's key material, then hashes it: the store keeps
    only the hash. The material already holds tenant, site, tool, arguments, result hash, question,
    prompt, model, template, settings and cache versions, locale and units.
    """

    store: WordingCacheStore

    @staticmethod
    def key(material: dict[str, Any]) -> str:
        canonical = json.dumps({**material, "semantic_model": semantic_model().version}, sort_keys=True,
                               default=str)  # fmt: skip
        return hashlib.sha256(canonical.encode()).hexdigest()

    def get(self, tenant: UUID, site: UUID, material: dict[str, Any]) -> str | None:
        return self.store.get(tenant, self.key(material))

    def put(self, tenant: UUID, site: UUID, material: dict[str, Any], tool: str, wording: str) -> None:
        self.store.put(tenant, site, self.key(material), tool, wording)


@dataclass
class LedgerMeter:
    """ModelMeter for one run: atomic budget reservation and one usage record per model call."""

    ledger: UsageLedger
    owner: Owner
    run_id: UUID
    agent: AgentRecord
    day: date
    cost: Decimal = Decimal(0)

    def reserve(self, estimate: int) -> bool:
        return self.ledger.reserve(
            self.owner.tenant, self.agent.id, self.day, estimate, self.agent.daily_token_budget
        )

    def settle(self, estimate: int, call: ModelCall) -> Decimal:
        used = call.input_tokens + call.output_tokens
        self.ledger.settle(self.owner.tenant, self.agent.id, self.day, estimate, used)
        cost = estimated_cost(
            call.model, call.input_tokens, call.output_tokens, call.cached_tokens, clock.wall_now().date()
        )
        self.cost += cost
        self.ledger.record(
            self.owner.tenant,
            UsageRecord(
                site_id=self.owner.site,
                run_id=self.run_id,
                agent_id=self.agent.id,
                agent_version=self.agent.version,
                purpose=call.purpose,
                model=call.model,
                region=call.region,
                input_tokens=call.input_tokens,
                output_tokens=call.output_tokens,
                cached_tokens=call.cached_tokens,
                cost_estimated=cost,
                latency_ms=call.latency_ms,
                outcome=call.outcome,
                stage=call.stage,
                usage_estimated=call.usage_estimated,
            ),
        )
        return cost


def _owner(principal: Principal, site_id: UUID) -> Owner | None:
    """The caller's own scope; None when the site is not one of the session's sites."""
    if not principal.may_see_site(site_id):
        return None
    return Owner(principal.tenant, principal.identity.subject, site_id)


def _history(turns: list[Any]) -> list[HistoryTurn]:
    """The last turns, oldest dropped first, within the history budget."""
    kept: list[HistoryTurn] = []
    size = 0
    for turn in reversed(turns):
        size += len(turn.question) + len(turn.answer or "")
        if size > HISTORY_CHARS:
            break
        kept.append(HistoryTurn(turn.question, turn.answer))
    return list(reversed(kept))


def _sse(event: RunEvent) -> dict[str, str]:
    return {
        "event": event.type,
        "id": str(event.seq),
        "data": json.dumps({"seq": event.seq, "type": event.type, **event.data}, default=str),
    }


# ---------------------------------------------------------------- runs


@router.post(
    "/runs",
    response_class=EventSourceResponse,
    responses={
        200: {"content": {"text/event-stream": {}}, "description": "Run events (agent spec 9.1)"},
        403: {"description": "FORBIDDEN"},
        404: {"description": "NOT_FOUND"},
        422: {"description": "VALIDATION_FAILED"},
        429: {"description": "RATE_LIMITED"},
        503: {"description": "DATA_NOT_READY, COPILOT_SUSPENDED or UNAVAILABLE"},
    },
)
async def start_run(body: RunIn, principal: CurrentPrincipal, svc: Services, auth: Auth) -> Response:
    trace_id = current_trace_id()
    owner = _owner(principal, body.site_id)
    if owner is None:
        return problem(404, "NOT_FOUND", "Not found.", trace_id)
    identity = principal.identity

    # 1. Rate limit, then the question itself.
    if not await asyncio.to_thread(
        svc.limiter.allow, owner.tenant, owner.subject, "copilot.run", RUNS_PER_MINUTE
    ):
        return problem(
            429, "RATE_LIMITED", "Too many questions. Try again in a minute.", trace_id, {"Retry-After": "60"}
        )
    try:
        question = normalize_question(body.question)
    except QuestionRejected as exc:
        return problem(422, "VALIDATION_FAILED", str(exc), trace_id)

    # 2. The site's data must be ready (no answers on partial data).
    site = await asyncio.to_thread(_site_status, svc.app_pool, owner)
    if site is None:
        return problem(404, "NOT_FOUND", "Not found.", trace_id)
    if site.data_status != "READY":
        return problem(503, "DATA_NOT_READY", "The warehouse data is still loading.", trace_id)

    # 3. The agent must be active (kill switch; rechecked on every tool call).
    agent = await asyncio.to_thread(svc.agents.load, owner.tenant, "copilot")
    if agent is None or not agent.active:
        return problem(503, "COPILOT_SUSPENDED", "The copilot has been paused by an administrator.", trace_id)

    # 4. The copilot acts for this user only through a validated token exchange.
    try:
        await asyncio.to_thread(auth.delegate_to_copilot, principal)
    except DelegationRefused:
        return problem(403, "FORBIDDEN", "The copilot may not act for this account.", trace_id)
    except SessionEnded:
        return problem(401, "UNAUTHENTICATED", "Please sign in again.", trace_id)
    except IdentityProviderUnavailable:
        return problem(503, "UNAVAILABLE", "The sign-in service is not reachable right now.", trace_id)

    if body.conversation_id is not None and not await asyncio.to_thread(
        svc.conversations.owns, owner, body.conversation_id
    ):
        return problem(404, "NOT_FOUND", "Not found.", trace_id)
    conversation_id, run_id = await asyncio.to_thread(
        svc.conversations.start_run,
        owner,
        body.conversation_id,
        question,
        trace_id,
        agent.id,
        agent.version,
        agent.prompt_version,
    )
    history = await asyncio.to_thread(svc.conversations.recent_turns, owner, conversation_id, HISTORY_TURNS)
    # The previous answer's identifiers, for "of those" follow-ups (semantic spec 7.5). Results
    # from before the current simulation run (a reset) no longer count.
    working_set = (
        await asyncio.to_thread(svc.conversations.working_set, owner, conversation_id, site.data_since)
        if body.conversation_id is not None
        else None
    )

    run_request = RunRequest(
        question=question,
        run=RunContext(
            run_id=run_id,
            trace_id=trace_id,
            subject=identity.subject,
            role=identity.role,
            tenant=owner.tenant,
            sites=identity.sites,
            site=owner.site,
            model=agent.primary_model,
            working_set=working_set,
        ),
        agent=AgentProfile(
            agent.id,
            agent.version,
            agent.primary_model,
            agent.fallback_model,
            agent.prompt_version,
            agent.max_steps,
        ),
        history=_history(history),
        page_context=body.page_context.model_dump(exclude_none=True) if body.page_context else None,
        suggestion=Suggestion(body.suggestion.label, body.suggestion.tool, dict(body.suggestion.arguments))
        if body.suggestion
        else None,
    )
    day = clock.wall_now().astimezone(ZoneInfo(site.time_zone)).date()
    meter = LedgerMeter(svc.ledger, owner, run_id, agent, day)

    channel: asyncio.Queue[RunEvent | None] = asyncio.Queue()
    task = asyncio.create_task(_drive(svc, run_request, meter, owner, channel, conversation_id))
    svc.active[run_id] = task

    async def stream() -> AsyncIterator[dict[str, str]]:
        try:
            while (event := await channel.get()) is not None:
                yield _sse(event)
        finally:
            if not task.done():  # client gone: cancel after the grace period (agent spec 9.1)
                asyncio.get_running_loop().call_later(DISCONNECT_GRACE_SECONDS, task.cancel)

    headers = {
        "X-Run-Id": str(run_id),
        "X-Conversation-Id": str(conversation_id),
        "X-Trace-Id": trace_id,
        "Cache-Control": "no-store",
    }
    return EventSourceResponse(stream(), headers=headers, ping=15)


def _site_status(pool: Pool, owner: Owner) -> status_repo.SiteStatus | None:
    with pool.connection() as conn:
        return status_repo.site_status(conn, owner.tenant, owner.site)


async def _drive(
    svc: CopilotServices,
    request: RunRequest,
    meter: LedgerMeter,
    owner: Owner,
    channel: "asyncio.Queue[RunEvent | None]",
    conversation_id: UUID,
) -> None:
    """Runs the copilot, forwards its events, then stores and audits the run whatever happens."""
    state = RunState()
    run_status = "COMPLETED"
    started = clock.monotonic()
    first: dict[str, float] = {}
    watcher = asyncio.create_task(_watch_cancel(svc, owner, request.run.run_id, asyncio.current_task()))
    try:
        attributes = {
            "pw.run.id": str(request.run.run_id),
            "pw.conversation.id": str(conversation_id),
            "pw.agent.id": request.agent.id,
            "pw.agent.version": request.agent.version,
            "pw.prompt.version": request.agent.prompt_version,
            "pw.user.hash": user_hash(request.run.subject),
        }
        with tracer.start_as_current_span("copilot.run", attributes=attributes) as span:
            try:
                async for event in svc.runtime.run(request, meter, state):
                    marker = FIRST_MARKERS.get(event.type)
                    if marker and marker not in first:
                        first[marker] = clock.monotonic() - started
                    await channel.put(event)
            finally:
                span.set_attribute("pw.run.path", state.path or "none")
                span.set_attribute("pw.run.outcome", state.outcome or "none")
    except asyncio.CancelledError:
        run_status = "CANCELLED"
        raise
    finally:
        watcher.cancel()
        if run_status != "CANCELLED" and state.outcome == "ERROR":
            run_status = "FAILED"
        latency = round((clock.monotonic() - started) * 1000)
        _observe_run(state, run_status, latency, first)
        record = asyncio.ensure_future(
            asyncio.to_thread(_record, svc, request, meter, owner, state, run_status, latency)
        )
        svc.finishing.add(record)
        record.add_done_callback(svc.finishing.discard)
        try:
            # Shielded: a second cancel (a late cancel request, the disconnect grace) cannot stop
            # the record, and the cleanup below still runs.
            await asyncio.shield(record)
        finally:
            svc.active.pop(request.run.run_id, None)
            channel.put_nowait(None)


FIRST_MARKERS = {"pw.result": "highlight", "TEXT_MESSAGE_CONTENT": "text"}


def _observe_run(state: RunState, run_status: str, latency_ms: int, first: dict[str, float]) -> None:
    """Run metrics (evals spec 12); labels are fixed enumerations only."""
    path = state.path or "none"
    outcome = "CANCELLED" if run_status == "CANCELLED" else (state.outcome or "none")
    metrics.copilot_runs.labels(path=path, outcome=outcome).inc()
    metrics.run_duration.labels(path=path, outcome=outcome).observe(latency_ms / 1000)
    if "highlight" in first:
        metrics.time_to_first_highlight.labels(path=path).observe(first["highlight"])
    if "text" in first:
        metrics.time_to_first_text.labels(path=path).observe(first["text"])


async def _watch_cancel(
    svc: CopilotServices, owner: Owner, run_id: UUID, task: "asyncio.Task[Any] | None"
) -> None:
    """Cancel requests may arrive at another API instance; they are seen here within a second."""
    while task is not None and not task.done():
        await asyncio.sleep(CANCEL_POLL_SECONDS)
        if await asyncio.to_thread(svc.conversations.cancel_requested, owner, run_id):
            task.cancel()
            return


def _record(
    svc: CopilotServices,
    request: RunRequest,
    meter: LedgerMeter,
    owner: Owner,
    state: RunState,
    run_status: str,
    latency_ms: int,
) -> None:
    try:
        svc.conversations.finish_run(
            owner,
            request.run.run_id,
            RunOutcome(
                status=run_status,
                path=state.path,
                outcome=state.outcome,
                answer_text=state.answer_text,
                results=[r.model_dump(mode="json") for _, r in state.results],
                suggestions=[s.as_json() for s in state.suggestions],
                entities=state.entities[:500],
                error_code=state.error_code,
            ),
        )
    except Exception:
        log.exception("could not store copilot run %s", request.run.run_id)
    try:
        svc.audit.append(
            owner.tenant,
            AuditEntry(
                actor_type="AGENT",
                action="copilot.run",
                outcome=state.outcome or run_status,
                trace_id=request.run.trace_id,
                site_id=owner.site,
                user_subject=request.run.subject,
                user_role=request.run.role,
                agent_id=request.agent.id,
                agent_version=request.agent.version,
                model=request.agent.primary_model,
                prompt_version=request.agent.prompt_version,
                run_id=request.run.run_id,
                parameters={
                    "path": state.path,
                    "status": run_status,
                    "error_code": state.error_code,
                    "budget_exhausted": state.budget_exhausted,
                    "tool_calls": len(state.results),
                },
                row_count=sum(r.row_count for _, r in state.results),
                latency_ms=latency_ms,
                input_tokens=state.input_tokens,
                output_tokens=state.output_tokens,
                cost_estimated=meter.cost,
            ),
        )
    except Exception:
        log.exception("could not audit copilot run %s", request.run.run_id)


@router.post("/runs/{run_id}/cancel", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_run(
    run_id: UUID, site_id: Annotated[UUID, Query()], principal: CurrentPrincipal, svc: Services
) -> Response:
    trace_id = current_trace_id()
    owner = _owner(principal, site_id)
    if owner is None or not await asyncio.to_thread(svc.conversations.request_cancel, owner, run_id):
        return problem(404, "NOT_FOUND", "No running answer with that id.", trace_id)
    if (task := svc.active.get(run_id)) is not None:  # on this instance: stop at once
        task.cancel()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/runs/{run_id}/feedback", status_code=status.HTTP_204_NO_CONTENT)
async def feedback(
    run_id: UUID,
    body: FeedbackIn,
    site_id: Annotated[UUID, Query()],
    principal: CurrentPrincipal,
    svc: Services,
) -> Response:
    owner = _owner(principal, site_id)
    if owner is None or not await asyncio.to_thread(
        svc.conversations.add_feedback, owner, run_id, body.rating, body.comment
    ):
        return problem(404, "NOT_FOUND", "Not found.", current_trace_id())
    metrics.feedback.labels(rating=body.rating).inc()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class SuggestionOut(BaseModel):
    label: str
    tool: str
    arguments: dict[str, str | int | bool | None]


@router.get("/starters", response_model=list[SuggestionOut])
async def starters(principal: CurrentPrincipal, svc: Services) -> Any:
    """Starter suggestions for an empty conversation (UX spec 6): only built capabilities."""
    del principal  # signed-in users only; the list is the same for everyone
    return [SuggestionOut(label=s.label, tool=s.tool, arguments=s.arguments) for s in svc.runtime.starters()]


# ---------------------------------------------------------------- history


@router.get("/conversations", response_model=list[ConversationOut])
async def conversations(site_id: Annotated[UUID, Query()], principal: CurrentPrincipal, svc: Services) -> Any:
    owner = _owner(principal, site_id)
    if owner is None:
        return problem(404, "NOT_FOUND", "Not found.", current_trace_id())
    found = await asyncio.to_thread(svc.conversations.conversations, owner)
    return [
        ConversationOut(id=c.id, title=c.title, created_at=c.created_at, updated_at=c.updated_at)
        for c in found
    ]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailOut)
async def conversation(
    conversation_id: UUID, site_id: Annotated[UUID, Query()], principal: CurrentPrincipal, svc: Services
) -> Any:
    owner = _owner(principal, site_id)
    found = await asyncio.to_thread(svc.conversations.conversation, owner, conversation_id) if owner else None
    if found is None:
        return problem(404, "NOT_FOUND", "Not found.", current_trace_id())
    head, runs = found
    return ConversationDetailOut(
        id=head.id,
        title=head.title,
        created_at=head.created_at,
        updated_at=head.updated_at,
        runs=[RunOut(**asdict(r)) for r in runs],
    )
