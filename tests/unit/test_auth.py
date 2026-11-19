"""Identity spec 8: token validation, role mapping, return paths and session encryption."""

import base64
import os
import time
from typing import Any
from uuid import uuid4

import jwt
import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric import rsa

from palletwise.auth.crypto import KeyRing, KeyRingError, Sealed, pkce_challenge
from palletwise.auth.oidc import TokenInvalid, validate_id_token
from palletwise.auth.principal import SignInRefused, identity_from_claims
from palletwise.auth.service import safe_return_path

ISSUER = "http://localhost:8180/realms/palletwise"
CLIENT = "palletwise-bff"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def b64key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def id_token(key: Any = KEY, alg: str = "RS256", **overrides: Any) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER, "aud": CLIENT, "azp": CLIENT, "sub": "user-1",
        "iat": now, "exp": now + 300, "nonce": "n-1",
    }  # fmt: skip
    claims.update(overrides)
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, key, algorithm=alg)


def validate(token: str, nonce: str | None = "n-1") -> dict[str, Any]:
    return validate_id_token(
        token, signing_key=lambda _: KEY.public_key(), issuer=ISSUER, client_id=CLIENT, nonce=nonce
    )


# ---------------------------------------------------------------- ID token validation


def test_valid_id_token_is_accepted() -> None:
    assert validate(id_token())["sub"] == "user-1"


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(id_token(nonce="other"), id="wrong nonce"),
        pytest.param(id_token(nonce=None), id="missing nonce"),
        pytest.param(id_token(aud="palletwise-copilot"), id="wrong audience"),
        pytest.param(id_token(iss="http://evil.example/realms/palletwise"), id="wrong issuer"),
        pytest.param(id_token(azp="another-client"), id="wrong authorized party"),
        pytest.param(id_token(exp=int(time.time()) - 3600), id="expired"),
        pytest.param(id_token(iat=None), id="missing iat"),
        pytest.param(id_token(key=OTHER_KEY), id="bad signature"),
        pytest.param(id_token(key="a-shared-secret-of-at-least-32-bytes!", alg="HS256"), id="HS256"),
        pytest.param(
            jwt.encode({"iss": ISSUER, "aud": CLIENT, "sub": "x"}, None, algorithm="none"), id="alg none"
        ),
    ],
)
def test_invalid_id_token_is_rejected(token: str) -> None:
    with pytest.raises(TokenInvalid):
        validate(token)


def test_refreshed_id_token_needs_no_nonce() -> None:
    assert validate(id_token(nonce=None), nonce=None)["sub"] == "user-1"


# ---------------------------------------------------------------- roles, tenant, sites


def claims(**extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"sub": "u", "pw_tenant": str(uuid4()), "pw_sites": [str(uuid4())]}
    base.update(extra)
    return base


@pytest.mark.parametrize(
    ("roles", "expected"),
    [
        (["warehouse_manager"], "warehouse_manager"),
        (["admin"], "admin"),
        (["admin", "offline_access", "uma_authorization"], "admin"),  # unrelated roles ignored
        ("warehouse_manager", "warehouse_manager"),  # single-valued claim
    ],
)
def test_exactly_one_application_role(roles: Any, expected: str) -> None:
    assert identity_from_claims(claims(pw_roles=roles)).role == expected


@pytest.mark.parametrize(
    "roles",
    [[], None, ["offline_access"], ["warehouse_manager", "admin"]],
    ids=["empty", "absent", "unrelated", "both"],
)
def test_no_or_both_application_roles_are_refused(roles: Any) -> None:
    with pytest.raises(SignInRefused) as refused:
        identity_from_claims(claims(pw_roles=roles))
    assert refused.value.reason == "role"


@pytest.mark.parametrize("tenant", [None, "", "not-a-uuid"])
def test_missing_or_invalid_tenant_is_refused(tenant: Any) -> None:
    with pytest.raises(SignInRefused) as refused:
        identity_from_claims(claims(pw_roles=["admin"], pw_tenant=tenant))
    assert refused.value.reason == "tenant"


def test_sites_are_parsed_and_an_empty_list_means_no_access() -> None:
    site = uuid4()
    assert identity_from_claims(claims(pw_roles=["admin"], pw_sites=str(site))).sites == (site,)
    assert identity_from_claims(claims(pw_roles=["admin"], pw_sites=[])).sites == ()
    assert identity_from_claims(claims(pw_roles=["admin"], pw_sites=None)).sites == ()


# ---------------------------------------------------------------- return paths (no open redirect)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/", "/"),
        ("/inventory?sku=SKU-102", "/inventory?sku=SKU-102"),
        (None, "/"),
        ("", "/"),
        ("https://evil.example/", "/"),
        ("//evil.example/", "/"),
        ("/\\evil.example", "/"),
        ("/a\\b", "/"),
        ("/x\r\nSet-Cookie: a=b", "/"),
        ("evil", "/"),
        ("/api/auth/login", "/"),
        ("/" + "a" * 600, "/"),
    ],
)
def test_return_path_is_same_origin_only(value: str | None, expected: str) -> None:
    assert safe_return_path(value) == expected


# ---------------------------------------------------------------- PKCE and refresh token encryption


def test_pkce_s256_matches_rfc_7636_example() -> None:
    assert (
        pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
        == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    )


def test_key_ring_round_trip_and_rotation() -> None:
    old, new = b64key(), b64key()
    sealed = KeyRing(f"k1:{old}").seal("refresh-token", b"session-a")
    assert sealed.key_id == "k1" and b"refresh-token" not in sealed.ciphertext
    rotated = KeyRing(f"k2:{new},k1:{old}")
    assert rotated.open(sealed, b"session-a") == "refresh-token"
    assert rotated.seal("x", b"s").key_id == "k2"


def test_ciphertext_is_bound_to_its_session() -> None:
    ring = KeyRing(f"k1:{b64key()}")
    sealed = ring.seal("refresh-token", b"session-a")
    with pytest.raises(InvalidTag):
        ring.open(sealed, b"session-b")
    tampered = Sealed(sealed.ciphertext[:-1] + bytes([sealed.ciphertext[-1] ^ 1]), "k1")
    with pytest.raises(InvalidTag):
        ring.open(tampered, b"session-a")


def test_retired_key_is_reported() -> None:
    sealed = KeyRing(f"k1:{b64key()}").seal("t", b"s")
    with pytest.raises(KeyRingError):
        KeyRing(f"k2:{b64key()}").open(sealed, b"s")


@pytest.mark.parametrize(
    "spec", ["", "k1", "k1:not-base64!", f"k1:{base64.b64encode(b'short').decode()}", "k1:{0},k1:{0}"]
)
def test_invalid_key_ring_is_refused(spec: str) -> None:
    with pytest.raises(KeyRingError):
        KeyRing(spec.format(b64key()))
