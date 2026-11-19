"""The real Keycloak realm file against the real sign-in code (identity spec 2, 4, 5, 8).

Starts Keycloak with infra/keycloak/palletwise-realm.json, signs in through the login form and
checks what the fake provider in test_auth_flow.py cannot: the realm's mappers, user profile,
PKCE enforcement, code replay, revocation and the copilot audience.
"""

import base64
import html
import json
import os
import re
import time
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient
from support import provision_site

from palletwise.api.app import create_app
from palletwise.api.security import SESSION_COOKIE
from palletwise.auth.crypto import KeyRing
from palletwise.auth.oidc import OidcClient
from palletwise.auth.service import AuthService
from palletwise.db.connection import role_pool
from palletwise.db.session_repo import SessionStore
from palletwise.settings import DEMO_SITE_ID, DEMO_TENANT_ID, OidcSettings, SessionSettings, WebSettings

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
ORIGIN = "https://testserver"
SECRETS = {
    "KC_DEV_MANAGER_PASSWORD": "test-manager-pw",
    "KC_DEV_ADMIN_PASSWORD": "test-admin-pw",
    "KC_BFF_CLIENT_SECRET": "test-bff-secret",
    "KC_COPILOT_CLIENT_SECRET": "test-copilot-secret",
}
ADMIN = ("kcadmin", "kcadmin-pw")


def keycloak_image() -> str:
    for line in (ROOT / "infra/images.env").read_text().splitlines():
        if line.startswith("KEYCLOAK_IMAGE="):
            return line.split("=", 1)[1]
    raise AssertionError("KEYCLOAK_IMAGE missing from infra/images.env")


@pytest.fixture(scope="module")
def keycloak(tmp_path_factory: pytest.TempPathFactory) -> Generator[str]:
    from testcontainers.core.container import DockerContainer

    realm = json.loads((ROOT / "infra/keycloak/palletwise-realm.json").read_text())
    # Through Docker port forwarding the test runner is not a private address to Keycloak.
    realm["sslRequired"] = "none"
    for client in realm["clients"]:
        if client["clientId"] == "palletwise-bff":
            client["redirectUris"].append(f"{ORIGIN}/api/auth/callback")
    folder = tmp_path_factory.mktemp("realm")
    (folder / "palletwise-realm.json").write_text(json.dumps(realm))
    folder.chmod(0o755)

    container = (
        DockerContainer(keycloak_image())
        .with_command("start-dev --import-realm --http-port=8080")
        .with_env("KC_BOOTSTRAP_ADMIN_USERNAME", ADMIN[0])
        .with_env("KC_BOOTSTRAP_ADMIN_PASSWORD", ADMIN[1])
        .with_exposed_ports(8080)
        .with_volume_mapping(str(folder), "/opt/keycloak/data/import", "ro")
    )
    for key, value in SECRETS.items():
        container = container.with_env(key, value)
    with container:
        base = f"http://{container.get_container_host_ip()}:{container.get_exposed_port(8080)}"
        deadline = time.monotonic() + 180
        while True:
            try:
                if httpx.get(f"{base}/realms/palletwise").status_code in (200, 403):
                    break
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                out, err = container.get_logs()
                raise AssertionError("Keycloak did not start:\n" + (out + err).decode()[-3000:])
            time.sleep(1)
        # The admin API is in the master realm; relax it from inside the container (localhost).
        kcadm = "/opt/keycloak/bin/kcadm.sh"
        login = (
            f"{kcadm} config credentials --server http://localhost:8080 --realm master"
            f" --user {ADMIN[0]} --password {ADMIN[1]}"
        )
        result = container.exec(
            ["bash", "-c", f"{login} && {kcadm} update realms/master -s sslRequired=NONE"]
        )
        assert result.exit_code == 0, result.output.decode()[-500:]
        yield base


