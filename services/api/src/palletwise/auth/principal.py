"""The signed-in user as the API sees them: role, tenant and sites (identity spec 4)."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast
from uuid import UUID

Role = Literal["warehouse_manager", "admin"]
APP_ROLES: frozenset[str] = frozenset({"warehouse_manager", "admin"})

# Claim names set by the realm's protocol mappers.
ROLES_CLAIM = "pw_roles"
TENANT_CLAIM = "pw_tenant"
SITES_CLAIM = "pw_sites"


class SignInRefused(Exception):
    """The person authenticated but may not use PalletWise. `reason` is shown to them."""

    def __init__(self, reason: Literal["role", "tenant"]) -> None:
        super().__init__(reason)
        self.reason: Literal["role", "tenant"] = reason


@dataclass(frozen=True, slots=True)
class Identity:
    """What the identity provider asserts. Refreshed and revalidated every few minutes."""

    subject: str
    role: Role
    tenant: UUID
    sites: tuple[UUID, ...]
    display_name: str


@dataclass(frozen=True, slots=True)
class Principal:
    """The identity bound to one session. Tenant and sites never come from a request."""

    session_hash: bytes
    identity: Identity

    @property
    def tenant(self) -> UUID:
        return self.identity.tenant

    def may_see_site(self, site_id: UUID) -> bool:
        return site_id in self.identity.sites


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return cast(list[Any], value)
    return [value]


def identity_from_claims(claims: Mapping[str, Any]) -> Identity:
    """Exactly one application role; a valid tenant. Unrelated roles are ignored."""
    roles = {r for r in _as_list(claims.get(ROLES_CLAIM)) if r in APP_ROLES}
    if len(roles) != 1:
        raise SignInRefused("role")
    try:
        tenant = UUID(str(claims[TENANT_CLAIM]))
        sites = tuple(dict.fromkeys(UUID(str(s)) for s in _as_list(claims.get(SITES_CLAIM))))
    except (KeyError, ValueError) as exc:
        raise SignInRefused("tenant") from exc
    name = claims.get("name") or claims.get("preferred_username") or "User"
    role: Role = "admin" if "admin" in roles else "warehouse_manager"
    return Identity(str(claims["sub"]), role, tenant, sites, str(name))
