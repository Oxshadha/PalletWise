"""Admin panel core (FR-ADM-03, 04, 05, 06, 07; FR-COP-14): the copilot's status and kill switch,
usage and cost, unanswered questions, answer feedback, site settings, and the audit trail with CSV
export.

Tenant-wide views. The policy decides on a site, so each request is decided for the first site of
the Admin's session (an Admin of a tenant with no site has nothing to administer here). Scopes:
`admin.agents.manage` for the agent and its switch, `admin.usage.read` for usage and unanswered
questions, `admin.audit.read` for the audit trail. Reads as pw_admin_read; the switch is audited in
its own transaction.
"""

import asyncio
import csv
import io
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, Query, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from palletwise import clock
from palletwise.api.problems import problem
from palletwise.api.security import CurrentPrincipal
from palletwise.api.sites import Reads, authorize_site
from palletwise.auth.principal import Principal
from palletwise.db import admin_repo
from palletwise.db.audit_repo import AuditEntry
from palletwise.db.simulation_repo import site_simulation
from palletwise.governed import tool as governed
from palletwise.telemetry.tracing import current_trace_id

router = APIRouter(prefix="/api/admin", tags=["admin"])

AGENT = "copilot"
IDEMPOTENCY_KEY = r"^[A-Za-z0-9_-]{8,128}$"


async def authorize(principal: Principal, action: str, scope: str, reads: Reads) -> Response | None:
    sites = principal.identity.sites
    if not sites:
        return problem(403, "FORBIDDEN", "Your account has no site to administer.", current_trace_id())
    return await authorize_site(
        principal, sites[0], action, reads, scope=scope,
        refusal="You do not have permission for the admin panel.",
    )  # fmt: skip


def _since(days: int) -> datetime:
    # Usage and audit times are wall-clock times (when calls happened), not site time.
    return clock.wall_now() - timedelta(days=days)


class AgentOut(BaseModel):
    id: str
    version: str
    status: Literal["ACTIVE", "SUSPENDED"]
    primary_model: str
    prompt_version: str
    daily_token_budget: int
    used_tokens_today: int
    governed_query_enabled: bool  # custom queries on (ADR 0002); they also need a passed gate
    # True when a configuration has passed the evaluation gate, so the model is offered the tool.
    governed_query_evaluated: bool


class StatusIn(BaseModel):
    status: Literal["ACTIVE", "SUSPENDED"]


class StatusOut(BaseModel):
    outcome: Literal["changed", "unchanged"]
    status: Literal["ACTIVE", "SUSPENDED"]


@router.get("/agent", response_model=AgentOut)
async def get_agent(principal: CurrentPrincipal, reads: Reads) -> Any:
    if (refused := await authorize(principal, "admin.agent.read", "admin.agents.manage", reads)) is not None:
        return refused
    # The budget counts wall-clock days in the site's time zone (copilot.LedgerMeter).
    site = await asyncio.to_thread(
        site_simulation, reads.app_pool, principal.tenant, principal.identity.sites[0]
    )
    zone = ZoneInfo(site.time_zone) if site else UTC
    day = clock.wall_now().astimezone(zone).date().isoformat()
    found = await asyncio.to_thread(admin_repo.agent, principal.tenant, AGENT, day)
    if found is None:
        return problem(404, "NOT_FOUND", "Not found.", current_trace_id())
    return AgentOut(**found, governed_query_evaluated=bool(governed.passed_fingerprints()))


