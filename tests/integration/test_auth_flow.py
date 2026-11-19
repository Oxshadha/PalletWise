"""Identity spec 8 through the real API and PostgreSQL, with a fake identity provider.

The fake provider behaves like Keycloak where it matters: single-use codes bound to the PKCE
challenge and redirect URI, rotating refresh tokens, and RS256 ID tokens. Keycloak itself is
covered by the end-to-end test in test_keycloak.py.
"""

import base64
import os
import threading
from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from support import provision_site

from evals.fake_idp import CLIENT_ID, CLIENT_SECRET, ISSUER, SIGNING_KEY, FakeIdp
from palletwise.api.app import create_app
from palletwise.api.security import LOGIN_COOKIE, SESSION_COOKIE
from palletwise.auth.crypto import KeyRing, token_hash
from palletwise.auth.oidc import OidcClient
from palletwise.auth.service import AuthService
from palletwise.db.audit_repo import AuditLog
from palletwise.db.connection import role_pool
from palletwise.db.session_repo import SessionStore
from palletwise.settings import OidcSettings, SessionSettings, WebSettings

pytestmark = pytest.mark.integration

Connect = Callable[[str], psycopg.Connection]
ORIGIN = "https://testserver"


# ---------------------------------------------------------------- fixtures


@dataclass
class Env:
    idp: FakeIdp
    client: TestClient
    tenant: UUID
    site: UUID
    db: psycopg.Connection  # superuser, to move time and inspect rows

    def user(self, subject: str = "user-1", **claims: Any) -> str:
        self.idp.users[subject] = {
            "pw_roles": ["warehouse_manager"], "pw_tenant": str(self.tenant),
            "pw_sites": [str(self.site)], "name": "Demo Manager", **claims,
        }  # fmt: skip
        return subject

    def sign_in(self, subject: str, return_to: str = "/") -> httpx.Response:
        start = self.client.get("/api/auth/login", params={"return_to": return_to}, follow_redirects=False)
        assert start.status_code == 302
        code = self.idp.authorize(start.headers["location"], subject)
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
        return self.client.get(
            f"/api/auth/callback?{urlencode({'state': state, 'code': code})}", follow_redirects=False
        )

    def session_count(self) -> int:
        row = self.db.execute("SELECT count(*) FROM pw.web_session").fetchone()
        return int(row[0]) if row else 0

    def age_session(self, **delta: str) -> None:
        """Move a session's timestamps into the past, e.g. refreshed_at='6 minutes'."""
        for column, interval in delta.items():
            self.db.execute(f"UPDATE pw.web_session SET {column} = {column} - %s::interval", (interval,))


@pytest.fixture
def env(database: dict[str, Any], connect_as: Connect) -> Generator[Env]:
    tenant, site = provision_site(connect_as)
    idp = FakeIdp()
    oidc = OidcClient(
        OidcSettings(public_url=ISSUER, internal_url="http://idp.internal/realms/palletwise",
                     client_id=CLIENT_ID, client_secret=CLIENT_SECRET),
        WebSettings(public_origin=ORIGIN),
        signing_key=lambda _: SIGNING_KEY.public_key(),
        transport=httpx.MockTransport(idp.handle),
    )  # fmt: skip
    sessions = SessionSettings(keys="k1:" + base64.b64encode(os.urandom(32)).decode())
    auth = AuthService(
        oidc,
        KeyRing(sessions.keys),
        sessions,
        SessionStore(role_pool("PW_APP")),
        AuditLog(role_pool("PW_APP")),
    )
    client = TestClient(create_app(auth, WebSettings(public_origin=ORIGIN)), base_url=ORIGIN)
    db = connect_as("postgres")
    db.autocommit = True
    db.execute("DELETE FROM pw.web_session")
    yield Env(idp, client, tenant, site, db)


def no_session(env: Env) -> None:
    env.client.cookies.clear()


# ---------------------------------------------------------------- sign-in