class Browser:
    """Keeps cookies by hand: Keycloak and the API set Secure cookies over plain http here."""

    def __init__(self) -> None:
        self.cookies: dict[str, str] = {}

    def request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        header = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        response = httpx.request(method, url, headers={"Cookie": header}, **kw)
        for cookie in response.headers.get_list("set-cookie"):
            name, value = cookie.split(";")[0].split("=", 1)
            self.cookies[name] = value
        return response


def sign_in_at_keycloak(authorize_url: str, username: str, password: str) -> str:
    """Submits the login form; returns the callback URL Keycloak redirects to."""
    browser = Browser()
    page = browser.request("GET", authorize_url)
    action = html.unescape(re.search(r'action="([^"]+)"', page.text).group(1))  # type: ignore[union-attr]
    response = browser.request("POST", action, data={"username": username, "password": password})
    assert response.status_code == 302, "Keycloak did not accept the sign-in"
    return response.headers["location"]


def admin_api(base: str) -> httpx.Client:
    token = httpx.post(
        f"{base}/realms/master/protocol/openid-connect/token",
        data={"grant_type": "password", "client_id": "admin-cli", "username": ADMIN[0], "password": ADMIN[1]},
    ).json()["access_token"]
    return httpx.Client(
        base_url=f"{base}/admin/realms/palletwise", headers={"Authorization": f"Bearer {token}"}
    )


@pytest.fixture
def app(
    keycloak: str, database: dict[str, Any], connect_as: Callable[[str], psycopg.Connection]
) -> TestClient:
    # The realm's dev users belong to the demo tenant and site.
    provision_site(connect_as, DEMO_TENANT_ID, DEMO_SITE_ID)
    realm = f"{keycloak}/realms/palletwise"
    oidc = OidcClient(
        OidcSettings(public_url=realm, internal_url=realm, client_secret=SECRETS["KC_BFF_CLIENT_SECRET"]),
        WebSettings(public_origin=ORIGIN),
    )
    sessions = SessionSettings(keys="k1:" + base64.b64encode(os.urandom(32)).decode())
    auth = AuthService(oidc, KeyRing(sessions.keys), sessions, SessionStore(role_pool("PW_APP")))
    return TestClient(create_app(auth, WebSettings(public_origin=ORIGIN)), base_url=ORIGIN)


def start(app: TestClient) -> tuple[str, str]:
    """Starts sign-in at the API; returns the authorize URL and the state."""
    response = app.get("/api/auth/login", follow_redirects=False)
    url = response.headers["location"]
    return url, parse_qs(urlparse(url).query)["state"][0]


def callback(app: TestClient, callback_url: str) -> httpx.Response:
    query = urlparse(callback_url).query
    return app.get(f"/api/auth/callback?{query}", follow_redirects=False)


def sign_in(app: TestClient, username: str = "manager") -> None:
    url, _ = start(app)
    password = SECRETS["KC_DEV_MANAGER_PASSWORD" if username == "manager" else "KC_DEV_ADMIN_PASSWORD"]
    assert callback(app, sign_in_at_keycloak(url, username, password)).headers["location"] == "/"


def claims(token: str) -> dict[str, Any]:
    return jwt.decode(token, options={"verify_signature": False})


# ---------------------------------------------------------------- tests


@pytest.mark.parametrize(("username", "role"), [("manager", "warehouse_manager"), ("admin", "admin")])
def test_realm_users_sign_in_with_their_role_tenant_and_sites(
    app: TestClient, username: str, role: str
) -> None:
    sign_in(app, username)
    me = app.get("/api/me").json()
    assert me["role"] == role and [s["id"] for s in me["sites"]] == [str(DEMO_SITE_ID)]


def test_replayed_code_is_rejected_by_keycloak(app: TestClient) -> None:
    url, _ = start(app)
    callback_url = sign_in_at_keycloak(url, "manager", SECRETS["KC_DEV_MANAGER_PASSWORD"])
    assert callback(app, callback_url).headers["location"] == "/"
    code = parse_qs(urlparse(callback_url).query)["code"][0]
    _, state = start(app)
    replay = app.get(
        f"/api/auth/callback?{urlencode({'state': state, 'code': code})}", follow_redirects=False
    )
    assert replay.headers["location"] == "/?auth_error=failed"