@router.put("/agent/status", response_model=StatusOut)
async def set_agent_status(
    body: StatusIn,
    principal: CurrentPrincipal,
    reads: Reads,
    idempotency_key: Annotated[str, Header(pattern=IDEMPOTENCY_KEY)],
) -> Any:
    """The kill switch (FR-ADM-03): suspending stops new copilot requests, fast path included, on
    the next request (the registry is read per request and per tool call)."""
    if (
        refused := await authorize(principal, "admin.agent.status", "admin.agents.manage", reads)
    ) is not None:
        return refused
    identity, trace_id = principal.identity, current_trace_id()

    def entry(change: admin_repo.StatusChange) -> AuditEntry:
        return AuditEntry(
            actor_type="USER", action="agent.status", outcome=change.outcome.upper(), trace_id=trace_id,
            user_subject=identity.subject, user_role=identity.role, scope="admin.agents.manage",
            decision="ALLOW", agent_id=AGENT,
            parameters={"before": change.before, "after": change.after, "idempotency_key": idempotency_key},
        )  # fmt: skip

    change = await asyncio.to_thread(
        admin_repo.set_status, reads.app_pool, principal.tenant, AGENT, body.status, entry
    )
    if change.outcome == "not_found":
        return problem(404, "NOT_FOUND", "Not found.", trace_id)
    return StatusOut(outcome=change.outcome, status=body.status)  # pyright: ignore[reportArgumentType]


class GovernedIn(BaseModel):
    enabled: bool


class GovernedOut(BaseModel):
    outcome: Literal["changed", "unchanged"]
    enabled: bool


@router.put("/agent/governed-query", response_model=GovernedOut)
async def set_governed_query(
    body: GovernedIn,
    principal: CurrentPrincipal,
    reads: Reads,
    idempotency_key: Annotated[str, Header(pattern=IDEMPOTENCY_KEY)],
) -> Any:
    """Custom queries on or off for the organisation (ADR 0002 section 7, D5); audited."""
    refused = await authorize(principal, "admin.agent.governed_query", "admin.agents.manage", reads)
    if refused is not None:
        return refused
    identity, trace_id = principal.identity, current_trace_id()

    def entry(change: admin_repo.StatusChange) -> AuditEntry:
        return AuditEntry(
            actor_type="USER", action="agent.governed_query", outcome=change.outcome.upper(),
            trace_id=trace_id, user_subject=identity.subject, user_role=identity.role,
            scope="admin.agents.manage", decision="ALLOW", agent_id=AGENT,
            parameters={"before": change.before, "after": change.after, "idempotency_key": idempotency_key},
        )  # fmt: skip

    change = await asyncio.to_thread(
        admin_repo.set_governed_query, reads.app_pool, principal.tenant, AGENT, body.enabled, entry
    )
    if change.outcome == "not_found":
        return problem(404, "NOT_FOUND", "Not found.", trace_id)
    return GovernedOut(outcome=change.outcome, enabled=body.enabled)  # pyright: ignore[reportArgumentType]


class UsageRow(BaseModel):
    day: str
    model: str
    purpose: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_estimated: float
    avg_latency_ms: int
    errors: int


@router.get("/usage", response_model=list[UsageRow])
async def get_usage(
    principal: CurrentPrincipal, reads: Reads, days: Annotated[int, Query(ge=1, le=90)] = 7
) -> Any:
    """Model calls by day, model and purpose. Cost is estimated: provider-reported tokens times the
    configured price table (FR-ADM-06)."""
    if (refused := await authorize(principal, "admin.usage.read", "admin.usage.read", reads)) is not None:
        return refused
    rows = await asyncio.to_thread(admin_repo.usage, principal.tenant, _since(days))
    return [UsageRow(**{**r, "day": r["day"].isoformat()}) for r in rows]


class UnansweredRow(BaseModel):
    question: str
    times: int
    last_asked: datetime
    reasons: list[str]


@router.get("/unanswered", response_model=list[UnansweredRow])
async def get_unanswered(
    principal: CurrentPrincipal, reads: Reads, days: Annotated[int, Query(ge=1, le=90)] = 30
) -> Any:
    """Declined or failed questions with their reasons and how often they were asked (FR-ADM-07)."""
    if (
        refused := await authorize(principal, "admin.unanswered.read", "admin.usage.read", reads)
    ) is not None:
        return refused
    rows = await asyncio.to_thread(
        admin_repo.unanswered, principal.tenant, _since(days), principal.identity.subject
    )
    return [UnansweredRow(**r) for r in rows]


# ---------------------------------------------------------------- answer feedback (FR-COP-14)


