"""Sign-in, session lookup, revalidation and sign-out (identity spec sections 2 and 3).

Secrets handled here (state, code, tokens, session ids, keys) are never logged; failures are
logged by reason only.
"""

import hmac
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal
from uuid import UUID

from palletwise.auth.crypto import KeyRing, Sealed, pkce_challenge, random_token, token_hash
from palletwise.auth.oidc import (
    IdentityProviderUnavailable,
    OidcClient,
    TokenInvalid,
    TokenRejected,
    TokenSet,
)
from palletwise.auth.principal import Identity, Principal, Role, SignInRefused, identity_from_claims
from palletwise.db.audit_repo import AuditEntry, AuditLog
from palletwise.db.session_repo import SessionRecord, SessionRow, SessionStore, TenantNotProvisioned
from palletwise.settings import SessionSettings
from palletwise.telemetry.tracing import current_trace_id

log = logging.getLogger(__name__)

LoginFailure = Literal["state", "failed", "role", "tenant", "unavailable"]
MAX_RETURN_PATH = 512


class LoginFailed(Exception):
    def __init__(self, reason: LoginFailure) -> None:
        super().__init__(reason)
        self.reason: LoginFailure = reason


class DelegationRefused(Exception):
    """The identity provider would not let the copilot act for this user; the run is denied."""


@dataclass(frozen=True, slots=True)
class Delegation:
    subject: str
    client: str


class SessionEnded(Exception):
    """The session no longer exists or lost its access; the caller answers 401."""


def safe_return_path(value: str | None) -> str:
    """A same-origin path only: no scheme, no host, no protocol-relative or backslash tricks."""
    if (
        not value
        or len(value) > MAX_RETURN_PATH
        or not value.startswith("/")
        or value.startswith(("//", "/\\"))
        or any(c in value for c in "\\\r\n\t")
        or value.startswith("/api/auth/")
    ):
        return "/"
    return value


def _login_failure(exc: Exception) -> LoginFailed:
    if isinstance(exc, LoginFailed):
        return exc
    if isinstance(exc, SignInRefused):
        return LoginFailed(exc.reason)
    if isinstance(exc, IdentityProviderUnavailable):
        return LoginFailed("unavailable")
    log.warning("ID token rejected: %s", exc)
    return LoginFailed("failed")


def _identity(row: SessionRow) -> Identity:
    role: Role = "admin" if row.role == "admin" else "warehouse_manager"
    return Identity(row.subject, role, row.tenant, row.sites, row.display_name)


@dataclass(frozen=True, slots=True)
class LoginStart:
    state: str
    redirect_url: str


@dataclass(frozen=True, slots=True)
class LoginResult:
    session_id: str
    return_to: str


