"""tools.executor: the only way a tool handler runs (agent spec 4, 6, 7; baseline 4).

Every call, in order:
1. kill switch: the agent registry row is read fresh; not active stops the run;
2. arguments validated against the tool's input model (unknown fields refused);
3. policy decision from OPA (user role and agent scopes, tenant and site); unreachable denies;
4. the handler runs as `pw_agent_read` inside a transaction bound to the session's tenant;
5. one audit entry, whatever the outcome. If the audit cannot be written, the result is
   withheld: nothing leaves the executor unaudited.

The model gets short, fixed messages for failures, never exception text or policy internals.
"""

import json
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ValidationError

from palletwise import clock
from palletwise.contracts.results import RequestRejected, ResultEnvelope, WorkingSet
from palletwise.db.agent_repo import AgentRecord, AgentRegistry
from palletwise.db.audit_repo import AuditEntry, AuditLog
from palletwise.db.connection import Connection, Pool, tenant_session
from palletwise.policy import Decision, PolicyAgent, PolicyClient, PolicyRequest, PolicyUser
from palletwise.telemetry import metrics
from palletwise.telemetry.tracing import record_policy_decision, tracer

log = logging.getLogger(__name__)

MAX_AUDITED_ARGUMENTS = 2_000  # characters of raw, unvalidated arguments kept in the audit


class Outcome(StrEnum):
    OK = "OK"
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    DENIED = "DENIED"
    ERROR = "ERROR"
    SUSPENDED = "SUSPENDED"
    RATE_LIMITED = "RATE_LIMITED"


class RateLimit(Protocol):
    def allow(self, tenant: UUID, subject: str, key: str, per_minute: int) -> bool: ...


class ToolOutput(Protocol):
    @property
    def row_count(self) -> int: ...


@dataclass(frozen=True, slots=True)
class ToolContext:
    """What a handler may use: a read-only connection with the tenant already bound."""

    conn: Connection
    tenant: UUID
    site: UUID
    working_set: WorkingSet | None = None  # the previous answer's identifiers (semantic spec 7.5)
    # The run and the agent acting, for tools that must carry the agent's identity further (the
    # governed query tags its database session with them, ADR 0002 section 6a).
    run: "RunContext | None" = None
    agent: AgentRecord | None = None


@dataclass(frozen=True, slots=True)
class ToolSpec[I: BaseModel, O: ToolOutput]:
    name: str
    description: str
    input_model: type[I]
    scope: str
    side_effect: Literal["read", "propose"]
    handler: Callable[[ToolContext, I], O]
    rate_limit: int = 30  # calls per user per minute (agent spec 4)

    def __post_init__(self) -> None:
        # Unknown arguments must fail, not be ignored (evals spec: unexpected parameters).
        if self.input_model.model_config.get("extra") != "forbid":
            raise ValueError(f"tool {self.name}: input model must forbid extra fields")


class ToolRegistry:
    def __init__(self, tools: Iterable[ToolSpec[Any, Any]]) -> None:
        self._tools: dict[str, ToolSpec[Any, Any]] = {}
        for tool in tools:
            if tool.name in self._tools:
                raise ValueError(f"tool {tool.name} registered twice")
            self._tools[tool.name] = tool

    def get(self, name: str) -> ToolSpec[Any, Any] | None:
        return self._tools.get(name)

    def __iter__(self) -> Any:
        return iter(self._tools.values())


@dataclass(frozen=True, slots=True)
class RunContext:
    """One copilot run on behalf of one signed-in user. Identity comes from the session."""

    run_id: UUID
    trace_id: str
    subject: str
    role: str
    tenant: UUID
    sites: tuple[UUID, ...]
    site: UUID
    agent_id: str = "copilot"
    model: str | None = None
    # Loaded from the conversation store by the API, never from the model (semantic spec 7.5).
    working_set: WorkingSet | None = None


StopReason = Literal["suspended", "registry_unavailable"]


class RunStopped(Exception):
    """The run must end now (agent suspended or registry unreachable). Shown to the user."""

    def __init__(self, reason: StopReason) -> None:
        super().__init__(reason)
        self.reason = reason


