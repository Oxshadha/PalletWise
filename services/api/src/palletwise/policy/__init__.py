"""OPA policy decisions (identity spec 6). Any failure to decide is a deny."""

import logging
from dataclasses import dataclass, field
from typing import Any, cast

import httpx

from palletwise.settings import PolicySettings

log = logging.getLogger(__name__)

DECISION_PATH = "/v1/data/palletwise/authz/decision"
UNAVAILABLE = "policy_unavailable"


@dataclass(frozen=True, slots=True)
class PolicyUser:
    sub: str
    role: str
    tenant: str
    sites: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PolicyAgent:
    id: str
    version: str
    status: str
    scopes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PolicyRequest:
    """Built by the server from the session, the registry and the resolved resource."""

    user: PolicyUser
    action: str
    scope: str
    resource_tenant: str
    resource_site: str
    agent: PolicyAgent | None = None

    def as_input(self) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "user": {"sub": self.user.sub, "role": self.user.role, "tenant": self.user.tenant,
                     "sites": list(self.user.sites)},
            "action": {"name": self.action, "scope": self.scope},
            "resource": {"tenant": self.resource_tenant, "site": self.resource_site},
        }  # fmt: skip
        if self.agent is not None:
            doc["agent"] = {"id": self.agent.id, "version": self.agent.version,
                            "status": self.agent.status, "scopes": list(self.agent.scopes)}  # fmt: skip
        return doc


@dataclass(frozen=True, slots=True)
class Decision:
    allow: bool
    reasons: tuple[str, ...] = field(default=())


class PolicyClient:
    def __init__(self, settings: PolicySettings, *, transport: httpx.BaseTransport | None = None) -> None:
        self._http = httpx.Client(
            base_url=settings.url, timeout=settings.timeout_seconds, transport=transport
        )

    def decide(self, request: PolicyRequest) -> Decision:
        try:
            response = self._http.post(DECISION_PATH, json={"input": request.as_input()})
            response.raise_for_status()
            body: Any = response.json()
        except (httpx.HTTPError, ValueError):
            log.warning("policy decision unavailable; denying")
            return Decision(False, (UNAVAILABLE,))
        return parse_decision(body)

    def close(self) -> None:
        self._http.close()


def parse_decision(body: Any) -> Decision:
    """Only an explicit boolean true allows; a missing or malformed result denies."""
    payload = cast(dict[str, Any], body) if isinstance(body, dict) else {}
    result = payload.get("result")
    if not isinstance(result, dict):
        return Decision(False, (UNAVAILABLE,))
    typed = cast(dict[str, Any], result)
    if typed.get("allow") is True:
        return Decision(True)
    reasons = typed.get("reasons")
    if isinstance(reasons, list) and reasons and all(isinstance(r, str) for r in cast(list[Any], reasons)):
        return Decision(False, tuple(cast(list[str], reasons)))
    return Decision(False, (UNAVAILABLE,))
