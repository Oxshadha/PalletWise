"""Session cookie, the authenticated-request dependency and the CSRF check (identity spec 3, 7)."""

from collections.abc import Awaitable, Callable
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from palletwise.auth.oidc import IdentityProviderUnavailable
from palletwise.auth.principal import Principal
from palletwise.auth.service import AuthService, SessionEnded

SESSION_COOKIE = "__Host-pw_session"
LOGIN_COOKIE = "__Host-pw_login"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def auth_service(request: Request) -> AuthService:
    service: AuthService = request.app.state.auth
    return service


def set_cookie(response: Response, name: str, value: str, max_age: int) -> None:
    # __Host- prefix: Secure, Path=/ and no Domain, so the cookie stays on this exact origin.
    response.set_cookie(name, value, max_age=max_age, path="/", secure=True, httponly=True, samesite="lax")


def clear_cookie(response: Response, name: str) -> None:
    response.delete_cookie(name, path="/", secure=True, httponly=True, samesite="lax")


def require_session(request: Request, auth: Annotated[AuthService, Depends(auth_service)]) -> Principal:
    try:
        return auth.authenticate(request.cookies.get(SESSION_COOKIE))
    except SessionEnded:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not signed in") from None
    except IdentityProviderUnavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "sign-in service unavailable") from None


CurrentPrincipal = Annotated[Principal, Depends(require_session)]


def site_in_scope(site_id: UUID, principal: CurrentPrincipal) -> UUID:
    """A site in the path must be one of the session's sites; anything else is 404."""
    if not principal.may_see_site(site_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    return site_id


def csrf_middleware(
    public_origin: str,
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    """State-changing requests need our Origin; Sec-Fetch-Site, when sent, must agree."""

    async def check(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        if request.method not in SAFE_METHODS:
            origin = request.headers.get("origin")
            fetch_site = request.headers.get("sec-fetch-site")
            if origin != public_origin or (fetch_site is not None and fetch_site != "same-origin"):
                return JSONResponse({"detail": "cross-site request refused"}, status_code=403)
        return await call_next(request)

    return check
