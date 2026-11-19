"""A fake OIDC provider for API tests, behaving like Keycloak where it matters.

Single-use codes bound to the PKCE challenge and redirect URI, rotating refresh tokens, RS256
ID tokens, and RFC 8693 token exchange for the copilot audience.
"""

import base64
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from palletwise.auth.crypto import pkce_challenge

ISSUER = "http://idp.test/realms/palletwise"
ORIGIN = "https://testserver"
CLIENT_ID = "palletwise-bff"
CLIENT_SECRET = "test-client-secret"
SIGNING_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


# ---------------------------------------------------------------- fake identity provider


@dataclass
class FakeIdp:
    users: dict[str, dict[str, Any]] = field(default_factory=dict)
    codes: dict[str, dict[str, Any]] = field(default_factory=dict)
    refresh_tokens: dict[str, str] = field(default_factory=dict)  # token -> subject
    access_tokens: dict[str, str] = field(default_factory=dict)  # token -> subject
    exchanges: int = 0
    refuse_exchange: bool = False
    issued: list[str] = field(default_factory=list)  # every token handed out
    logouts: list[str] = field(default_factory=list)
    refresh_calls: int = 0
    refresh_delay: float = 0.0
    id_token_overrides: dict[str, Any] = field(default_factory=dict)
    down: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def authorize(self, url: str, subject: str) -> str:
        """What Keycloak does when the user signs in: returns an authorization code."""
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        assert url.startswith(f"{ISSUER}/protocol/openid-connect/auth?")
        assert q["response_type"] == "code" and q["client_id"] == CLIENT_ID
        assert q["code_challenge_method"] == "S256" and q["scope"] == "openid"
        code = base64.urlsafe_b64encode(os.urandom(24)).decode()
        self.codes[code] = {
            "sub": subject,
            "nonce": q["nonce"],
            "challenge": q["code_challenge"],
            "redirect_uri": q["redirect_uri"],
        }
        return code

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("identity provider down")
        expected = "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
        if request.headers.get("authorization") != expected:
            return httpx.Response(401, json={"error": "unauthorized_client"})
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if request.url.path.endswith("/logout"):
            self.logouts.append(form["refresh_token"])
            self.refresh_tokens.pop(form["refresh_token"], None)
            return httpx.Response(204)
        if form["grant_type"] == "authorization_code":
            grant = self.codes.pop(form["code"], None)  # single use
            if (
                grant is None
                or grant["redirect_uri"] != form.get("redirect_uri")
                or pkce_challenge(form.get("code_verifier", "")) != grant["challenge"]
            ):
                return httpx.Response(400, json={"error": "invalid_grant"})
            return self._tokens(grant["sub"], nonce=grant["nonce"])
        if form["grant_type"] == "urn:ietf:params:oauth:grant-type:token-exchange":
            return self._exchange(form)
        if form["grant_type"] == "refresh_token":
            with self.lock:
                self.refresh_calls += 1
                subject = self.refresh_tokens.pop(form["refresh_token"], None)  # rotation
            time.sleep(self.refresh_delay)
            if subject is None or self.users[subject].get("disabled"):
                return httpx.Response(400, json={"error": "invalid_grant"})
            return self._tokens(subject, nonce=None)
        return httpx.Response(400, json={"error": "unsupported_grant_type"})

    def _tokens(self, subject: str, nonce: str | None) -> httpx.Response:
        now = int(time.time())
        user = {k: v for k, v in self.users[subject].items() if k != "disabled"}
        claims = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "azp": CLIENT_ID,
            "sub": subject,
            "iat": now,
            "exp": now + 300,
            **user,
            **self.id_token_overrides,
        }
        if nonce is not None:
            claims.setdefault("nonce", nonce)
        id_token = jwt.encode(
            {k: v for k, v in claims.items() if v is not None}, SIGNING_KEY, algorithm="RS256"
        )
        access = base64.urlsafe_b64encode(os.urandom(32)).decode()
        refresh = base64.urlsafe_b64encode(os.urandom(32)).decode()
        self.refresh_tokens[refresh] = subject
        self.access_tokens[access] = subject
        self.issued += [id_token, access, refresh]
        return httpx.Response(
            200, json={"id_token": id_token, "access_token": access, "refresh_token": refresh}
        )

    def _exchange(self, form: dict[str, str]) -> httpx.Response:
        """Token exchange: the subject token must be a live access token of this client."""
        self.exchanges += 1
        subject = self.access_tokens.get(form.get("subject_token", ""))
        if self.refuse_exchange or subject is None or form.get("audience") != "palletwise-copilot":
            return httpx.Response(400, json={"error": "access_denied"})
        now = int(time.time())
        token = jwt.encode(
            {
                "iss": ISSUER,
                "aud": ["palletwise-copilot"],
                "azp": CLIENT_ID,
                "sub": subject,
                "iat": now,
                "exp": now + 300,
            },
            SIGNING_KEY,
            algorithm="RS256",
        )
        self.issued.append(token)
        return httpx.Response(200, json={"access_token": token, "token_type": "Bearer"})