class AuditUnavailable(Exception):
    """The audit entry could not be written, so the call's result is withheld."""


@dataclass(frozen=True, slots=True)
class ToolCallResult:
    outcome: Outcome
    tool: str
    output: Any = None
    message: str | None = None  # for the model when the call did not succeed
    reasons: tuple[str, ...] = field(default=())


MESSAGES = {
    Outcome.UNKNOWN_TOOL: "No tool with that name exists.",
    Outcome.DENIED: "This action is not permitted for this user.",
    Outcome.ERROR: "The tool failed. Do not retry with the same arguments.",
    Outcome.RATE_LIMITED: "Too many requests for this tool. Try again in a minute.",
}


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        agents: AgentRegistry,
        policy: PolicyClient,
        audit: AuditLog,
        read_pool: Pool,
        limiter: RateLimit | None = None,
    ) -> None:
        self._registry = registry
        self._agents = agents
        self._policy = policy
        self._audit = audit
        self._read_pool = read_pool
        self._limiter = limiter

    def execute(self, run: RunContext, name: str, arguments: Mapping[str, Any]) -> ToolCallResult:
        started = clock.monotonic()
        with tracer.start_as_current_span(
            "tool.execute", attributes={"pw.tool.name": name, "pw.tool.layer": "semantic"}
        ) as span:
            result, entry, stop = self._execute(run, name, arguments, started)
            span.set_attribute("pw.tool.outcome", result.outcome.value)
            output = result.output
            if isinstance(output, ResultEnvelope):
                span.set_attribute("pw.tool.row_count", output.row_count)
                span.set_attribute("pw.tool.highlight_count", len(output.highlights))
                if output.evidence.data_as_of is not None:
                    span.set_attribute("pw.data_as_of", output.evidence.data_as_of.isoformat())
        known = name if self._registry.get(name) is not None else "unknown"
        metrics.tool_calls.labels(tool=known, outcome=result.outcome.value).inc()
        try:
            self._audit.append(run.tenant, entry)
        except Exception as exc:
            log.exception("audit append failed for tool %s; result withheld", name)
            raise AuditUnavailable from exc
        if stop is not None:
            raise RunStopped(stop)
        if result.outcome is Outcome.SUSPENDED:
            raise RunStopped("suspended")
        return result

    def _execute(
        self, run: RunContext, name: str, arguments: Mapping[str, Any], started: float
    ) -> tuple[ToolCallResult, AuditEntry, StopReason | None]:
        """The result, its audit entry (every outcome is audited), and why the run must stop, if so."""
        spec = self._registry.get(name)

        def finish(
            outcome: Outcome,
            *,
            agent: AgentRecord | None = None,
            parameters: Any = None,
            decision: Decision | None = None,
            output: Any = None,
            message: str | None = None,
        ) -> tuple[ToolCallResult, AuditEntry, StopReason | None]:
            entry = AuditEntry(
                actor_type="AGENT", action="tool.call", outcome=outcome.value, trace_id=run.trace_id,
                site_id=run.site, user_subject=run.subject, user_role=run.role,
                agent_id=run.agent_id, agent_version=agent.version if agent else None,
                model=run.model, prompt_version=agent.prompt_version if agent else None,
                run_id=run.run_id, scope=spec.scope if spec else None, tool=name,
                parameters=parameters if parameters is not None else _raw_arguments(arguments),
                decision=None if decision is None else ("ALLOW" if decision.allow else "DENY"),
                deny_reasons=decision.reasons if decision and decision.reasons else None,
                row_count=output.row_count if output is not None else None,
                latency_ms=round((clock.monotonic() - started) * 1000),
            )  # fmt: skip
            reasons = decision.reasons if decision else ()
            text = message or MESSAGES.get(outcome)
            return ToolCallResult(outcome, name, output, text, reasons), entry, None

        if spec is None:
            return finish(Outcome.UNKNOWN_TOOL)

        # 1. Kill switch, read at call time so a suspension stops the run at its next step.
        try:
            agent = self._agents.load(run.tenant, run.agent_id)
        except Exception as exc:
            # The kill switch cannot be checked: fail closed, audited, and stop the run.
            log.warning("agent registry unavailable: %s", type(exc).__name__)
            result, entry, _ = finish(Outcome.ERROR, message="The copilot is unavailable.")
            return result, entry, "registry_unavailable"
        if agent is None or not agent.active:
            return finish(Outcome.SUSPENDED, agent=agent)

        # 2. Arguments.
        try:
            params = spec.input_model.model_validate(dict(arguments))
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(map(str, e['loc'])) or 'input'}: {e['msg']}" for e in exc.errors()
            )
            return finish(Outcome.VALIDATION_FAILED, agent=agent, message=f"Invalid arguments: {problems}")
        normalized = params.model_dump(mode="json")

        # Rate limit per user and tool, after validation so malformed calls do not count.
        if self._limiter is not None:
            try:
                allowed = self._limiter.allow(run.tenant, run.subject, f"tool:{name}", spec.rate_limit)
            except Exception as exc:  # cannot count: fail closed, audited
                log.warning("rate limiter unavailable: %s", type(exc).__name__)
                return finish(Outcome.ERROR, agent=agent, parameters=normalized)
            if not allowed:
                return finish(Outcome.RATE_LIMITED, agent=agent, parameters=normalized)

        # 3. Policy.
        decision = self._decide(run, spec, agent)
        if not decision.allow:
            return finish(Outcome.DENIED, agent=agent, parameters=normalized, decision=decision)

        # 4. Handler, as pw_agent_read, bound to the session's tenant.
        try:
            with tenant_session(self._read_pool, run.tenant, read_snapshot=True) as conn:
                context = ToolContext(conn, run.tenant, run.site, run.working_set, run, agent)
                output = spec.handler(context, params)
        except RequestRejected as exc:  # a reason the agent can act on, not a failure
            audited = {**normalized, **exc.audit} if exc.audit else normalized
            return finish(Outcome.VALIDATION_FAILED, agent=agent, parameters=audited, decision=decision,
                          message=str(exc))  # fmt: skip
        except Exception:
            log.exception("tool %s failed", name)
            return finish(Outcome.ERROR, agent=agent, parameters=normalized, decision=decision)
        if isinstance(output, ResultEnvelope) and "working_set_from" in output.normalized_request:
            # The answer narrowed a previous result: the audit names that result (semantic spec 7.5).
            normalized = {**normalized, "working_set_from": output.normalized_request["working_set_from"]}
        if isinstance(output, ResultEnvelope) and "audit" in output.normalized_request:
            # What the tool recorded about how it ran (the governed query's SQL, verdict, size).
            normalized = {**normalized, **output.normalized_request["audit"]}
        return finish(Outcome.OK, agent=agent, parameters=normalized, decision=decision, output=output)

    def _decide(self, run: RunContext, spec: ToolSpec[Any, Any], agent: AgentRecord) -> Decision:
        request = PolicyRequest(
            user=PolicyUser(run.subject, run.role, str(run.tenant), tuple(str(s) for s in run.sites)),
            action=spec.name,
            scope=spec.scope,
            resource_tenant=str(run.tenant),
            resource_site=str(run.site),
            agent=PolicyAgent(agent.id, agent.version, agent.status, agent.allowed_scopes),
        )
        with tracer.start_as_current_span("policy.decision") as span:
            decision = self._policy.decide(request)
            record_policy_decision(span, spec.name, spec.scope, decision.allow, decision.reasons)
        return decision


def _raw_arguments(arguments: Mapping[str, Any]) -> Any:
    """Unvalidated arguments as audited: JSON-safe and bounded in size."""
    try:
        text = json.dumps(dict(arguments), default=str, sort_keys=True)
    except (TypeError, ValueError):
        return {"unserializable": True}
    if len(text) > MAX_AUDITED_ARGUMENTS:
        return {"truncated": text[:MAX_AUDITED_ARGUMENTS]}
    return json.loads(text)