def test_sign_in_creates_a_session_and_tokens_never_reach_the_browser(env: Env) -> None:
    response = env.sign_in(env.user(), return_to="/inventory?sku=SKU-102")
    assert response.status_code == 302 and response.headers["location"] == "/inventory?sku=SKU-102"
    set_cookie = response.headers.get_list("set-cookie")
    session_cookie = next(c for c in set_cookie if c.startswith(f"{SESSION_COOKIE}="))
    for attribute in ("HttpOnly", "Secure", "SameSite=lax", "Path=/"):
        assert attribute.lower() in session_cookie.lower()
    assert "domain" not in session_cookie.lower()

    me = env.client.get("/api/me")
    assert me.status_code == 200
    assert me.json() == {
        "name": "Demo Manager", "role": "warehouse_manager",
        "sites": [{"id": str(env.site), "code": "PLG", "name": "Test site"}],
    }  # fmt: skip

    seen = response.text + str(response.headers) + me.text + str(me.headers)
    assert env.idp.issued and not any(token in seen for token in env.idp.issued)


def test_session_id_and_refresh_token_are_not_stored_in_plaintext(env: Env) -> None:
    env.sign_in(env.user())
    session_id = env.client.cookies[SESSION_COOKIE]
    row = env.db.execute("SELECT id_hash, refresh_token, key_id FROM pw.web_session").fetchone()
    assert row is not None and bytes(row[0]) == token_hash(session_id) and row[2] == "k1"
    refresh = next(t for t in env.idp.refresh_tokens)
    assert refresh.encode() not in bytes(row[1]) and session_id.encode() not in bytes(row[1])