def test_code_without_pkce_verifier_is_rejected(keycloak: str, app: TestClient) -> None:
    url, _ = start(app)
    code = parse_qs(urlparse(sign_in_at_keycloak(url, "manager", SECRETS["KC_DEV_MANAGER_PASSWORD"])).query)[
        "code"
    ][0]
    response = httpx.post(
        f"{keycloak}/realms/palletwise/protocol/openid-connect/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": f"{ORIGIN}/api/auth/callback",
        },
        auth=("palletwise-bff", SECRETS["KC_BFF_CLIENT_SECRET"]),
    )
    assert response.status_code == 400 and response.json()["error"] == "invalid_grant"


def test_users_cannot_change_their_own_tenant_or_sites(keycloak: str) -> None:
    profile = admin_api(keycloak).get("/users/profile").json()
    attributes = {a["name"]: a for a in profile["attributes"]}
    for name in ("pw_tenant", "pw_sites"):
        assert attributes[name]["permissions"] == {"view": ["admin"], "edit": ["admin"]}
    assert profile.get("unmanagedAttributePolicy") is None  # undeclared attributes are dropped


def test_role_removed_in_keycloak_ends_the_session_at_the_next_refresh(
    keycloak: str, app: TestClient, connect_as: Callable[[str], psycopg.Connection]
) -> None:
    admin = admin_api(keycloak)
    username = "revoke-me"
    created = admin.post("/users", json={
        "username": username, "enabled": True, "email": "revoke@palletwise.local", "emailVerified": True,
        "firstName": "Re", "lastName": "Voke",
        "attributes": {"pw_tenant": [str(DEMO_TENANT_ID)], "pw_sites": [str(DEMO_SITE_ID)]},
        "credentials": [{"type": "password", "value": "revoke-pw", "temporary": False}],
    })  # fmt: skip
    assert created.status_code == 201
    user_id = created.headers["location"].rsplit("/", 1)[1]
    bff = admin.get("/clients", params={"clientId": "palletwise-bff"}).json()[0]["id"]
    role = admin.get(f"/clients/{bff}/roles/warehouse_manager").json()
    admin.post(f"/users/{user_id}/role-mappings/clients/{bff}", json=[role])

    url, _ = start(app)
    assert callback(app, sign_in_at_keycloak(url, username, "revoke-pw")).headers["location"] == "/"
    assert app.get("/api/me").status_code == 200

    admin.request("DELETE", f"/users/{user_id}/role-mappings/clients/{bff}", json=[role])
    db = connect_as("postgres")
    db.execute("UPDATE pw.web_session SET refreshed_at = refreshed_at - interval '6 minutes'")
    db.commit()
    assert app.get("/api/me").status_code == 401


def test_copilot_audience_only_in_the_token_exchange(keycloak: str, app: TestClient) -> None:
    """Spec 5: user tokens never carry the copilot audience; the exchange adds it."""
    sign_in(app)
    auth: AuthService = app.app.state.auth  # type: ignore[attr-defined]
    principal = auth.authenticate(app.cookies[SESSION_COOKIE])
    user_token = auth.fresh_access_token(principal)
    assert "palletwise-copilot" not in str(claims(user_token).get("aud"))

    exchanged = httpx.post(
        f"{keycloak}/realms/palletwise/protocol/openid-connect/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "subject_token": user_token,
            "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "audience": "palletwise-copilot",
            "scope": "copilot-audience",
        },
        auth=("palletwise-bff", SECRETS["KC_BFF_CLIENT_SECRET"]),
    )
    assert exchanged.status_code == 200, exchanged.json().get("error_description")
    token = claims(exchanged.json()["access_token"])
    assert token["azp"] == "palletwise-bff" and "palletwise-copilot" in token["aud"]
    assert token["sub"] == principal.identity.subject and "act" not in token