class FeedbackRow(BaseModel):
    created_at: datetime
    rating: Literal["HELPFUL", "NOT_HELPFUL"]
    comment: str | None
    question: str | None  # only for "not helpful": the user sent it for review
    tools: list[str]  # from the audit trail
    outcome: str | None
    trace_id: str


class FeedbackOut(BaseModel):
    helpful: int
    not_helpful: int
    rows: list[FeedbackRow]


@router.get("/feedback", response_model=FeedbackOut)
async def get_feedback(
    principal: CurrentPrincipal,
    reads: Reads,
    days: Annotated[int, Query(ge=1, le=90)] = 30,
    rating: Literal["HELPFUL", "NOT_HELPFUL"] | None = None,
) -> Any:
    """Answer ratings with their comments, newest first (FR-COP-14): a "not helpful" answer is a
    candidate for the evaluation set. Conversations stay private: only feedback rows are read."""
    if (refused := await authorize(principal, "admin.feedback.read", "admin.usage.read", reads)) is not None:
        return refused
    since, admin = _since(days), principal.identity.subject
    rows = await asyncio.to_thread(admin_repo.feedback, principal.tenant, since, rating, admin)
    counts = await asyncio.to_thread(admin_repo.feedback_counts, principal.tenant, since, admin)
    return FeedbackOut(
        helpful=counts.get("HELPFUL", 0),
        not_helpful=counts.get("NOT_HELPFUL", 0),
        rows=[FeedbackRow(**r) for r in rows],
    )


# ---------------------------------------------------------------- site settings (FR-ADM-04)


class SettingsValues(BaseModel):
    """Settings schema version 2 (migration 0022). The database CHECK (pw.settings_valid) is the
    final validator; these bounds match it, and are tighter where it has none, so a refusal is a
    clear 422 rather than a database error."""

    model_config = ConfigDict(extra="forbid")

    dead_stock_days: int = Field(ge=1, le=365)
    slow_in_prime_days: int = Field(ge=1, le=365)
    capacity_threshold_pct: float = Field(gt=0, le=100)
    stockout_horizon_hours: int = Field(ge=1, le=168)
    trailing_window_operating_days: int = Field(ge=1, le=365)
    impact_lines_cap: float = Field(gt=0, le=10_000)
    impact_hours_cap: float = Field(gt=0, le=8_760)
    impact_value_cap: float = Field(gt=0, le=1_000_000_000_000)
    impact_weight_lines: float = Field(ge=0, le=100)
    impact_weight_urgency: float = Field(ge=0, le=100)
    impact_weight_value: float = Field(ge=0, le=100)

    @model_validator(mode="after")
    def some_weight(self) -> "SettingsValues":
        if self.impact_weight_lines + self.impact_weight_urgency + self.impact_weight_value <= 0:
            raise ValueError("at least one impact weight must be more than 0")
        return self

    def stored(self) -> dict[str, int | float]:
        """As stored: whole numbers as integers, so "24" never becomes "24.0" in the history."""
        return {k: int(v) if float(v).is_integer() else v for k, v in self.model_dump().items()}


class SettingField(BaseModel):
    key: str
    group: Literal["rules", "impact"]
    label: str
    affects: str  # FR-ADM-04: each setting shows what it affects
    unit: str
    step: float


