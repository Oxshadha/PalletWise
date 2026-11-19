"""FastAPI BFF. Every endpoint except health and sign-in requires a session (identity spec 8)."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Response, status
from pydantic import BaseModel

from palletwise.api import auth_routes
from palletwise.api.security import CurrentPrincipal, csrf_middleware, require_session, site_in_scope
from palletwise.auth.crypto import KeyRing
from palletwise.auth.oidc import OidcClient
from palletwise.auth.service import AuthService
from palletwise.db import status_repo
from palletwise.db.audit_repo import AuditLog
from palletwise.db.connection import close_pools, role_pool
from palletwise.db.session_repo import SessionStore
from palletwise.settings import OidcSettings, SessionSettings, WebSettings


class Health(BaseModel):
    ready: bool
    database: bool


class SiteStatusOut(BaseModel):
    code: str
    name: str
    data_status: str
    sim_time: datetime | None


def build_auth_service() -> AuthService:
    web = WebSettings()
    sessions = SessionSettings()  # pyright: ignore[reportCallIssue]
    oidc = OidcClient(OidcSettings(), web)  # pyright: ignore[reportCallIssue]
    pool = role_pool("PW_APP")
    return AuthService(oidc, KeyRing(sessions.keys), sessions, SessionStore(pool), AuditLog(pool))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    if getattr(app.state, "auth", None) is None:
        app.state.auth = build_auth_service()
    yield
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
    return SiteStatusOut(code=site.code, name=site.name, data_status=site.data_status, sim_time=site.sim_time)


def create_app(auth: AuthService | None = None, web: WebSettings | None = None) -> FastAPI:
    app = FastAPI(
        title="PalletWise API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.auth = auth
    app.middleware("http")(csrf_middleware((web or WebSettings()).public_origin))
    app.include_router(public)
    app.include_router(auth_routes.router)
    app.include_router(protected)
    return app


app = create_app()
