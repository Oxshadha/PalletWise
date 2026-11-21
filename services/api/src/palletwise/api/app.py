"""FastAPI BFF. Every endpoint except health and sign-in requires a session (identity spec 8)."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Response, status
from pydantic import BaseModel

from palletwise.agent import semantic_router
from palletwise.agent.runtime import PRODUCTION_RUNTIME, CopilotRuntime
from palletwise.api import auth_routes, brief, copilot, events, exceptions, sites
from palletwise.api.observability import request_span
from palletwise.api.security import CurrentPrincipal, csrf_middleware, require_session, site_in_scope
from palletwise.auth.crypto import KeyRing
from palletwise.auth.oidc import OidcClient
from palletwise.auth.service import AuthService
from palletwise.db import status_repo
from palletwise.db.agent_repo import AgentRegistry
from palletwise.db.audit_repo import AuditLog
from palletwise.db.cache_repo import WordingCacheStore
from palletwise.db.connection import close_pools, role_pool
from palletwise.db.copilot_repo import ConversationStore, RateLimiter, UsageLedger
from palletwise.db.session_repo import SessionStore
from palletwise.llm import build_provider
from palletwise.policy import PolicyClient
from palletwise.settings import (
    LlmSettings,
    OidcSettings,
    PolicySettings,
    SessionSettings,
    TelemetrySettings,
    WebSettings,
)
from palletwise.telemetry import metrics
from palletwise.telemetry.logs import configure_api_logging
from palletwise.telemetry.setup import configure_telemetry
from palletwise.tools.catalog import v1_tools
from palletwise.tools.executor import ToolExecutor

SHUTDOWN_GRACE_SECONDS = 10.0


class Health(BaseModel):
    ready: bool
    database: bool


class SiteStatusOut(BaseModel):
    code: str
    name: str
    data_status: str
    sim_time: datetime | None
    time_zone: str


def build_auth_service() -> AuthService:
    web = WebSettings()
    sessions = SessionSettings()  # pyright: ignore[reportCallIssue]
    oidc = OidcClient(OidcSettings(), web)  # pyright: ignore[reportCallIssue]
    pool = role_pool("PW_APP")
    return AuthService(oidc, KeyRing(sessions.keys), sessions, SessionStore(pool), AuditLog(pool))


def build_copilot() -> copilot.CopilotServices:
    """Each role in its own pool: pw_app for governance, pw_agent_read for tools, pw_memory for history."""
    app_pool, read_pool = role_pool("PW_APP"), role_pool("PW_AGENT_READ")
    agents, audit, limiter = AgentRegistry(app_pool), AuditLog(app_pool), RateLimiter(app_pool)
    tools = v1_tools()
    executor = ToolExecutor(tools, agents, PolicyClient(PolicySettings()), audit, read_pool, limiter)
    llm = LlmSettings()
    semantic_router.router()  # parsed at startup: a broken routes file fails here, not on a question
    return copilot.CopilotServices(
        runtime=CopilotRuntime(
            tools,
            executor,
            build_provider(llm),
            llm.run_timeout_seconds,
            config=PRODUCTION_RUNTIME,
            cache=copilot.WordingCacheAdapter(WordingCacheStore(role_pool("PW_CACHE"))),
        ),
        agents=agents,
        conversations=ConversationStore(role_pool("PW_MEMORY")),
        ledger=UsageLedger(app_pool),
        limiter=limiter,
        audit=audit,
        app_pool=app_pool,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    configure_api_logging()
    telemetry = TelemetrySettings()
    configure_telemetry("palletwise-api", telemetry)
    metrics.serve_metrics(telemetry.metrics_port)
    if getattr(app.state, "auth", None) is None:
        app.state.auth = build_auth_service()
    if getattr(app.state, "copilot", None) is None:
        app.state.copilot = build_copilot()
    if getattr(app.state, "reads", None) is None:
        app_pool = role_pool("PW_APP")
        app.state.reads = sites.ReadServices(
            PolicyClient(PolicySettings()), AuditLog(app_pool), app_pool, role_pool("PW_AGENT_READ")
        )
    yield
    await app.state.site_changes.stop()
    await app.state.copilot.drain(timeout=SHUTDOWN_GRACE_SECONDS)  # records written before pools close
    close_pools()


public = APIRouter(prefix="/api")
protected = APIRouter(prefix="/api", dependencies=[Depends(require_session)])


@public.get("/health", response_model=Health)
def health(response: Response) -> Health:
    try:
        with role_pool("PW_APP").connection(timeout=2) as conn:
            conn.execute("SELECT 1")
        return Health(ready=True, database=True)
    except Exception:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return Health(ready=False, database=False)


@protected.get(
    "/sites/{site_id}/status",
    response_model=SiteStatusOut,
    responses={404: {"description": "Not one of your sites, or not created yet"}},
)
def site_status(
    site_id: Annotated[UUID, Depends(site_in_scope)], principal: CurrentPrincipal
) -> SiteStatusOut:
    with role_pool("PW_APP").connection() as conn:
        site = status_repo.site_status(conn, principal.tenant, site_id)
    if site is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    return SiteStatusOut(
        code=site.code,
        name=site.name,
        data_status=site.data_status,
        sim_time=site.sim_time,
        time_zone=site.time_zone,
    )


def create_app(
    auth: AuthService | None = None,
    web: WebSettings | None = None,
    copilot_services: copilot.CopilotServices | None = None,
    reads: sites.ReadServices | None = None,
    site_changes: events.SiteChangeHub | None = None,
) -> FastAPI:
    app = FastAPI(
        title="PalletWise API",
        version="0.1.0",
        lifespan=lifespan,
        # FastAPI's built-in telemetry is off: our request middleware emits the one http.server
        # span with allowlisted attributes, and nothing else records exception messages.
        telemetry={"tracing": False, "metrics": False, "logs": False, "operation_spans": False},
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.auth = auth
    app.state.copilot = copilot_services
    app.state.reads = reads
    app.state.site_changes = site_changes or events.SiteChangeHub()  # listens from the first stream
    app.middleware("http")(csrf_middleware((web or WebSettings()).public_origin))
    app.middleware("http")(request_span)  # added last, so outermost: it sees CSRF refusals too
    app.include_router(public)
    app.include_router(auth_routes.router)
    app.include_router(protected)
    app.include_router(copilot.router)
    app.include_router(sites.router)
    app.include_router(events.router)
    app.include_router(exceptions.router)
    app.include_router(brief.router)
    return app


app = create_app()