SETTING_FIELDS: tuple[SettingField, ...] = (
    SettingField(key="dead_stock_days", group="rules", label="Dead-stock period", unit="days", step=1,
                 affects="Dead stock: products with stock not picked for longer than this."),
    SettingField(key="slow_in_prime_days", group="rules", label="Slow mover period", unit="days", step=1,
                 affects="Slow movers in the fast-pick zone: products not picked for longer than this."),
    SettingField(key="capacity_threshold_pct", group="rules", label="Capacity threshold", unit="%", step=1,
                 affects="Capacity hotspots: zones at or above this utilization."),
    SettingField(key="stockout_horizon_hours", group="rules", label="Stockout horizon", unit="hours", step=1,
                 affects="Stockout risk: open order lines due within this many hours."),
    SettingField(key="trailing_window_operating_days", group="rules", label="Demand window",
                 unit="operating days", step=1,
                 affects="Average daily demand and days of cover use this many operating days."),
    SettingField(key="impact_lines_cap", group="impact", label="Order lines cap", unit="lines", step=1,
                 affects="Impact score: this many affected order lines counts as full impact."),
    SettingField(key="impact_hours_cap", group="impact", label="Urgency window", unit="hours", step=1,
                 affects="Impact score: a deadline further away than this adds no urgency."),
    SettingField(key="impact_value_cap", group="impact", label="Value cap", unit="LKR", step=1000,
                 affects="Impact score: this value at risk counts as full impact (logarithmic)."),
    SettingField(key="impact_weight_lines", group="impact", label="Weight: order lines", unit="", step=0.5,
                 affects="How much affected order lines count in the inbox ranking."),
    SettingField(key="impact_weight_urgency", group="impact", label="Weight: urgency", unit="", step=0.5,
                 affects="How much a near deadline counts in the inbox ranking."),
    SettingField(key="impact_weight_value", group="impact", label="Weight: value", unit="", step=0.5,
                 affects="How much the value at risk counts in the inbox ranking."),
)  # fmt: skip


class SettingsVersionOut(BaseModel):
    version_no: int
    saved_at: datetime
    saved_by: str
    note: str | None


class SettingsOut(BaseModel):
    version_no: int
    values: SettingsValues
    fields: list[SettingField]
    history: list[SettingsVersionOut]


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1, description="The version the editor started from")
    values: SettingsValues
    note: str | None = Field(default=None, max_length=200)


class SettingsSaved(BaseModel):
    outcome: Literal["changed", "unchanged"]
    version_no: int


def _saved_by(row: dict[str, Any]) -> str:
    if row["created_by"] == "system":
        return "System"
    return str(row["created_by_name"] or row["created_by"])


@router.get("/settings", response_model=SettingsOut)
async def get_settings(principal: CurrentPrincipal, reads: Reads) -> Any:
    """The site's settings in effect, what each affects, and the recent versions (FR-ADM-04)."""
    refused = await authorize(principal, "admin.settings.read", "admin.settings.manage", reads)
    if refused is not None:
        return refused
    versions = await asyncio.to_thread(
        admin_repo.settings_versions, principal.tenant, principal.identity.sites[0]
    )
    if not versions:
        return problem(404, "NOT_FOUND", "Not found.", current_trace_id())
    current = versions[0]
    return SettingsOut(
        version_no=current["version_no"],
        values=SettingsValues.model_validate(current["values"]),
        fields=list(SETTING_FIELDS),
        history=[
            SettingsVersionOut(version_no=v["version_no"], saved_at=v["created_at"], saved_by=_saved_by(v),
                               note=v["note"])
            for v in versions
        ],
    )  # fmt: skip


@router.put("/settings", response_model=SettingsSaved, responses={409: {"description": "VERSION_CONFLICT"}})
async def save_settings(
    body: SettingsIn,
    principal: CurrentPrincipal,
    reads: Reads,
    idempotency_key: Annotated[str, Header(pattern=IDEMPOTENCY_KEY)],
) -> Any:
    """Saves the settings as a new version (FR-ADM-04), audited. Every number that depends on them
    uses the new version from the next request, and the exception engine re-evaluates open
    exceptions on its next cycle (its evaluation key includes the settings version)."""
    refused = await authorize(principal, "admin.settings.change", "admin.settings.manage", reads)
    if refused is not None:
        return refused
    identity, trace_id, site = principal.identity, current_trace_id(), principal.identity.sites[0]
    values = body.values.stored()

    def entry(change: admin_repo.SettingsChange) -> AuditEntry:
        before = change.before or {}
        changed = {k: [before.get(k), v] for k, v in values.items() if before.get(k) != v}
        return AuditEntry(
            actor_type="USER", action="settings.change", outcome=change.outcome.upper(), trace_id=trace_id,
            site_id=site, user_subject=identity.subject, user_role=identity.role,
            scope="admin.settings.manage", decision="ALLOW",
            parameters={"expected_version": body.expected_version, "version_no": change.version_no,
                        "changed": changed, "note": body.note, "idempotency_key": idempotency_key},
        )  # fmt: skip

    change = await asyncio.to_thread(
        admin_repo.save_settings, reads.app_pool, principal.tenant, site, body.expected_version, values,
        identity.subject, body.note, entry,
    )  # fmt: skip
    if change.outcome == "not_found":
        return problem(404, "NOT_FOUND", "Not found.", trace_id)
    if change.outcome == "conflict":
        return problem(
            409, "VERSION_CONFLICT",
            f"Someone else changed the settings (now version {change.version_no}). Reload and try again.",
            trace_id,
        )  # fmt: skip
    return SettingsSaved(outcome=change.outcome, version_no=change.version_no or 0)  # pyright: ignore[reportArgumentType]