class AuthService:
    def __init__(
        self, oidc: OidcClient, keys: KeyRing, settings: SessionSettings, store: SessionStore, audit: AuditLog
    ) -> None:
        self._oidc = oidc
        self._keys = keys
        self._store = store
        self._audit = audit
        self.settings = settings
        self._idle = timedelta(minutes=settings.idle_minutes)
        self._absolute = timedelta(hours=settings.absolute_hours)
        self._revalidate = timedelta(seconds=settings.revalidate_seconds)
        self._login_ttl = timedelta(minutes=settings.login_ttl_minutes)

    # ------------------------------------------------------------ sign-in

    def start_login(self, return_to: str | None) -> LoginStart:
        state, nonce, verifier = random_token(), random_token(), random_token()
        self._store.create_login(
            token_hash(state), nonce, verifier, safe_return_path(return_to), self._login_ttl
        )
        url = self._oidc.authorize_url(state=state, nonce=nonce, code_challenge=pkce_challenge(verifier))
        return LoginStart(state, url)

    def finish_login(self, *, state: str, bound_state: str | None, code: str) -> LoginResult:
        # The state must match the one bound to this browser by the login cookie...
        if not bound_state or not hmac.compare_digest(state.encode(), bound_state.encode()):
            raise LoginFailed("state")
        # ...and an unexpired, unused attempt, which is marked used in the same statement.
        attempt = self._store.consume_login(token_hash(state))
        if attempt is None:
            raise LoginFailed("state")

        try:
            tokens = self._oidc.exchange_code(code, attempt.code_verifier)
        except TokenRejected as exc:
            log.info("sign-in rejected by the identity provider: %s", exc)
            raise LoginFailed("failed") from exc
        except IdentityProviderUnavailable as exc:
            raise LoginFailed("unavailable") from exc

        try:
            identity = identity_from_claims(self._oidc.validate(tokens.id_token, nonce=attempt.nonce))
            session_id = random_token()
            self._create_session(session_id, identity, tokens)
        except (TokenInvalid, SignInRefused, LoginFailed, IdentityProviderUnavailable) as exc:
            # Do not leave a signed-in identity provider session behind a refused sign-in.
            self._oidc.logout(tokens.refresh_token)
            raise _login_failure(exc) from exc
        # Sign-in is an audited action (NFR-AUD-03): no audit entry, no session.
        try:
            self._audit_session_event(identity.tenant, "auth.sign_in", "OK", identity.subject, identity.role)
        except Exception as exc:
            log.exception("sign-in audit failed; refusing the sign-in")
            self._store.end(token_hash(session_id))
            self._oidc.logout(tokens.refresh_token)
            raise LoginFailed("unavailable") from exc
        return LoginResult(session_id, attempt.return_to)

    def _audit_session_event(
        self, tenant: UUID, action: str, outcome: str, subject: str, role: str | None
    ) -> None:
        self._audit.append(
            tenant,
            AuditEntry(actor_type="USER", action=action, outcome=outcome, trace_id=current_trace_id(),
                       user_subject=subject, user_role=role),
        )  # fmt: skip

    def _create_session(self, session_id: str, identity: Identity, tokens: TokenSet) -> None:
        id_hash = token_hash(session_id)
        try:
            self._store.create_session(id_hash, self._record(identity, tokens, id_hash), self._absolute)
        except TenantNotProvisioned as exc:
            raise LoginFailed("tenant") from exc

    def _record(self, identity: Identity, tokens: TokenSet, id_hash: bytes) -> SessionRecord:
        sealed = self._keys.seal(tokens.refresh_token, associated=id_hash)
        return SessionRecord(
            identity.subject,
            identity.role,
            identity.tenant,
            identity.sites,
            identity.display_name,
            sealed.ciphertext,
            sealed.key_id,
        )

    # ------------------------------------------------------------ every request

    def authenticate(self, session_id: str | None) -> Principal:
        """The principal for a session cookie, revalidated when due. Raises SessionEnded."""
        if not session_id:
            raise SessionEnded
        id_hash = token_hash(session_id)
        row = self._store.touch(id_hash, self._idle, self._revalidate)
        if row is None:
            raise SessionEnded
        if row.refresh_due:
            return self._refresh(id_hash, force=False)[0]
        return Principal(id_hash, _identity(row))

    def delegate_to_copilot(self, principal: Principal) -> Delegation:
        """Fresh user token by refresh, then token exchange (identity spec 5). Any failure denies.

        Keycloak issues no `act` claim (identity spec 5, delegation claim), so the acting agent is
        recorded by the caller from the registry; here the exchanged token proves that this
        client obtained a copilot token for this same user.
        """
        try:
            exchanged = self._oidc.exchange_for_copilot(self.fresh_access_token(principal))
            claims = self._oidc.validate_copilot_token(exchanged, subject=principal.identity.subject)
        except (TokenRejected, TokenInvalid) as exc:
            log.warning("copilot delegation refused: %s", exc)
            raise DelegationRefused from exc
        return Delegation(subject=str(claims["sub"]), client=str(claims["azp"]))

    def fresh_access_token(self, principal: Principal) -> str:
        """A new user access token by server-side refresh, e.g. before token exchange."""
        _, access_token = self._refresh(principal.session_hash, force=True)
        if access_token is None:
            raise SessionEnded
        return access_token

    def _refresh(self, id_hash: bytes, *, force: bool) -> tuple[Principal, str | None]:
        """Serialized per session by the row lock held for the whole refresh.

        Ending a session is committed before SessionEnded is raised, so the row is really gone.
        If the identity provider is unreachable the transaction rolls back and the session is
        kept, but the request fails: access is never granted without revalidation.
        """
        ended_because: str | None = None
        ended_owner: tuple[UUID, str] | None = None  # tenant and subject, for the audit entry
        result: tuple[Principal, str | None] | None = None
        with self._store.locked(id_hash, self._revalidate) as locked:
            if locked is None:
                ended_because = "missing"
            elif not force and not locked.refresh_due:
                # Another request refreshed while this one waited for the lock.
                row = locked.touch(self._idle, self._revalidate)
                if row is None:
                    ended_because = "expired"
                else:
                    result = Principal(id_hash, _identity(row)), None
            else:
                refresh_token = self._keys.open(Sealed(locked.refresh_token, locked.key_id), id_hash)
                try:
                    tokens = self._oidc.refresh(refresh_token)
                    claims = self._oidc.validate(tokens.id_token, nonce=None)
                    if claims["sub"] != locked.subject:
                        raise TokenInvalid("sub")
                    identity = identity_from_claims(claims)
                except (TokenRejected, TokenInvalid, SignInRefused) as exc:
                    # Disabled account, revoked session, lost role or broken claims.
                    ended_because = f"{type(exc).__name__}: {exc}"  # a reason label, never a token
                    locked.end()
                    ended_owner = (locked.tenant, locked.subject)
                else:
                    record = self._record(identity, tokens, id_hash)
                    if locked.store_refresh(record):
                        result = Principal(id_hash, identity), tokens.access_token
                    else:
                        ended_because = "tenant changed"
                        ended_owner = (locked.tenant, locked.subject)
        if result is None:
            log.info("session ended at revalidation: %s", ended_because)
            if ended_owner is not None:
                self._audit_quietly(
                    ended_owner[0],
                    "auth.session_ended",
                    f"REVALIDATION_{ended_because}",
                    ended_owner[1],
                    None,
                )
            raise SessionEnded
        return result

    # ------------------------------------------------------------ sign-out

    def logout(self, session_id: str | None) -> None:
        if not session_id:
            return
        id_hash = token_hash(session_id)
        ended = self._store.end(id_hash)
        if ended is None:
            return
        self._oidc.logout(self._keys.open(Sealed(ended.refresh_token, ended.key_id), id_hash))
        self._audit_quietly(ended.tenant, "auth.sign_out", "OK", ended.subject, ended.role)

    def _audit_quietly(self, tenant: UUID, action: str, outcome: str, subject: str, role: str | None) -> None:
        """For events where access is being removed: a failed audit must not keep it alive."""
        try:
            self._audit_session_event(tenant, action, outcome, subject, role)
        except Exception:
            log.exception("audit failed for %s", action)
