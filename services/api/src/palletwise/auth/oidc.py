"""OIDC client for Keycloak and ID token validation (identity spec 2).

The browser only ever sees the authorization endpoint. Code exchange, refresh and logout
run server to server, so tokens never reach the browser.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from palletwise.settings import OidcSettings, WebSettings

log = logging.getLogger(__name__)

ALGORITHMS = ["RS256"]
COPILOT_AUDIENCE = "palletwise-copilot"
# RFC 8693 identifiers, not credentials.
TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"  # noqa: S105
ACCESS_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:access_token"  # noqa: S105
LEEWAY_SECONDS = 30
CALLBACK_PATH = "/api/auth/callback"

SigningKeyResolver = Callable[[str], Any]


class TokenInvalid(Exception):
    """A token failed validation. The message names the check, never the token."""


class TokenRejected(Exception):
    """Keycloak refused a code or refresh token (for example reused, revoked or expired)."""


class IdentityProviderUnavailable(Exception):
    """Keycloak could not be reached or answered with a server error."""


@dataclass(frozen=True, slots=True)
class TokenSet:
    id_token: str
    access_token: str
    refresh_token: str


def jwks_resolver(settings: OidcSettings) -> SigningKeyResolver:
    """Keycloak's published signing keys, cached and re-fetched on an unknown key id."""
    client = jwt.PyJWKClient(
        f"{settings.internal_url}/protocol/openid-connect/certs",
        cache_keys=True,
        lifespan=300,
        timeout=int(settings.http_timeout_seconds),
    )

    def resolve(token: str) -> Any:
        try:
            return client.get_signing_key_from_jwt(token).key
        except jwt.PyJWKClientConnectionError as exc:
            raise IdentityProviderUnavailable("signing keys unavailable") from exc
        except jwt.PyJWKClientError as exc:
            raise TokenInvalid("unknown signing key") from exc

    return resolve


def validate_id_token(
    token: str,
    *,
    signing_key: SigningKeyResolver,
    issuer: str,
    client_id: str,
    nonce: str | None,
) -> dict[str, Any]:
    """Validates signature, alg, iss, aud, azp, exp, iat and, at sign-in, nonce.

    On refresh, `nonce` is None: OIDC Core 12.2 lets a refreshed ID token omit it, and the
    caller checks that the subject is unchanged instead.
    """
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            signing_key(token),
            algorithms=ALGORITHMS,
            audience=client_id,
            issuer=issuer,
            leeway=LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.InvalidTokenError as exc:
        raise TokenInvalid(type(exc).__name__) from exc
    if "azp" in claims and claims["azp"] != client_id:
        raise TokenInvalid("azp")
    if nonce is not None and claims.get("nonce") != nonce:
        raise TokenInvalid("nonce")
    return claims


class OidcClient:
    def __init__(
        self,
        settings: OidcSettings,
        web: WebSettings,
        *,
        signing_key: SigningKeyResolver | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self.redirect_uri = web.public_origin + CALLBACK_PATH
        self._signing_key = signing_key or jwks_resolver(settings)
        self._http = httpx.Client(
            base_url=settings.internal_url,
            timeout=settings.http_timeout_seconds,
            auth=(settings.client_id, settings.client_secret),
            transport=transport,
        )

    @property
    def issuer(self) -> str:
        return self._settings.public_url

    def authorize_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self._settings.client_id,
                "redirect_uri": self.redirect_uri,
                "scope": "openid",
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{self._settings.public_url}/protocol/openid-connect/auth?{query}"

    def exchange_code(self, code: str, code_verifier: str) -> TokenSet:
        return self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "code_verifier": code_verifier,
            }
        )

    def refresh(self, refresh_token: str) -> TokenSet:
        return self._token_request({"grant_type": "refresh_token", "refresh_token": refresh_token})

    def logout(self, refresh_token: str) -> bool:
        """Ends the Keycloak session, which also invalidates the refresh token."""
        try:
            response = self._http.post(
                "/protocol/openid-connect/logout", data={"refresh_token": refresh_token}
            )
        except httpx.HTTPError:
            log.warning("identity provider logout failed: not reachable")
            return False
        if response.status_code >= 300:
            log.warning("identity provider logout failed: HTTP %s", response.status_code)
            return False
        return True

    def validate(self, id_token: str, *, nonce: str | None) -> dict[str, Any]:
        return validate_id_token(
            id_token,
            signing_key=self._signing_key,
            issuer=self.issuer,
            client_id=self._settings.client_id,
            nonce=nonce,
        )

    def exchange_for_copilot(self, subject_token: str) -> str:
        """RFC 8693 token exchange for a token with the copilot audience (identity spec 5).

        The `copilot-audience` scope is optional on the client, so only this call gets it.
        """
        try:
            response = self._http.post(
                "/protocol/openid-connect/token",
                data={
                    "grant_type": TOKEN_EXCHANGE,
                    "subject_token": subject_token,
                    "subject_token_type": ACCESS_TOKEN_TYPE,
                    "requested_token_type": ACCESS_TOKEN_TYPE,
                    "audience": COPILOT_AUDIENCE,
                    # openid stays in the requested scope: Keycloak keeps the exchange's scope on
                    # the client session, and without openid the next refresh has no ID token.
                    "scope": "openid copilot-audience",
                },
            )
        except httpx.HTTPError as exc:
            raise IdentityProviderUnavailable("token endpoint not reachable") from exc
        if response.status_code >= 500:
            raise IdentityProviderUnavailable(f"token endpoint HTTP {response.status_code}")
        if response.status_code != 200:
            error = response.json().get("error", "unknown") if response.content else "unknown"
            raise TokenRejected(str(error))
        token = response.json().get("access_token")
        if not isinstance(token, str):
            raise TokenRejected("missing access_token")
        return token

    def validate_copilot_token(self, token: str, *, subject: str) -> dict[str, Any]:
        """Issuer, signature, audience palletwise-copilot, azp this client, and the same user."""
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                self._signing_key(token),
                algorithms=ALGORITHMS,
                audience=COPILOT_AUDIENCE,
                issuer=self.issuer,
                leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "iss", "aud", "sub", "azp"]},
            )
        except jwt.InvalidTokenError as exc:
            raise TokenInvalid(type(exc).__name__) from exc
        if claims["azp"] != self._settings.client_id:
            raise TokenInvalid("azp")
        if claims["sub"] != subject:
            raise TokenInvalid("sub")
        return claims

    def close(self) -> None:
        self._http.close()

    def _token_request(self, form: dict[str, str]) -> TokenSet:
        try:
            response = self._http.post("/protocol/openid-connect/token", data=form)
        except httpx.HTTPError as exc:
            raise IdentityProviderUnavailable("token endpoint not reachable") from exc
        if response.status_code >= 500:
            raise IdentityProviderUnavailable(f"token endpoint HTTP {response.status_code}")
        if response.status_code != 200:
            # The body names the OAuth error (e.g. invalid_grant); it never contains a token.
            error = response.json().get("error", "unknown") if response.content else "unknown"
            raise TokenRejected(str(error))
        body = response.json()
        try:
            return TokenSet(body["id_token"], body["access_token"], body["refresh_token"])
        except KeyError as exc:
            raise TokenRejected(f"missing {exc.args[0]}") from exc