class AuditRow(BaseModel):
    occurred_at: datetime
    actor_type: str
    user_subject: str | None
    user_name: str | None
    user_role: str | None
    agent_id: str | None
    agent_version: str | None
    model: str | None
    prompt_version: str | None
    run_id: str | None
    action: str
    scope: str | None
    tool: str | None
    decision: str | None
    deny_reasons: list[str] | None
    outcome: str
    row_count: int | None
    latency_ms: int | None
    cost_estimated: float | None
    trace_id: str
    parameters: dict[str, Any] | None


def _audit_filter(
    since: datetime | None, until: datetime | None, user: str | None, agent: str | None,
    tool: str | None, action: str | None, decision: str | None,
) -> admin_repo.AuditFilter:  # fmt: skip
    return admin_repo.AuditFilter(since, until, user, agent, tool, action, decision)


@router.get("/audit", response_model=list[AuditRow])
async def get_audit(
    principal: CurrentPrincipal,
    reads: Reads,
    since: datetime | None = None,
    until: datetime | None = None,
    user: Annotated[str | None, Query(max_length=200)] = None,
    agent: Annotated[str | None, Query(max_length=100)] = None,
    tool: Annotated[str | None, Query(max_length=100)] = None,
    action: Annotated[str | None, Query(max_length=100)] = None,
    decision: Literal["ALLOW", "DENY"] | None = None,
) -> Any:
    """The audit trail, newest first, at most 500 entries, with filters (FR-ADM-05)."""
    if (refused := await authorize(principal, "admin.audit.read", "admin.audit.read", reads)) is not None:
        return refused
    f = _audit_filter(since, until, user, agent, tool, action, decision)
    rows = await asyncio.to_thread(admin_repo.audit, principal.tenant, f)
    return [AuditRow.model_validate({**r, "run_id": str(r["run_id"]) if r["run_id"] else None}) for r in rows]


@router.get("/audit.csv", response_class=Response, responses={200: {"content": {"text/csv": {}}}})
async def export_audit(
    principal: CurrentPrincipal,
    reads: Reads,
    since: datetime | None = None,
    until: datetime | None = None,
    user: Annotated[str | None, Query(max_length=200)] = None,
    agent: Annotated[str | None, Query(max_length=100)] = None,
    tool: Annotated[str | None, Query(max_length=100)] = None,
    action: Annotated[str | None, Query(max_length=100)] = None,
    decision: Literal["ALLOW", "DENY"] | None = None,
) -> Any:
    """The same filtered view as CSV (FR-ADM-05: the export matches the filtered view)."""
    if (refused := await authorize(principal, "admin.audit.export", "admin.audit.read", reads)) is not None:
        return refused
    f = _audit_filter(since, until, user, agent, tool, action, decision)
    rows = await asyncio.to_thread(admin_repo.audit, principal.tenant, f)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(admin_repo.AUDIT_COLUMNS)
    for r in rows:
        # A leading =, +, -, @, tab or CR would be read as a formula by a spreadsheet: prefix a quote.
        writer.writerow([_cell(r[c]) for c in admin_repo.AUDIT_COLUMNS])
    return Response(
        out.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="palletwise-audit.csv"'},
    )


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text