def test_pw_app_cannot_read_session_tables_directly(connect_as: Connect) -> None:
    conn = connect_as("pw_app")
    for table in ("pw.web_session", "pw.web_login"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(f"SELECT 1 FROM {table}")
        conn.rollback()


def test_session_rows_are_invisible_even_with_an_accidental_grant(env: Env, connect_as: Connect) -> None:
    env.sign_in(env.user())
    env.db.execute("GRANT SELECT ON pw.web_session TO pw_app")
    try:
        row = connect_as("pw_app").execute("SELECT count(*) FROM pw.web_session").fetchone()
        assert row == (0,)
    finally:
        env.db.execute("REVOKE SELECT ON pw.web_session FROM pw_app")


def test_every_endpoint_needs_a_session(env: Env) -> None:
    """Spec 8: 401 everywhere except health and sign-in. Sign-out is idempotent and also open."""
    open_paths = {"/api/health", "/api/auth/login", "/api/auth/callback", "/api/auth/logout"}
    paths: dict[str, Any] = env.client.app.openapi()["paths"]  # type: ignore[attr-defined]
    checked = 0
    for path, operations in paths.items():
        if path in open_paths:
            continue
        for method in operations:
            url = path.replace("{site_id}", str(env.site))
            response = env.client.request(method.upper(), url, headers={"Origin": ORIGIN})
            assert response.status_code == 401, (method, path)
            checked += 1
    assert checked >= 2


@pytest.mark.parametrize("cookie", [None, "a-different-state"], ids=["no login cookie", "other browser"])
def test_callback_needs_the_state_bound_to_this_browser(env: Env, cookie: str | None) -> None:
    start = env.client.get("/api/auth/login", follow_redirects=False)
    code = env.idp.authorize(start.headers["location"], env.user())
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    env.client.cookies.clear()
    if cookie:
        env.client.cookies.set(LOGIN_COOKIE, cookie)
    response = env.client.get(f"/api/auth/callback?state={state}&code={code}", follow_redirects=False)
    assert response.headers["location"] == "/?auth_error=state" and env.session_count() == 0


def test_reused_state_is_rejected(env: Env) -> None:
    start = env.client.get("/api/auth/login", follow_redirects=False)
    code = env.idp.authorize(start.headers["location"], env.user())
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    login_cookie = env.client.cookies[LOGIN_COOKIE]
    first = env.client.get(f"/api/auth/callback?state={state}&code={code}", follow_redirects=False)
    assert first.headers["location"] == "/"
    env.client.cookies.set(LOGIN_COOKIE, login_cookie)
    again = env.client.get(f"/api/auth/callback?state={state}&code={code}", follow_redirects=False)
    assert again.headers["location"] == "/?auth_error=state"


def test_expired_login_attempt_is_rejected(env: Env) -> None:
    start = env.client.get("/api/auth/login", follow_redirects=False)
    code = env.idp.authorize(start.headers["location"], env.user())
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    env.db.execute("UPDATE pw.web_login SET expires_at = now() - interval '1 second'")
    response = env.client.get(f"/api/auth/callback?state={state}&code={code}", follow_redirects=False)
    assert response.headers["location"] == "/?auth_error=state"


def test_replayed_authorization_code_is_rejected(env: Env) -> None:
    first = env.client.get("/api/auth/login", follow_redirects=False)
    code = env.idp.authorize(first.headers["location"], env.user())
    state = parse_qs(urlparse(first.headers["location"]).query)["state"][0]
    assert (
        env.client.get(f"/api/auth/callback?state={state}&code={code}", follow_redirects=False).headers[
            "location"
        ]
        == "/"
    )
    second = env.client.get("/api/auth/login", follow_redirects=False)
    state2 = parse_qs(urlparse(second.headers["location"]).query)["state"][0]
    replay = env.client.get(f"/api/auth/callback?state={state2}&code={code}", follow_redirects=False)
    assert replay.headers["location"] == "/?auth_error=failed"


def test_provider_error_on_callback_is_a_failed_sign_in(env: Env) -> None:
    response = env.client.get("/api/auth/callback?error=access_denied&state=x", follow_redirects=False)
    assert response.headers["location"] == "/?auth_error=failed"


@pytest.mark.parametrize(
    "override",
    [
        {"nonce": "not-the-attempts-nonce"},
        {"aud": "palletwise-copilot"},
        {"iss": "http://evil.test/realms/x"},
    ],
    ids=["nonce", "audience", "issuer"],
)
def test_invalid_id_token_rejects_sign_in_and_ends_the_provider_session(
    env: Env, override: dict[str, Any]
) -> None:
    env.idp.id_token_overrides = override
    response = env.sign_in(env.user())
    assert response.headers["location"] == "/?auth_error=failed"
    assert env.session_count() == 0 and len(env.idp.logouts) == 1


@pytest.mark.parametrize("roles", [[], ["warehouse_manager", "admin"]], ids=["none", "both"])
def test_no_or_both_roles_are_refused_with_a_clear_reason(env: Env, roles: list[str]) -> None:
    response = env.sign_in(env.user(pw_roles=roles))
    assert response.headers["location"] == "/?auth_error=role" and env.session_count() == 0


def test_unprovisioned_tenant_is_refused(env: Env) -> None:
    response = env.sign_in(env.user(pw_tenant=str(uuid4())))
    assert response.headers["location"] == "/?auth_error=tenant" and env.session_count() == 0


def test_open_redirect_is_not_possible(env: Env) -> None:
    response = env.sign_in(env.user(), return_to="//evil.example/")
    assert response.headers["location"] == "/"


# ---------------------------------------------------------------- tenant binding


def test_another_tenants_site_is_not_found(env: Env, connect_as: Connect) -> None:
    _, other_site = provision_site(connect_as)
    env.sign_in(env.user())
    assert env.client.get(f"/api/sites/{env.site}/status").status_code == 200
    assert env.client.get(f"/api/sites/{other_site}/status").status_code == 404
    assert env.client.get(f"/api/sites/{uuid4()}/status").status_code == 404


# ---------------------------------------------------------------- revalidation (every 5 minutes)


def test_role_revoked_during_a_session_ends_it_at_the_next_refresh(env: Env) -> None:
    subject = env.user()
    env.sign_in(subject)
    env.idp.users[subject]["pw_roles"] = []
    assert env.client.get("/api/me").status_code == 200  # not yet due
    env.age_session(refreshed_at="6 minutes")
    assert env.client.get("/api/me").status_code == 401
    assert env.session_count() == 0


def test_site_revoked_returns_404_after_the_next_refresh(env: Env) -> None:
    subject = env.user()
    env.sign_in(subject)
    env.idp.users[subject]["pw_sites"] = []
    env.age_session(refreshed_at="6 minutes")
    assert env.client.get(f"/api/sites/{env.site}/status").status_code == 404
    assert env.client.get("/api/me").json()["sites"] == []


def test_tenant_changed_ends_the_session(env: Env, connect_as: Connect) -> None:
    other_tenant, _ = provision_site(connect_as)
    subject = env.user()
    env.sign_in(subject)
    env.idp.users[subject]["pw_tenant"] = str(other_tenant)
    env.age_session(refreshed_at="6 minutes")
    assert env.client.get("/api/me").status_code == 401
    assert env.session_count() == 0


def test_disabled_account_ends_the_session(env: Env) -> None:
    subject = env.user()
    env.sign_in(subject)
    env.idp.users[subject]["disabled"] = True
    env.age_session(refreshed_at="6 minutes")
    assert env.client.get("/api/me").status_code == 401


def test_unreachable_provider_denies_but_keeps_the_session(env: Env) -> None:
    env.sign_in(env.user())
    env.age_session(refreshed_at="6 minutes")
    env.idp.down = True
    assert env.client.get("/api/me").status_code == 503
    env.idp.down = False
    assert env.client.get("/api/me").status_code == 200


def test_concurrent_requests_refresh_once(env: Env) -> None:
    env.sign_in(env.user())
    env.age_session(refreshed_at="6 minutes")
    env.idp.refresh_delay = 0.5
    results: list[int] = []
    threads = [
        threading.Thread(target=lambda: results.append(env.client.get("/api/me").status_code))
        for _ in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [200, 200, 200, 200]
    assert env.idp.refresh_calls == 1


def test_fresh_access_token_refreshes_serially_with_rotation(env: Env) -> None:
    env.sign_in(env.user())
    auth: AuthService = env.client.app.state.auth  # type: ignore[attr-defined]
    principal = auth.authenticate(env.client.cookies[SESSION_COOKIE])
    tokens: list[str] = []
    threads = [
        threading.Thread(target=lambda: tokens.append(auth.fresh_access_token(principal))) for _ in range(3)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(tokens)) == 3 and env.idp.refresh_calls == 3  # no rotation failures


# ---------------------------------------------------------------- expiry


@pytest.mark.parametrize(
    "aging", [{"last_seen_at": "31 minutes"}, {"expires_at": "12 hours"}], ids=["idle", "absolute"]
)
def test_expired_session_is_refused_on_the_request(env: Env, aging: dict[str, str]) -> None:
    env.sign_in(env.user())
    env.age_session(**aging)
    assert env.client.get("/api/me").status_code == 401
    assert env.session_count() == 0


# ---------------------------------------------------------------- CSRF and sign-out


@pytest.mark.parametrize(
    "headers",
    [{}, {"Origin": "https://evil.example"}, {"Origin": ORIGIN, "Sec-Fetch-Site": "cross-site"}],
    ids=["no origin", "foreign origin", "cross-site fetch"],
)
def test_state_changing_request_needs_our_origin(env: Env, headers: dict[str, str]) -> None:
    env.sign_in(env.user())
    assert env.client.post("/api/auth/logout", headers=headers).status_code == 403
    assert env.session_count() == 1


def test_sign_in_and_sign_out_are_audited(env: Env) -> None:
    subject = env.user()
    env.sign_in(subject)
    env.client.post("/api/auth/logout", headers={"Origin": ORIGIN})
    rows = env.db.execute(  # superuser: RLS does not apply, so filter by tenant
        "SELECT action, outcome, actor_type, user_subject, user_role FROM pw.audit_event"
        " WHERE tenant_id = %s ORDER BY seq",
        (env.tenant,),
    ).fetchall()
    assert rows == [
        ("auth.sign_in", "OK", "USER", subject, "warehouse_manager"),
        ("auth.sign_out", "OK", "USER", subject, "warehouse_manager"),
    ]


def test_sign_out_ends_both_sessions(env: Env) -> None:
    env.sign_in(env.user())
    refresh = next(iter(env.idp.refresh_tokens))
    response = env.client.post(
        "/api/auth/logout", headers={"Origin": ORIGIN, "Sec-Fetch-Site": "same-origin"}
    )
    assert response.status_code == 204
    assert any(c.startswith(f'{SESSION_COOKIE}=""') for c in response.headers.get_list("set-cookie"))
    assert env.session_count() == 0 and env.idp.logouts == [refresh]
    assert env.client.get("/api/me").status_code == 401
