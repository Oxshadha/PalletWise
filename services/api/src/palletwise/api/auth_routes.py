"""Sign-in, sign-out and the current user (identity spec 2). Tokens never leave the server."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from palletwise.api.security import (
    LOGIN_COOKIE,
    SESSION_COOKIE,
    CurrentPrincipal,
    auth_service,
    clear_cookie,
    set_cookie,
)
from palletwise.auth.service import AuthService, LoginFailed
from palletwise.db import status_repo
from palletwise.db.connection import role_pool

router = APIRouter(prefix="/api")
Auth = Annotated[AuthService, Depends(auth_service)]
NO_STORE = {"Cache-Control": "no-store"}


class SiteOut(BaseModel):
    id: UUID
    code: str
    name: str


class MeOut(BaseModel):
    name: str
    role: Literal["warehouse_manager", "admin"]
    sites: list[SiteOut]


@router.get("/auth/login", status_code=status.HTTP_302_FOUND, response_class=RedirectResponse)
def login(auth: Auth, return_to: Annotated[str | None, Query(max_length=512)] = None) -> Response:
    start = auth.start_login(return_to)
    response = RedirectResponse(start.redirect_url, status.HTTP_302_FOUND, headers=NO_STORE)
    set_cookie(response, LOGIN_COOKIE, start.state, max_age=auth.settings.login_ttl_minutes * 60)
    return response


@router.get("/auth/callback", status_code=status.HTTP_302_FOUND, response_class=RedirectResponse)
def callback(
    request: Request,
    auth: Auth,
    state: Annotated[str, Query(max_length=128)] = "",
    code: Annotated[str, Query(max_length=4096)] = "",
) -> Response:
    try:
        if not state or not code:  # includes an error returned by the identity provider
            raise LoginFailed("failed")
        result = auth.finish_login(state=state, bound_state=request.cookies.get(LOGIN_COOKIE), code=code)
    except LoginFailed as failure:
        response = RedirectResponse(f"/?auth_error={failure.reason}", status.HTTP_302_FOUND, headers=NO_STORE)
    else:
        response = RedirectResponse(result.return_to, status.HTTP_302_FOUND, headers=NO_STORE)
        set_cookie(response, SESSION_COOKIE, result.session_id, max_age=auth.settings.absolute_hours * 3600)
    clear_cookie(response, LOGIN_COOKIE)
    return response


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, auth: Auth) -> Response:
    auth.logout(request.cookies.get(SESSION_COOKIE))
    response = Response(status_code=status.HTTP_204_NO_CONTENT, headers=NO_STORE)
    clear_cookie(response, SESSION_COOKIE)
    return response


@router.get("/me", response_model=MeOut)
def me(principal: CurrentPrincipal) -> MeOut:
    identity = principal.identity
    with role_pool("PW_APP").connection() as conn:
        sites = status_repo.site_summaries(conn, identity.tenant, identity.sites)
    return MeOut(
        name=identity.display_name,
        role=identity.role,
        sites=[SiteOut(id=s.id, code=s.code, name=s.name) for s in sites],
    )
